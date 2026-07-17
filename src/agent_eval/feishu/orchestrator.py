"""内置 agent 编排：自然语言 → JSON 意图 → 工具调用（ReAct 精简版）。

范式复用（不引入 function-calling 协议、不加 langchain）：
- LLM 底座用 `build_judge_client`（judge_clients.py），与评估器判分同一套
  provider 凭证/重试/超时。
- 让模型吐 `{action, params}` JSON，用 `_extract_json`（configurable_judge.py）
  抠出来——这与 configurable_judge / case_generator 已在用的「prompt→JSON」
  闭环完全一致。
- 手写 tool registry（见 tools.py 的 TOOLS）把 action dispatch 到本地 API 调用。

循环：最多 N 轮。每轮把「工具结果」塞回对话再问 LLM，直到 LLM 给出
`{action: "final", params: {reply: "..."}}` 或达到轮数上限。

二次确认门（危险操作）：
- 删除类工具（tools.py 里 dangerous=True）**不在编排循环内直接执行**。一旦
  LLM 选中危险工具，本函数立即中断循环，返回一个 `PendingAction`（工具名 +
  参数 + 人类可读摘要）给 bot_service。
- bot_service 按 open_id 暂存该 pending，回一句确认提示；用户下条消息回
  「确认」才由 `execute_pending` 真正下发；回「取消」或其它则丢弃。
- 之所以放在编排层拦截、而非工具层：确认状态天然跨消息（飞书是多轮会话），
  必须由持有 open_id 会话态的 bot_service 承接；工具层只管「怎么调」。

provider 选择：用 config `feishu.judge_provider`（默认 kiro）对应的
evaluator_provider 记录作 LLM。缺失则回一句降级提示（不崩）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agent_eval.db import async_session_factory
from agent_eval.db_models.repository import Repository
from agent_eval.feishu.skills import SKILLS, skills_catalog
from agent_eval.feishu.tools import TOOLS, tools_catalog

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是 agent-eval 平台的助手。用户用自然语言提出请求，你需要：
1. 判断该调用哪个工具（或直接回答）。
2. 只输出一个 JSON 对象，格式：{"action": "<工具名或 final>", "params": {...}}。
3. 工具结果会以 observation 形式返回给你，你据此决定下一步或给出 final 回复。
4. final 的 params 形如 {"reply": "给用户看的中文回复"}。

可用工具：
{tools}

规则：
- 只输出 JSON，不要额外解释文字。
- 需要多步时逐步调用；信息够了就 final。
- 写操作（新建/更新数据集、样例、评估器、provider，试跑、启动/停止评估）可
  直接调用；但参数不全（缺 id / 缺来源 / 缺必填字段）时先 final 反问用户，
  不要臆造 id、URL 或密钥。
- 标注【危险·需二次确认】的删除类工具：你照常发起调用（给出正确 action +
  params 即可），系统会自动拦截并向用户请求确认，你无需自己模拟确认流程。
- 当用户问「你能做什么 / 有什么功能 / 怎么用」，或明显是新手、表达迷茫时：
  直接 final 给一份**结构化能力概览**（不要逐个罗列工具名），按域分组介绍：
  数据集与样例管理、评估器与 judge provider 配置、发起/查看评估运行、
  多维表格导入导出、定时评估任务、评估结果分析报告；每组给一句话作用 +
  一个最小自然语言示例（如「列出所有数据集」「用数据集 X 定时评估 agent Y」）。
  目的是让用户知道能说什么，而不是等他猜。
- 预设技能（见下「可用技能」）：把「本要连调好几个只读工具才能拼出的固定流程」
  收敛成一次调用（如数据集体检、评估运行报告、平台总览）。用法与工具完全一致：
  action 填技能名、params 填其参数。用户意图命中某技能时**优先用技能**而非
  自己手工连调多个工具——一次到位、步骤更稳。技能只做只读聚合，不含写/删操作。

可用技能：
{skills}

final 回复（reply 字段）的写法——飞书卡片会用 markdown 渲染，务必清晰直白：
- 开门见山给结论，不要用「抱歉」「很遗憾」这类道歉开场；做不到的事直接说
  「暂不支持 X」并紧跟一句可行的替代路径。
- 善用 markdown：关键数字/名称用 **加粗**；成组信息用「- 」列表；有层级时
  用一行 **小标题** 再跟列表；需要分区时用一空行隔开。
- 数据要点前置：如「共 **10** 个数据集」先给总数，再分组列细节。列表每项
  控制在一行，形如 `- **名字**：要点（数字/状态）`。
- 反问用户时，明确列出「还缺什么」，能给例子就给一个最小例子，别让用户猜格式。
- 简洁：能一句话说清就不写两句；不复述用户的问题，不加寒暄和多余的收尾语。
"""


@dataclass
class PendingAction:
    """一个已被 LLM 选中、但因危险（删除类）而暂缓、等用户确认的动作。"""
    tool_name: str
    params: dict[str, Any]
    summary: str  # 给用户看的中文摘要（确认提示里回显）


@dataclass
class OrchestrationResult:
    """一次编排的产出：要么给出最终回复，要么给出一个待确认的危险动作。"""
    reply: str
    pending: PendingAction | None = None


def _summarize_pending(tool_name: str, params: dict[str, Any]) -> str:
    """把危险动作渲染成一句人类可读的确认摘要（回显关键标的）。"""
    target = (
        params.get("name")
        or params.get("example_id")
        or params.get("evaluator_id")
        or params.get("provider_id")
        or params.get("run_id")
    )
    if tool_name == "batch_delete_cases":
        ids = params.get("example_ids") or []
        target = f"{len(ids)} 个样例" if isinstance(ids, list) else str(ids)
    tool = TOOLS.get(tool_name)
    desc = tool.description if tool else tool_name
    return f"即将执行【{tool_name}】：{desc}\n目标：{target}"


async def _load_provider_row(provider_name: str):
    """按 name 找 evaluator_provider 记录（作编排 LLM）。找不到返回 None。"""
    async with async_session_factory() as session:
        repo = Repository(session)
        providers = await repo.list_evaluator_providers()
    for p in providers:
        if p.name == provider_name:
            return p
    return None


async def execute_pending(pending: PendingAction, token: str) -> str:
    """用户确认后真正下发危险动作，返回中文结果。"""
    tool = TOOLS.get(pending.tool_name)
    if tool is None:
        return f"该操作已失效（未知工具 {pending.tool_name}），请重新发起。"
    try:
        result = await tool.run(pending.params, token)
    except Exception as e:  # noqa: BLE001
        logger.exception("pending tool %s crashed", pending.tool_name)
        return f"执行出错：{type(e).__name__}: {e}"
    if result.get("ok"):
        return f"已执行：{pending.tool_name}。结果：{json.dumps(result.get('data'), ensure_ascii=False)[:800]}"
    # 后端 403（非 admin）等在这里如实回给用户。
    return f"未能执行 {pending.tool_name}：{result.get('error') or result.get('status')}"


async def run_orchestration(
    user_text: str, token: str, *, open_id: str | None = None, max_rounds: int = 5,
) -> OrchestrationResult:
    """把用户自然语言跑成一次或多次工具调用。

    返回 OrchestrationResult：
    - 正常完成 → reply 有值、pending=None。
    - 命中危险工具 → pending 有值（reply 为确认提示），调用方需暂存等用户确认。
    """
    from agent_eval.config import settings
    from agent_eval.evaluation.configurable_judge import _extract_json
    from agent_eval.evaluation.judge_clients import JudgeClientError, build_judge_client

    provider_row = await _load_provider_row(settings.feishu.judge_provider)
    if provider_row is None:
        return OrchestrationResult(reply=(
            f"编排 LLM 未配置（缺 provider «{settings.feishu.judge_provider}»）。"
            "请管理员在 Judge Providers 里配置后重试。"
        ))

    system = _SYSTEM_PROMPT.replace("{tools}", tools_catalog()).replace(
        "{skills}", skills_catalog()
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]

    # 机器人编排模型：config 显式指定则覆盖 provider.default_model（只影响机器人，
    # 不改 kiro provider 本身、评估器不受影响）。空串→None→回退 default_model。
    override_model = (settings.feishu.judge_model or "").strip() or None

    for _ in range(max_rounds):
        try:
            async with build_judge_client(provider_row, model=override_model) as judge:
                invocation = await judge.ainvoke(messages)
        except JudgeClientError as e:
            return OrchestrationResult(reply=f"编排 LLM 调用失败：{e}")
        except Exception as e:  # noqa: BLE001
            logger.exception("orchestration LLM call crashed")
            return OrchestrationResult(reply=f"编排出错：{type(e).__name__}: {e}")

        body = _extract_json(invocation.content)
        if not body or "action" not in body:
            # 模型没吐合法 JSON——把它当直接回复兜底。
            return OrchestrationResult(
                reply=(invocation.content or "").strip() or "（未能理解，请换个说法）"
            )

        action = body.get("action")
        params = body.get("params") or {}

        if action == "final":
            return OrchestrationResult(reply=params.get("reply") or "（完成）")

        # 注入触发者 open_id：让机器人发起的评估 / 新建的定时任务把完成通知
        # 回推给当前用户。仅在 LLM 未显式给出时补默认，不覆盖其显式意图。
        if open_id:
            if action == "start_eval":
                existing = params.get("notify_open_ids")
                if not existing:
                    params["notify_open_ids"] = [open_id]
            elif action in ("create_scheduled_task", "update_scheduled_task"):
                params.setdefault("created_by", open_id)
                if action == "create_scheduled_task" and not params.get("notify_open_ids"):
                    params["notify_open_ids"] = [open_id]

        # 预设技能：与工具同构地 dispatch。技能只做只读聚合（内部串若干只读
        # 工具），无危险分支，直接执行并把合成结果当作一次 observation 回灌。
        skill = SKILLS.get(action)
        if skill is not None:
            try:
                result = await skill.run(params, token)
            except Exception as e:  # noqa: BLE001
                logger.exception("skill %s crashed", action)
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            messages.append({"role": "assistant", "content": invocation.content})
            messages.append({
                "role": "user",
                "content": (
                    f"observation（技能 {action} 的结果）:\n"
                    f"{json.dumps(result, ensure_ascii=False)[:3000]}"
                ),
            })
            continue

        tool = TOOLS.get(action)
        if tool is None:
            messages.append({"role": "assistant", "content": invocation.content})
            messages.append({
                "role": "user",
                "content": (
                    f"没有名为 {action} 的工具或技能。"
                    f"可用工具：{', '.join(TOOLS)}。可用技能：{', '.join(SKILLS)}。请重新决定。"
                ),
            })
            continue

        # 危险操作：中断编排，交由 bot_service 暂存并向用户请求确认。
        if tool.dangerous:
            summary = _summarize_pending(action, params)
            return OrchestrationResult(
                reply=f"{summary}\n\n此操作不可逆。回复「确认」执行，回复「取消」放弃。",
                pending=PendingAction(tool_name=action, params=params, summary=summary),
            )

        try:
            result = await tool.run(params, token)
        except Exception as e:  # noqa: BLE001
            logger.exception("tool %s crashed", action)
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        messages.append({"role": "assistant", "content": invocation.content})
        messages.append({
            "role": "user",
            "content": (
                f"observation（{action} 的结果）:\n"
                f"{json.dumps(result, ensure_ascii=False)[:3000]}"
            ),
        })

    return OrchestrationResult(
        reply="（达到最大编排轮数仍未完成，请把需求说得更具体些）"
    )
