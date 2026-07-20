"""Configurable LLM-as-judge engine (single-score paradigm, post-2026-05-28).

Mirrors Langfuse 的评估器配置：每个评估器只产出 **一个** 命名分数，分数类型为
``numeric`` / ``boolean`` / ``categorical`` 三选一。Prompt 拆成 3 段：

* ``evaluation_prompt`` —— 主任务说明 + 待评样本，使用 Mustache 占位符
  ``{{varname}}``。变量名由 ``variable_mapping`` 决定来源（见下）
* ``reasoning_prompt`` —— 让模型先打"理由"（多用作 chain-of-thought 引导）
* ``output_prompt`` —— 强约束输出格式，例如 "只输出 JSON 对象 {score, reasoning}"

落到 LLM 的消息结构：

    system: <reasoning_prompt>\n\n<output_prompt>
    user:   <evaluation_prompt>（占位符已替换）

变量映射（variable_mapping）
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1:1 对齐 Langfuse 的两段式：模板里写 ``{{Query}}`` / ``{{Generation}}``
等任意名字（**大小写敏感**），运行时按 mapping 查找数据源：

    {
      "evaluation_prompt": "Question: {{Query}}\\nAnswer: {{Generation}}",
      "variable_mapping": {
        "Query": "input",
        "Generation": "output",
        "GroundTruth": "expected_output",
        "Foo": "metadata.foo",       # metadata 子字段（点路径）
      }
    }

数据源取值 ``input`` / ``output`` / ``expected_output`` / ``metadata`` /
``metadata.<key>``（点路径递归取子字段）。模板里出现的 ``{{name}}`` 在
mapping 里没配会报错——保存前 UI 会拦截，运行时也不再 silent 渲染成空字符串。

兼容期：旧的单大括号 ``{input}`` / ``{output}`` / ``{expected_output}`` /
``{metadata}`` 仍可识别（仅模板里没有任何 ``{{...}}`` 时启用），方便老
评估器配置不需要立即手工迁移。

Why 拆 3 段（而不是单 system_prompt）？
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Langfuse 这样拆的好处：编辑器可以独立默认填充 reasoning / output 两段
样板，用户改 evaluation_prompt 不会破坏输出契约。重构旧 dimensions 模式
时一并采纳。

输出 schema
-----------

模型必须返回一个 JSON 对象（也允许 ```json ... ``` 围栏）。字段名固定：

    {"score": <number|bool|string>, "reasoning": "<short reason>"}

* numeric  → ``score`` 解析为 float，按 ``score_range=[min,max]`` 归一到 [0,1]
* boolean  → ``score`` 解析为 bool / 0/1，归一到 0.0 或 1.0
* categorical → ``score`` 解析为 string；与 ``categories=[{label,value}]``
                配对得到归一值（其中 value 必须是 [0,1] 浮点）

无法解析时 ``ConfigurableJudgeResult.error`` 非空，``score`` 为 None。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from agent_eval.db_models.tables import EvaluatorProviderRow
from agent_eval.evaluation.judge_clients import (
    JudgeClientError,
    JudgeUsage,
    build_judge_client,
)

logger = logging.getLogger(__name__)

# 完整性补评：单次 judge 调用未出分（网络耗尽 / 流截断 / 不可解析）时，以
# 升级超时有界重跑，力求每个评估维度都拿到分。穷尽仍失败则如实返回 error，
# 绝不伪造分数（与「judge 挂了不静默 skip、不让幸存维度独自判 pass」一致）。
_JUDGE_COMPLETENESS_ATTEMPTS = 3


# ────────────────────────────────────────────────────────────────────────
# Defaults — 与 Langfuse 模板默认值对齐
# ────────────────────────────────────────────────────────────────────────

DEFAULT_EVALUATION_PROMPT = """请评估下面 AI 助手的回答质量。

## 用户输入
{{Query}}

## AI 回答
{{Generation}}

## 期望答案（如有）
{{GroundTruth}}

## 评判要点（如有，逐条核对回答是否满足）
{{Criteria}}

请给出一个 0 到 1 之间的总分（0=完全错误，1=完美）。"""

DEFAULT_REASONING_PROMPT = """你是一个严谨、客观的评估专家。
请先简短地写出评分理由（2-3 句话），再给出分数。
理由要可复核，避免空泛的"很好/不错"。"""

DEFAULT_OUTPUT_PROMPT = """严格只输出以下 JSON，不要附加任何其他文字、Markdown 或代码围栏：

{"score": <数值或布尔或类别字符串>, "reasoning": "<简短理由>"}"""


# checklist 评分类型专用的 system 段。value 由后端按通过率机械计算，模型
# 只需对每个检查项判 pass/fail/na 并给出可复核的证据（引用作答原文/规则依据），
# 不自行给总分、不做算术。
CHECKLIST_REASONING_PROMPT = """你是一个严谨、客观的评估执行器。下面会给出一组**编号的二元检查项**。
你的唯一任务：对每一个检查项，仅依据给定的「AI 回答」内容判定它是 pass（满足）
还是 fail（不满足）；若该检查项在本样例下不适用，判 na。

严格要求：
- 只依据给定内容判定，不臆测未提供的信息，不使用检查项以外的任何自定标准。
- 每一项都要给出 evidence：引用 AI 回答里的相关原文片段，或说明依据哪条规则判 fail。
- 不要自己计算总分、不要输出分数——总分由系统按你的逐项判定自动计算。"""

CHECKLIST_OUTPUT_PROMPT = """严格只输出以下 JSON，不要附加任何其他文字、Markdown 或代码围栏：

{"checks": [{"id": "<检查项编号，如 F1>", "verdict": "pass|fail|na", "evidence": "<引用作答原文或判定依据，简短>"}], "reasoning": "<一句话总述>"}

必须为上面列出的每一个检查项输出一条对应记录（含数据补充的检查项），verdict 只能是 pass / fail / na 三者之一。"""


# 与 DEFAULT_EVALUATION_PROMPT 配对的默认 mapping。前端新建评估器时一并塞入。
DEFAULT_VARIABLE_MAPPING: dict[str, str] = {
    "Query": "input",
    "Generation": "output",
    "GroundTruth": "expected_output",
    # 多轮逐轮打分时，score_conversation 把该轮 criteria 注入 metadata.turn_criteria；
    # 单轮场景无此 key → 渲染空字符串，无副作用。
    "Criteria": "metadata.turn_criteria",
}


# numeric 默认范围；可被 params['score_range'] 覆盖
DEFAULT_NUMERIC_RANGE: tuple[float, float] = (0.0, 1.0)


# ────────────────────────────────────────────────────────────────────────
# Result types
# ────────────────────────────────────────────────────────────────────────


@dataclass
class JudgeScore:
    """单一命名分数；value 已归一到 [0.0, 1.0]。

    name 字段保留是为了让上层 (langfuse_runner) 能用 evaluator label 当 key
    入库——本模块自身只会产出最多一个 JudgeScore。raw_value 保留模型的
    原始输出（数值/布尔/类别名），用于 dry-run UI 展示。

    checklist 评分类型下，``checks`` 携带逐项 pass/fail/na + 证据的明细，
    value 由后端按 ``通过数 / (通过数 + 失败数)`` **机械算出**（模型只判二元
    verdict，算术不经模型）。其它 score_type 时 ``checks`` 为 None。
    """
    name: str
    value: float
    reason: str = ""
    raw_value: Any = None
    checks: list[dict[str, Any]] | None = None


@dataclass
class ConfigurableJudgeResult:
    """单次 configurable judge 调用的输出。

    ``error`` 非空时 ``scores`` 为空。``raw_response`` 保留原始响应对象，
    ``raw_content`` 保留 assistant message 文本，dry-run UI 都会展示。
    """
    scores: list[JudgeScore] = field(default_factory=list)
    usage: JudgeUsage = field(default_factory=JudgeUsage)
    model: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)
    raw_content: str = ""
    rendered_messages: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None


# ────────────────────────────────────────────────────────────────────────
# Template rendering
# ────────────────────────────────────────────────────────────────────────


# Mustache 占位符 ``{{Name}}``。变量名允许字母 / 数字 / 下划线，**大小写敏感**。
# 名字两侧允许少量空白（``{{ Query }}``）以兼容用户从 Langfuse UI 复制粘贴。
_MUSTACHE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

# 兼容旧单大括号占位符——只在模板里没有任何 ``{{...}}`` 时启用。
_LEGACY_RE = re.compile(r"\{(input|output|expected_output|metadata)\}")
_LEGACY_KEYS = {"input", "output", "expected_output", "metadata"}


class TemplateRenderError(ValueError):
    """变量名出现在模板里但 ``variable_mapping`` 没配置数据源。

    保存阶段（dry-run / runtime 落库前）会被 UI 拦截，运行时再次抛出
    则进入 ``ConfigurableJudgeResult.error``——比 silent 渲染成空字符串
    更便于排查。
    """


def _resolve_source(
    spec: str,
    *,
    input_text: str,
    output_text: str,
    expected_output: str,
    metadata: dict[str, Any] | None,
) -> str:
    """根据 ``variable_mapping`` 的取值表达式（如 ``"input"`` /
    ``"metadata.foo.bar"``）从样本里挑出字符串。

    * ``input`` / ``output`` / ``expected_output`` —— 直接取
    * ``metadata`` —— 整体 JSON 序列化
    * ``metadata.<key>[.<sub>...]`` —— metadata 字典里点路径取子字段，
      最终值非字符串时 ``str(...)``；缺失返回空字符串
    """
    s = (spec or "").strip()
    if s == "input":
        return input_text or ""
    if s == "output":
        return output_text or ""
    if s == "expected_output":
        return expected_output or ""
    if s == "metadata":
        return json.dumps(metadata or {}, ensure_ascii=False)
    if s.startswith("metadata."):
        cur: Any = metadata or {}
        for key in s.split(".")[1:]:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return ""
        if cur is None:
            return ""
        return cur if isinstance(cur, str) else json.dumps(cur, ensure_ascii=False)
    # 未识别的 source 表达式——按"未配置"处理，让上层拦截
    raise TemplateRenderError(f"unknown source expression: {spec!r}")


def _render(
    template: str,
    *,
    variable_mapping: dict[str, str] | None,
    input_text: str,
    output_text: str,
    expected_output: str,
    metadata: dict[str, Any] | None,
) -> str:
    """把模板里的 ``{{Name}}`` / ``{Name}``（旧式）换成实际样本字符串。

    新模板：从 ``variable_mapping`` 查 ``Name → source``，再调
    ``_resolve_source(source, ...)``。``Name`` 大小写敏感；查不到时抛
    ``TemplateRenderError``。

    旧模板（仅当模板里没有任何 ``{{...}}`` 时）：直接按内置 4 个 key
    渲染——保持兼容已存的评估器配置。
    """
    if not template:
        return ""

    has_mustache = bool(_MUSTACHE_RE.search(template))
    if has_mustache:
        mapping = variable_mapping or {}

        def _sub(match: re.Match[str]) -> str:
            name = match.group(1)
            source = mapping.get(name)
            if source is None:
                raise TemplateRenderError(
                    f"variable {{{{{name}}}}} appears in prompt but is not in variable_mapping"
                )
            return _resolve_source(
                source,
                input_text=input_text,
                output_text=output_text,
                expected_output=expected_output,
                metadata=metadata,
            )

        return _MUSTACHE_RE.sub(_sub, template)

    # legacy 单大括号 —— 只识别 4 个内置 key，其它 ``{...}`` 原样保留
    # （典型场景：用户在 evaluation_prompt 里粘 JSON 示例）。
    def _legacy_sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in _LEGACY_KEYS:
            return match.group(0)
        return _resolve_source(
            key,
            input_text=input_text,
            output_text=output_text,
            expected_output=expected_output,
            metadata=metadata,
        )

    return _LEGACY_RE.sub(_legacy_sub, template)


def _build_messages(
    *,
    params: dict[str, Any],
    input_text: str,
    output_text: str,
    expected_output: str,
    metadata: dict[str, Any] | None,
) -> list[dict[str, str]]:
    # checklist 类型缺省用 checklist 专用 reasoning/output 模板（要求逐项给
    # verdict+证据、只输出 checks 数组），避免落回"吐单个 score"的旧契约。
    is_checklist = (params.get("score_type") or "").lower() == "checklist"
    evaluation_prompt = params.get("evaluation_prompt") or DEFAULT_EVALUATION_PROMPT
    reasoning_prompt = params.get("reasoning_prompt") or (
        CHECKLIST_REASONING_PROMPT if is_checklist else DEFAULT_REASONING_PROMPT
    )
    output_prompt = params.get("output_prompt") or (
        CHECKLIST_OUTPUT_PROMPT if is_checklist else DEFAULT_OUTPUT_PROMPT
    )

    raw_mapping = params.get("variable_mapping")
    if isinstance(raw_mapping, dict):
        variable_mapping = {str(k): str(v) for k, v in raw_mapping.items()}
    elif evaluation_prompt == DEFAULT_EVALUATION_PROMPT:
        # params 没显式给 mapping，但用的是默认模板——补默认 mapping
        # 让老调用方（不传 variable_mapping）继续可用
        variable_mapping = dict(DEFAULT_VARIABLE_MAPPING)
    else:
        variable_mapping = {}

    user = _render(
        evaluation_prompt,
        variable_mapping=variable_mapping,
        input_text=input_text,
        output_text=output_text,
        expected_output=expected_output if expected_output else "（未提供）",
        metadata=metadata,
    )
    system = f"{reasoning_prompt}\n\n{output_prompt}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ────────────────────────────────────────────────────────────────────────
# Response parsing
# ────────────────────────────────────────────────────────────────────────


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


# agent-as-judge 兜底：SSE 业务 agent 往往无视"只输出 JSON"的判决契约，
# 直接吐一段中文散文（有时含"评分：0.8""得分 85 分""score: 0.9"之类）。
# JSON 抽取失败时，退而从散文里正则捞一个数值分作总分——只在 numeric/boolean
# 场景启用（categorical/checklist 无法从散文可靠还原），且仅对 agent 类型
# provider 生效（不放松 LLM judge 的严格 JSON 契约，避免把散文里的无关数字误当分）。
#
# 识别的写法（大小写不敏感，中英冒号/空格兼容）。两条互补 pattern，取更靠前的命中：
#
# 1) 关键词引导式（keyword → number）：允许关键词与数字间隔少量非数字字符
#    （"我给它打 8/10 分" 的"它"、"满意度 90%" 的空格）。分子后可跟 %/分/比分。
#      score / rating / 分数 / 得分 / 评分 / 总分 / 打分 / 打…分 / 满意度 / 准确率 / 符合度
#      后跟  0.85 | 85% | 85分 | 8/10 | 3/100
# 2) 独立比分式（number/number）：无关键词也能捞 "8/10"、"85/100"——业务 agent
#    常直接写比分。分母 (2..1000) 限定，避免把日期/无关分数误当分。
_PROSE_SCORE_RE = re.compile(
    r"(?:score|rating|分数|得分|评分|总分|打分|打|满意度|准确率|符合度|符合率)"
    r"[^\d]{0,4}?"
    r"(\d+(?:\.\d+)?)\s*(%|分|/\s*(\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)
_PROSE_RATIO_RE = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d{1,4}(?:\.\d+)?)")


def _salvage_prose_score(text: str, *, score_type: str) -> tuple[float, str] | None:
    """从自然语言里捞一个 [0,1] 归一分。命中返回 (value, matched_snippet)，
    否则 None。仅供 agent-as-judge 兜底调用。

    归一规则：
      * ``85%`` / ``85分`` / 裸 ``85``（>1 且 <=100）→ /100
      * ``8/10`` / ``8/100`` → 按分母归一
      * ``0.85``（0~1）→ 原样
    boolean 场景把归一分二值化（>=0.5→1.0，否则 0.0）。
    """
    if not text:
        return None

    value: float | None = None
    snippet = ""
    m = _PROSE_SCORE_RE.search(text)
    if m:
        try:
            num = float(m.group(1))
        except (TypeError, ValueError):
            num = None
        if num is not None:
            unit = (m.group(2) or "").strip()
            denom_group = m.group(3)
            if denom_group:  # "8/10" / "8/100" 形式
                try:
                    denom = float(denom_group)
                    value = num / denom if denom > 0 else 0.0
                    snippet = m.group(0).strip()
                except (TypeError, ValueError):
                    value = None
            elif unit == "%":
                value = num / 100.0
                snippet = m.group(0).strip()
            elif unit == "分":  # "85 分" → 百分制；"0.85 分" 原样
                value = num / 100.0 if num > 1 else num
                snippet = m.group(0).strip()
            elif num <= 1.0:
                value = num
                snippet = m.group(0).strip()
            elif num <= 100.0:
                value = num / 100.0  # 裸的 2..100 视作百分制
                snippet = m.group(0).strip()

    # 关键词式没捞到，退到独立比分式（"8/10" 等）。
    if value is None:
        rm = _PROSE_RATIO_RE.search(text)
        if rm:
            try:
                a, b = float(rm.group(1)), float(rm.group(2))
                if b > 0 and a <= b:
                    value = a / b
                    snippet = rm.group(0).strip()
            except (TypeError, ValueError):
                value = None

    if value is None:
        return None

    value = max(0.0, min(1.0, value))
    if (score_type or "numeric").lower() == "boolean":
        value = 1.0 if value >= 0.5 else 0.0
    return value, snippet


def _diagnose_unparseable(invocation: Any) -> str:
    """When _extract_json returned None, give the user a useful explanation
    instead of a generic "not parseable JSON". The most common cause we see
    is that the judge's response was cut off at max_tokens before it ever
    got to emit the JSON object — usually because the model insists on a
    chain-of-thought first (mimo, DeepSeek-R1, etc.). Detect that via the
    provider's finish_reason / stop_reason field and report it explicitly
    so the caller can raise max_tokens."""
    body = invocation.raw_response or {}
    # OpenAI-compatible: choices[0].finish_reason == "length"
    finish = ""
    try:
        finish = (body.get("choices") or [{}])[0].get("finish_reason") or ""
    except (AttributeError, IndexError, TypeError):
        finish = ""
    # Anthropic /v1/messages: top-level stop_reason == "max_tokens"
    stop_reason = body.get("stop_reason") if isinstance(body, dict) else ""

    truncated = finish == "length" or stop_reason == "max_tokens"
    out_tok = invocation.usage.output_tokens if invocation.usage else 0

    # 流被对端中途切断（SSEStreamAdapter 标记 truncated）——与 max_tokens 截断
    # 不同，这是传输层失败（judge 大 payload 压垮 agent 或上游网关 RST 连接）。
    # 明确报出来，别和「模型没吐 JSON」混为一谈，方便运维定位是 judge 端挂了。
    stream_cut = isinstance(body, dict) and body.get("truncated")
    if stream_cut:
        return (
            f"agent judge 的响应流被中途切断（已收 output_tokens={out_tok}，未读完）——"
            "多为 judge 请求体过大压垮被测 agent 或触发上游网关读超时/RST。"
            "排查网关 proxy_read_timeout 与 response buffering，或裁剪 judge 输入。"
        )
    if truncated:
        return (
            f"judge response was truncated at max_tokens (output_tokens={out_tok}); "
            "raise max_tokens in the evaluator config — the model emitted CoT prose "
            "before reaching the JSON output"
        )
    if not invocation.content:
        return "judge returned empty content"
    return "judge response is not parseable JSON (no { ... } block found)"


def _normalise_numeric(value: float, range_min: float, range_max: float) -> float:
    span = range_max - range_min
    if span <= 0:
        return 0.0
    clamped = max(range_min, min(range_max, value))
    return round((clamped - range_min) / span, 3)


def _coerce_bool(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in {"true", "yes", "y", "1", "pass"}:
            return True
        if s in {"false", "no", "n", "0", "fail"}:
            return False
    return None


def _parse_checklist_score(
    body: dict[str, Any],
    *,
    evaluator_name: str,
) -> tuple[JudgeScore | None, str | None]:
    """checklist 评分：模型只对每个检查项判 pass/fail/na + 给证据，
    最终分数由**后端机械计算** ``通过数 / (通过数 + 失败数)``——算术不经模型，
    保证同一组 verdict 必得同一分数（可验证、可复现）。

    期望 body：``{"checks": [{"id","verdict","evidence"}, ...], "reasoning"}``。
    verdict 归一：pass/true/yes/1 → pass；fail/false/no/0 → fail；
    na/n/a/not_applicable/不适用 → na（不计入分母）。
    - 通过数+失败数 == 0（全 na 或无 check）→ value=1.0，注明无适用检查项。
    """
    raw_checks = body.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        return None, "checklist judge response missing non-empty 'checks' array"

    norm_checks: list[dict[str, Any]] = []
    n_pass = n_fail = n_na = 0
    for i, item in enumerate(raw_checks):
        if not isinstance(item, dict):
            continue
        raw_verdict = str(item.get("verdict") or item.get("result") or "").strip().lower()
        if raw_verdict in {"pass", "true", "yes", "y", "1", "ok"}:
            verdict = "pass"
            n_pass += 1
        elif raw_verdict in {"fail", "false", "no", "n", "0"}:
            verdict = "fail"
            n_fail += 1
        elif raw_verdict in {"na", "n/a", "not_applicable", "notapplicable", "不适用", "无关"}:
            verdict = "na"
            n_na += 1
        else:
            # 无法识别的 verdict 一律按 fail 计（保守：不给"看不懂"送分）。
            verdict = "fail"
            n_fail += 1
        norm_checks.append({
            "id": str(item.get("id") or item.get("name") or f"C{i + 1}"),
            "desc": str(item.get("desc") or item.get("description") or ""),
            "verdict": verdict,
            "evidence": str(item.get("evidence") or item.get("reason") or ""),
        })

    if not norm_checks:
        return None, "checklist judge response 'checks' has no valid items"

    denom = n_pass + n_fail
    value = round(n_pass / denom, 3) if denom > 0 else 1.0
    reasoning = str(body.get("reasoning") or body.get("reason") or "")
    summary = f"{n_pass}/{denom} 通过" + (f"（{n_na} 项不适用）" if n_na else "")
    if denom == 0:
        summary = f"无适用检查项（{n_na} 项 na）→ 1.0"
    return JudgeScore(
        name=evaluator_name,
        value=value,
        reason=reasoning or summary,
        raw_value=summary,
        checks=norm_checks,
    ), None


def _parse_single_score(
    body: dict[str, Any],
    *,
    score_type: str,
    score_range: list[float] | None,
    categories: list[dict[str, Any]] | None,
    evaluator_name: str,
) -> tuple[JudgeScore | None, str | None]:
    """根据 score_type 解析模型返回的 ``score`` 字段。

    返回 ``(JudgeScore | None, error_msg | None)``。score_type 不识别或
    解析失败时 JudgeScore 为 None，error_msg 给出原因。
    """
    if (score_type or "numeric").lower() == "checklist":
        return _parse_checklist_score(body, evaluator_name=evaluator_name)

    raw_score = body.get("score")
    if raw_score is None and "value" in body:  # 兼容用户改了字段名
        raw_score = body.get("value")
    if raw_score is None and "composite_score" in body:
        # 多维 rubric prompt 常见返回顶级 composite_score（各维度加权求和），
        # 视作总分别名
        raw_score = body.get("composite_score")
    if raw_score is None:
        # 宽松兜底：不少 rubric prompt 自定义了总分字段名（如
        # faithfulness_score / conciseness_score），既不是 score 也不是
        # composite_score。此处扫顶级形如 ``*_score`` 且值可转数值的字段
        # 作为总分——子维度对象的键是 d1_xxx（不带 _score 后缀），不会误伤。
        # 恰好命中一个才采纳；多个总分字段无从判断主分，明确报歧义而非乱猜。
        def _is_num(v: Any) -> bool:
            if isinstance(v, bool):
                return False
            if isinstance(v, (int, float)):
                return True
            if isinstance(v, str):
                try:
                    float(v.strip())
                    return True
                except ValueError:
                    return False
            return False

        candidates = {
            k: v for k, v in body.items()
            if isinstance(k, str) and k.endswith("_score") and _is_num(v)
        }
        if len(candidates) == 1:
            raw_score = next(iter(candidates.values()))
        elif len(candidates) > 1:
            return None, (
                "judge response has multiple top-level '*_score' fields "
                f"({sorted(candidates)}); cannot decide which is the overall "
                "score — set the prompt to emit a single top-level 'score'"
            )
    reasoning = str(body.get("reasoning") or body.get("reason") or "")

    if raw_score is None:
        return None, "judge response missing 'score' field"

    stype = (score_type or "numeric").lower()

    if stype == "numeric":
        try:
            num = float(raw_score)
        except (TypeError, ValueError):
            return None, f"score is not numeric: {raw_score!r}"
        rng = score_range or list(DEFAULT_NUMERIC_RANGE)
        try:
            rmin, rmax = float(rng[0]), float(rng[1])
        except (TypeError, ValueError, IndexError):
            rmin, rmax = DEFAULT_NUMERIC_RANGE
        return JudgeScore(
            name=evaluator_name,
            value=_normalise_numeric(num, rmin, rmax),
            reason=reasoning,
            raw_value=num,
        ), None

    if stype == "boolean":
        b = _coerce_bool(raw_score)
        if b is None:
            return None, f"score is not boolean-like: {raw_score!r}"
        return JudgeScore(
            name=evaluator_name,
            value=1.0 if b else 0.0,
            reason=reasoning,
            raw_value=b,
        ), None

    if stype == "categorical":
        if not categories:
            return None, "categorical score_type requires 'categories' in params"
        key = str(raw_score).strip()
        for cat in categories:
            label = str(cat.get("label") or "").strip()
            if label == key:
                try:
                    val = float(cat.get("value", 0.0))
                except (TypeError, ValueError):
                    val = 0.0
                return JudgeScore(
                    name=evaluator_name,
                    value=max(0.0, min(1.0, val)),
                    reason=reasoning,
                    raw_value=label,
                ), None
        return None, f"score {key!r} does not match any configured category"

    return None, f"unknown score_type: {score_type!r}"


# ────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────


async def run_configurable_judge(
    *,
    params: dict[str, Any],
    provider: EvaluatorProviderRow,
    input_text: str,
    output_text: str,
    expected_output: str | None = None,
    metadata: dict[str, Any] | None = None,
    evaluator_name: str = "score",
) -> ConfigurableJudgeResult:
    """对一组 (input, output) 用 evaluator 配置打一个分。

    ``params`` 识别的 key（其它一律忽略，便于将来扩字段）：

    * ``model``               覆盖 ``provider.default_model``
    * ``temperature`` / ``max_tokens`` / ``timeout``
    * ``evaluation_prompt``   主 prompt（含占位符）
    * ``reasoning_prompt``    引导思考的 system 段
    * ``output_prompt``       约束输出格式的 system 段
    * ``score_type``          ``"numeric"`` | ``"boolean"`` | ``"categorical"``
    * ``score_range``         numeric 时使用 ``[min, max]``，默认 [0,1]
    * ``categories``          categorical 时使用 ``[{label, value(0..1)}, ...]``

    ``evaluator_name`` 用作返回 ``JudgeScore.name``——上层
    ``langfuse_runner`` 可以传 evaluator label 进来当分数键。
    """
    score_type = (params.get("score_type") or "numeric").lower()
    score_range = params.get("score_range")
    categories = params.get("categories")

    try:
        messages = _build_messages(
            params=params,
            input_text=input_text,
            output_text=output_text,
            expected_output=expected_output or "",
            metadata=metadata,
        )
    except TemplateRenderError as e:
        return ConfigurableJudgeResult(error=str(e))

    base_timeout = float(params.get("timeout", 120.0))
    # 有界补评循环：仅在「未出分」时重跑（成功即刻返回，绝不重复计费成功调用）。
    # 每轮线性拉长超时（base, 1.5x, 2x...），直击 ReadTimeout 根因——多数超时
    # 维度在给足时间后即出分；穷尽仍失败则返回最后一次 error 结果。
    last_result: ConfigurableJudgeResult | None = None
    for attempt in range(1, _JUDGE_COMPLETENESS_ATTEMPTS + 1):
        attempt_timeout = base_timeout * (1 + 0.5 * (attempt - 1))
        try:
            client = build_judge_client(
                provider,
                model=params.get("model"),
                temperature=float(params.get("temperature", 0.0)),
                max_tokens=int(params.get("max_tokens", 1024)),
                timeout=attempt_timeout,
            )
        except JudgeClientError as e:
            # 客户端构造失败（如缺 model）是确定性错误，重试无益，直接返回。
            return ConfigurableJudgeResult(rendered_messages=messages, error=str(e))

        try:
            async with client as judge:
                invocation = await judge.ainvoke(messages)
        except JudgeClientError as e:
            last_result = ConfigurableJudgeResult(rendered_messages=messages, error=str(e))
            if attempt < _JUDGE_COMPLETENESS_ATTEMPTS:
                logger.warning(
                    "configurable_judge[%s] no score (attempt %d/%d, next timeout=%.0fs): %s",
                    evaluator_name, attempt, _JUDGE_COMPLETENESS_ATTEMPTS,
                    base_timeout * (1 + 0.5 * attempt), e,
                )
                continue
            return last_result
        except Exception as e:
            logger.exception("configurable_judge: unexpected failure")
            last_result = ConfigurableJudgeResult(
                rendered_messages=messages,
                error=f"unexpected error: {type(e).__name__}: {e}",
            )
            if attempt < _JUDGE_COMPLETENESS_ATTEMPTS:
                continue
            return last_result

        # 流被中途切断（SSEStreamAdapter 标记 truncated）时，judge 根本没把结论说完，
        # 半截散文里的任何数字都是无关数字——此时 salvage 抽分会把「基础设施失败」
        # 伪造成一个自信的分，可能把该 error 的 case 洗成 pass。故 truncated 一律不
        # 兜底，如实报 error，交由上层（judge_failed_dims）判 error 而非静默 skipped。
        raw_resp = invocation.raw_response if isinstance(invocation.raw_response, dict) else {}
        was_truncated = bool(raw_resp.get("truncated"))

        body = _extract_json(invocation.content)
        if body is None:
            # agent-as-judge 兜底：SSE 业务 agent 常无视 JSON 契约、直接吐中文散文。
            # numeric/boolean 场景下退而从散文里正则捞一个数值分，避免整轮评估
            # 因"agent 不吐 JSON"而全体 skipped。仅对 agent 类型 provider 生效——
            # 不放松 LLM judge 的严格 JSON 契约（散文里的无关数字不该被当分）。
            # truncated 响应不兜底（见上）——半截话抽出的分不可信。
            if (
                provider.provider_type == "agent"
                and score_type in ("numeric", "boolean")
                and not was_truncated
            ):
                salvaged = _salvage_prose_score(invocation.content, score_type=score_type)
                if salvaged is not None:
                    value, snippet = salvaged
                    return ConfigurableJudgeResult(
                        scores=[JudgeScore(
                            name=evaluator_name,
                            value=value,
                            reason=f"（从 agent 散文回复兜底解析）匹配：{snippet}",
                            raw_value=snippet,
                        )],
                        usage=invocation.usage,
                        model=invocation.model,
                        raw_response=invocation.raw_response,
                        raw_content=invocation.content,
                        rendered_messages=messages,
                    )
            last_result = ConfigurableJudgeResult(
                rendered_messages=messages,
                usage=invocation.usage,
                model=invocation.model,
                raw_response=invocation.raw_response,
                raw_content=invocation.content,
                error=_diagnose_unparseable(invocation),
            )
            if attempt < _JUDGE_COMPLETENESS_ATTEMPTS:
                logger.warning(
                    "configurable_judge[%s] unparseable (attempt %d/%d), retrying",
                    evaluator_name, attempt, _JUDGE_COMPLETENESS_ATTEMPTS,
                )
                continue
            return last_result

        score, parse_err = _parse_single_score(
            body,
            score_type=score_type,
            score_range=score_range,
            categories=categories,
            evaluator_name=evaluator_name,
        )
        if score:
            # 出分即成功——立刻返回，绝不因补评重复计费一次成功调用。
            return ConfigurableJudgeResult(
                scores=[score],
                usage=invocation.usage,
                model=invocation.model,
                raw_response=invocation.raw_response,
                raw_content=invocation.content,
                rendered_messages=messages,
                error=parse_err,
            )
        # 解析出 JSON 但无有效分数（schema 不符）——有界重试后仍无分则返回。
        last_result = ConfigurableJudgeResult(
            scores=[],
            usage=invocation.usage,
            model=invocation.model,
            raw_response=invocation.raw_response,
            raw_content=invocation.content,
            rendered_messages=messages,
            error=parse_err,
        )
        if attempt < _JUDGE_COMPLETENESS_ATTEMPTS:
            continue
        return last_result

    # 循环理应在最后一轮 return；兜底防御，绝不静默出空。
    return last_result or ConfigurableJudgeResult(
        rendered_messages=messages, error="judge produced no result",
    )


__all__ = [
    "ConfigurableJudgeResult",
    "JudgeScore",
    "DEFAULT_EVALUATION_PROMPT",
    "DEFAULT_REASONING_PROMPT",
    "DEFAULT_OUTPUT_PROMPT",
    "CHECKLIST_REASONING_PROMPT",
    "CHECKLIST_OUTPUT_PROMPT",
    "DEFAULT_VARIABLE_MAPPING",
    "TemplateRenderError",
    "run_configurable_judge",
]
