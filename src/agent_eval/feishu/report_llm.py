"""评估结果的 LLM 叙述式分析报告。

给一次评估运行的聚合指标（``summary_scores``）生成一段中文自然语言分析，
供两处消费：
- 飞书 Bitable 导出「概览」表（``export-bitable`` 的 ``include_report``）；
- 机器人对话里用户问「帮我分析下这次评估」。

底座复用 ``build_judge_client``（judge_clients.py），与编排 / 判分同一套
provider 凭证/重试/超时——LLM 用 config ``feishu.judge_provider``（默认 kiro）
对应的 evaluator_provider 记录，缺失则降级为「基于规则的摘要」（不崩，仍给
一段可读文字），因为报告是增强项、不应因 LLM 不可用而阻断导出主流程。

设计要点：
- **输入是已聚合的 summary_scores**（facts / acceptance / dimension_averages /
  score_distribution / tool_usage / cost_scored / cost_execution_abnormal /
  retry_stats / run_name），不重新拉逐样例——报告是「宏观解读」，逐样例数据
  已在导出的明细表里，无需喂给 LLM（省 token、避免超长）。
- **纯文本 markdown 输出**，不要求 LLM 吐 JSON（与编排/判分不同）：报告本身
  就是给人读的散文，套 JSON 只会增加解析失败面。
- 失败绝不抛给上层：任何异常都退回 ``_rule_based_summary``，保证「含分析报告」
  的导出/对话链路始终有话可说。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 多轮维度后缀：``correctness.turn0``/``.conversation`` → 折叠到 base ``correctness``。
# 与前端 dimensionCollapse.collapseScoreKey 同规则，保证喂 LLM 的维度不逐轮碎裂。
_TURN_SUFFIX = re.compile(r"^(turn\d+|conversation)$")


def _collapse_score_key(key: str) -> str:
    idx = key.rfind(".")
    if idx <= 0:
        return key
    return key[:idx] if _TURN_SUFFIX.match(key[idx + 1:]) else key


def _collapse_dim_avg(dim_avg: dict[str, Any] | None) -> dict[str, float]:
    """把逐轮维度均分折叠到 base 维度（各轮求均值）。

    多轮 run 的 dimension_averages key 形如 ``回答正确性.turn0..turnN``；不折叠
    会让 LLM 看到几十个碎维度而啰嗦。按 base 聚合为各轮均值（近似，无 count 权重）。
    """
    if not isinstance(dim_avg, dict) or not dim_avg:
        return {}
    acc: dict[str, dict[str, float]] = {}
    for k, v in dim_avg.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        base = _collapse_score_key(str(k))
        slot = acc.setdefault(base, {"sum": 0.0, "n": 0.0})
        slot["sum"] += fv
        slot["n"] += 1
    return {b: s["sum"] / s["n"] for b, s in acc.items() if s["n"]}

_SYSTEM_PROMPT = """你是资深的 AI 智能体评估分析师。用户会给你一次评估运行的聚合指标（JSON），你需要写一段简洁、专业、可执行的中文分析报告。

报告必须严格区分三层语义：Agent 执行事实、Judge 评分事实、显式验收结论。
1. **总体事实**：说明执行成功/异常，以及评分完成/跳过/异常的样例数。
2. **验收结论**：仅当 acceptance.configured=true 时，报告验收通过率、决策覆盖率和运行结论；未配置时必须明确“仅评分，未配置验收规则”，绝不能从分数推断通过/失败。
3. **维度表现**：逐维度点评平均分，指出强弱项；分数只是观测值，不自动等于达标。
4. **工具与效率**：按可用的 tool_usage、cost_scored、cost_execution_abnormal 分析。
5. **改进建议**：给出 2-4 条可操作建议。

要求：只依据给定数据；缺失项直说无数据；关键数字和维度名加粗；全文 400 字以内。"""


def _fmt_pct(part: int, whole: int) -> str:
    if not whole:
        return "0%"
    return f"{part / whole * 100:.0f}%"


def _rule_based_summary(summary: dict[str, Any], run_name: str) -> str:
    """LLM 不可用时按统一语义生成规则摘要。"""
    lines: list[str] = [f"**{run_name or '评估运行'} · 结果摘要**", ""]

    facts = summary.get("facts") or {}
    total = int(facts.get("total") or 0)
    if total:
        lines.append(
            f"- 共 **{total}** 个样例：Agent 执行成功 **{int(facts.get('execution_success') or 0)}**、"
            f"异常 **{int(facts.get('execution_abnormal') or 0)}**、未知 **{int(facts.get('execution_unknown') or 0)}**。"
        )
        lines.append(
            f"- Judge 评分完成 **{int(facts.get('evaluation_completed') or 0)}**、"
            f"跳过 **{int(facts.get('skipped') or 0)}**、"
            f"异常或信息不足 **{int(facts.get('evaluation_partial_or_error') or 0)}**。"
        )

    acceptance = summary.get("acceptance") or {}
    if acceptance.get("configured"):
        decided = int(acceptance.get("decided") or 0)
        passed = int(acceptance.get("passed") or 0)
        failed = int(acceptance.get("failed") or 0)
        undetermined = int(acceptance.get("undetermined") or 0)
        pass_rate = acceptance.get("pass_rate")
        coverage = acceptance.get("decision_coverage")
        rate_text = f"{float(pass_rate) * 100:.1f}%" if pass_rate is not None else "无数据"
        coverage_text = f"{float(coverage) * 100:.1f}%" if coverage is not None else "无数据"
        lines.append(
            f"- 显式验收：已决策 **{decided}**，通过 **{passed}**、未通过 **{failed}**、"
            f"未定 **{undetermined}**；通过率 **{rate_text}**，决策覆盖率 **{coverage_text}**，"
            f"运行结论 **{acceptance.get('run_decision') or 'undetermined'}**。"
        )
    else:
        lines.append("- 本次运行仅评分，**未配置验收规则**；不生成通过率或达标结论。")

    dim_avg = _collapse_dim_avg(summary.get("dimension_averages"))
    if dim_avg:
        ordered = sorted(dim_avg.items(), key=lambda kv: kv[1])
        weakest, w_score = ordered[0]
        strongest, s_score = ordered[-1]
        lines.append(
            f"- 维度均分：最高 **{strongest}**（{s_score:.2f}）、最低 **{weakest}**（{w_score:.2f}）。"
        )
        lines.append("  各维度：" + "、".join(f"{k} {v:.2f}" for k, v in dim_avg.items()))

    tool_usage = summary.get("tool_usage") or []
    if isinstance(tool_usage, list) and tool_usage:
        top = tool_usage[0]
        errored = [t for t in tool_usage if isinstance(t, dict) and t.get("errors")]
        errmsg = ""
        if errored:
            worst = max(errored, key=lambda t: t.get("errors", 0))
            errmsg = f"；报错最多 **{worst.get('name')}**（{worst.get('errors')} 次）"
        if isinstance(top, dict):
            lines.append(
                f"- 工具调用共 {len(tool_usage)} 种，最频繁 **{top.get('name')}**"
                f"（{top.get('calls')} 次）{errmsg}。"
            )

    if len(lines) <= 3:
        lines.append("- 暂无足够聚合数据可供分析。")
    lines.extend(["", "_（LLM 分析未启用，以上为基于规则的自动摘要。）_"])
    return "\n".join(lines)


def _compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """把统一语义汇总精简成喂给 LLM 的最小 JSON。"""
    out: dict[str, Any] = {}
    for key in (
        "facts", "acceptance", "score_distribution",
        "cost_scored", "cost_execution_abnormal",
        "cost_accepted", "cost_not_accepted", "retry_stats",
    ):
        if summary.get(key) is not None:
            out[key] = summary[key]

    collapsed = _collapse_dim_avg(summary.get("dimension_averages"))
    if collapsed:
        out["dimension_averages"] = collapsed
    tool_usage = summary.get("tool_usage")
    if isinstance(tool_usage, list) and tool_usage:
        out["tool_usage"] = tool_usage[:15]
    return out


async def generate_run_report(
    summary: dict[str, Any] | None, *, run_name: str = "",
) -> str:
    """为一次评估运行生成中文分析报告（markdown 文本）。

    ``summary`` 即 ``TestRunRow.summary_scores``（facts / acceptance /
    dimension_averages / score_distribution / tool_usage / cost_* / retry_stats）。

    LLM 走 config ``feishu.judge_provider``；provider 缺失或调用失败时退回
    ``_rule_based_summary``，绝不抛出——报告是增强项，不应阻断导出/对话主流程。
    """
    if not summary:
        return "本次运行暂无聚合数据，无法生成分析报告（可能尚未完成或无评分）。"

    import json

    from agent_eval.config import settings

    # 延迟导入，避免 feishu 包在无 LLM 依赖的最小环境里 import 就炸。
    try:
        from agent_eval.feishu.orchestrator import _load_provider_row
        from agent_eval.evaluation.judge_clients import (
            JudgeClientError,
            build_judge_client,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("report_llm imports unavailable, falling back to rules: %s", e)
        return _rule_based_summary(summary, run_name)

    try:
        provider_row = await _load_provider_row(settings.feishu.judge_provider)
        if provider_row is None:
            logger.info(
                "report_llm: provider «%s» missing, using rule-based summary",
                settings.feishu.judge_provider,
            )
            return _rule_based_summary(summary, run_name)

        payload = _compact_summary(summary)
        user_msg = (
            f"评估运行名：{run_name or '（未命名）'}\n"
            f"聚合指标 JSON：\n{json.dumps(payload, ensure_ascii=False)}"
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        override_model = (settings.feishu.judge_model or "").strip() or None

        async with build_judge_client(provider_row, model=override_model) as judge:
            invocation = await judge.ainvoke(messages)
        text = (invocation.content or "").strip()
        if not text:
            return _rule_based_summary(summary, run_name)
        return text
    except JudgeClientError as e:
        logger.warning("report_llm LLM call failed: %s", e)
    except Exception:  # noqa: BLE001
        logger.exception("report_llm crashed, falling back to rules")
    return _rule_based_summary(summary, run_name)


# ─────────────────────────────────────────────────────────────────────
# 对比报告：多个 run 的差异解读
# ─────────────────────────────────────────────────────────────────────

_COMPARE_SYSTEM_PROMPT = """你是资深的 AI 智能体评估分析师。用户会给你多次评估运行的对比数据（JSON），请写简洁、专业、可执行的中文对比报告。

必须严格区分 Agent 执行、Judge 评分和显式验收：
1. 先对比执行成功率、评分覆盖及维度均分。
2. 只有某运行 acceptance.configured=true 时，才可报告该运行的验收通过率和运行结论；未配置的运行必须标注“仅评分”，不得把分数转成通过率。
3. 混合比较时不得把“未配置验收”当作 0% 或失败。
4. 成本对比优先使用 cost_scored / cost_execution_abnormal；有验收策略时可补充 cost_accepted / cost_not_accepted。
5. 若有 align_stats，说明公共样例上的评分高低分歧，不称作通过/失败分歧。

只依据给定数据，缺失项直说无数据，全文 400 字以内。"""


def _rule_based_compare(payload: dict[str, Any]) -> str:
    """LLM 不可用时按统一语义生成对比摘要。"""
    runs = payload.get("runs") or []
    lines: list[str] = ["**评估对比 · 结果摘要**", ""]
    if not runs:
        lines.append("- 暂无可对比的运行数据。")
        return "\n".join(lines)

    for run in runs:
        name = run.get("name")
        facts = run.get("facts") or {}
        total = int(facts.get("total") or run.get("total") or 0)
        execution_success = int(facts.get("execution_success") or 0)
        scored = int(facts.get("evaluation_completed") or facts.get("scored") or 0)
        fact_text = f"总样例 {total}，执行成功 {execution_success}，评分完成 {scored}"
        acceptance = run.get("acceptance") or {}
        if acceptance.get("configured"):
            pass_rate = acceptance.get("pass_rate")
            rate_text = f"{float(pass_rate) * 100:.1f}%" if pass_rate is not None else "无数据"
            acceptance_text = (
                f"；验收通过率 {rate_text}，结论 "
                f"{acceptance.get('run_decision') or 'undetermined'}"
            )
        else:
            acceptance_text = "；仅评分，未配置验收规则"
        dims = _collapse_dim_avg(run.get("dimension_averages"))
        dim_text = "、".join(f"{k} {float(v):.2f}" for k, v in list(dims.items())[:6])
        lines.append(
            f"- **{name}**：{fact_text}{acceptance_text}"
            + (f"；维度 {dim_text}" if dim_text else "")
        )

    align = payload.get("align_stats")
    if isinstance(align, dict) and align.get("common"):
        lines.append(
            f"- 公共样例评分对比：公共 **{align.get('common')}** 个、分数有差异 "
            f"**{align.get('diverging')}** 个；{align.get('a_name')} 较高 "
            f"**{align.get('a_better')}** 个，{align.get('b_name')} 较高 "
            f"**{align.get('b_better')}** 个。"
        )
    lines.extend(["", "_（LLM 分析未启用，以上为基于规则的自动摘要。）_"])
    return "\n".join(lines)


async def generate_compare_report(
    runs_summary: list[dict[str, Any]], *, align_stats: dict[str, Any] | None = None,
) -> str:
    """为多次评估运行生成中文对比分析报告（markdown 文本）。

    ``runs_summary`` 每项包含 ``name / facts / acceptance /
    dimension_averages / cost_*``。``align_stats``（可选）为交叉样例评分统计
    ``{common, diverging, a_better, b_better, a_name, b_name}``。

    与 ``generate_run_report`` 同款：走 config ``feishu.judge_provider``；provider
    缺失或调用失败退回 ``_rule_based_compare``，绝不抛。
    """
    payload: dict[str, Any] = {"runs": runs_summary}
    if align_stats:
        payload["align_stats"] = align_stats

    if not runs_summary:
        return "本次对比暂无可用运行数据。"

    import json

    from agent_eval.config import settings

    try:
        from agent_eval.feishu.orchestrator import _load_provider_row
        from agent_eval.evaluation.judge_clients import (
            JudgeClientError,
            build_judge_client,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("report_llm compare imports unavailable: %s", e)
        return _rule_based_compare(payload)

    try:
        provider_row = await _load_provider_row(settings.feishu.judge_provider)
        if provider_row is None:
            logger.info("report_llm compare: provider missing, rule-based")
            return _rule_based_compare(payload)

        user_msg = f"对比数据 JSON：\n{json.dumps(payload, ensure_ascii=False)}"
        messages = [
            {"role": "system", "content": _COMPARE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        override_model = (settings.feishu.judge_model or "").strip() or None

        async with build_judge_client(provider_row, model=override_model) as judge:
            invocation = await judge.ainvoke(messages)
        text = (invocation.content or "").strip()
        if not text:
            return _rule_based_compare(payload)
        return text
    except JudgeClientError as e:
        logger.warning("report_llm compare LLM call failed: %s", e)
    except Exception:  # noqa: BLE001
        logger.exception("report_llm compare crashed, falling back to rules")
    return _rule_based_compare(payload)
