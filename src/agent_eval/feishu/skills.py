"""预设技能：把「本要跑好几轮工具才能拼出的固定只读流程」收敛成一次调用。

定位（与 tools.py / orchestrator.py 的关系）：
- 技能不是新的后端能力，而是既有 TOOLS 的**编排宏**——按工具名 dispatch，
  串起多个只读工具，合并结果。因此天然复用 tools.py 的门禁/租户注入/HTTP 契约，
  零耦合、零新增后端逻辑。
- 编排 LLM 看到的技能和工具同构：action 填技能名、params 填其参数；命中时
  orchestrator 直接 await 技能的 run，把合成结果当作一次 observation 回灌。
- 只做**只读聚合**：技能内部只调只读工具（get_*/list_*/analyze_*/*_stats），
  不触发任何写/危险操作。写操作仍走单工具 + 二次确认门，技能不绕过它。

为什么值得单列：像「这次跑得怎么样」需要 get_run + get_run_results + analyze_run
三连；「这个数据集健不健康」需要 stats + quality + duplicates + capacity 四连。
让 LLM 每次手工拼多轮既慢又易漏步，固化成技能后一次到位、步骤稳定。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent_eval.feishu.tools import TOOLS

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str
    parameters: dict[str, Any]  # 同 Tool：JSON-schema 风格入参说明（供 LLM 决策）
    run: Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]


async def _call(tool_name: str, args: dict[str, Any], token: str) -> dict[str, Any]:
    """按名字调一个已注册工具，把异常/缺失收敛成 {ok:False,...}，不打断技能其余步骤。"""
    tool = TOOLS.get(tool_name)
    if tool is None:
        return {"ok": False, "error": f"内部错误：技能引用了未知工具 {tool_name}"}
    try:
        return await tool.run(args, token)
    except Exception as e:  # noqa: BLE001
        logger.exception("skill sub-tool %s crashed", tool_name)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _need(args: dict[str, Any], key: str) -> Any:
    v = args.get(key)
    return v.strip() if isinstance(v, str) else v


# ── 技能实现 ──────────────────────────────────────────────────────────────
# 约定：每个技能返回 {ok, skill, steps:{子工具名: 该步 ok?}, data:{...合成结果}}。
# steps 让编排 LLM 一眼看清哪步取到、哪步失败（部分失败也照常给已取到的部分）。

async def _dataset_health(args: dict[str, Any], token: str) -> dict[str, Any]:
    """数据集体检：stats + quality + duplicates + capacity 一次拉齐。name 必填。"""
    name = _need(args, "name")
    if not name:
        return {"ok": False, "error": "缺少 name（数据集名）"}
    stats = await _call("get_dataset_stats", {"name": name}, token)
    quality = await _call("dataset_quality", {"name": name}, token)
    dups = await _call("find_duplicates", {"name": name}, token)
    cap = await _call("dataset_capacity", {"name": name}, token)
    steps = {
        "get_dataset_stats": bool(stats.get("ok")),
        "dataset_quality": bool(quality.get("ok")),
        "find_duplicates": bool(dups.get("ok")),
        "dataset_capacity": bool(cap.get("ok")),
    }
    return {
        "ok": any(steps.values()),
        "skill": "dataset_health",
        "steps": steps,
        "data": {
            "dataset": name,
            "stats": stats.get("data") if stats.get("ok") else {"error": stats.get("error") or stats.get("status")},
            "quality": quality.get("data") if quality.get("ok") else {"error": quality.get("error") or quality.get("status")},
            "duplicates": dups.get("data") if dups.get("ok") else {"error": dups.get("error") or dups.get("status")},
            "capacity": cap.get("data") if cap.get("ok") else {"error": cap.get("error") or cap.get("status")},
        },
    }


async def _run_report(args: dict[str, Any], token: str) -> dict[str, Any]:
    """评估运行体检：get_run 详情 + get_run_results 头部样例 + analyze_run 叙述报告。
    run_id 必填；results_page_size 可选（默认取前 5 条样例做手感，避免刷屏）。"""
    run_id = _need(args, "run_id")
    if not run_id:
        return {"ok": False, "error": "缺少 run_id（运行 ID）"}
    page_size = args.get("results_page_size", 5)
    detail = await _call("get_run", {"run_id": run_id}, token)
    results = await _call("get_run_results", {"run_id": run_id, "page": 1, "page_size": page_size}, token)
    analysis = await _call("analyze_run", {"run_id": run_id}, token)
    steps = {
        "get_run": bool(detail.get("ok")),
        "get_run_results": bool(results.get("ok")),
        "analyze_run": bool(analysis.get("ok")),
    }
    return {
        "ok": any(steps.values()),
        "skill": "run_report",
        "steps": steps,
        "data": {
            "run_id": run_id,
            "detail": detail.get("data") if detail.get("ok") else {"error": detail.get("error") or detail.get("status")},
            "sample_results": results.get("data") if results.get("ok") else {"error": results.get("error") or results.get("status")},
            "analysis": analysis.get("data") if analysis.get("ok") else {"error": analysis.get("error") or analysis.get("status")},
        },
    }


async def _platform_overview(args: dict[str, Any], token: str) -> dict[str, Any]:
    """平台总览：数据集清单 + 最近运行 + Langfuse 指标总览 + 客户反馈总览。
    可选 days（指标回看天数）。给『整体情况怎么样』这类问题一站式作答。"""
    days = args.get("days")
    datasets = await _call("list_datasets", {}, token)
    runs = await _call("list_runs", {"page": 1, "page_size": 10}, token)
    metrics = await _call("metrics_overview", {"days": days} if days is not None else {}, token)
    feedback = await _call("feedback_stats", {}, token)
    steps = {
        "list_datasets": bool(datasets.get("ok")),
        "list_runs": bool(runs.get("ok")),
        "metrics_overview": bool(metrics.get("ok")),
        "feedback_stats": bool(feedback.get("ok")),
    }
    return {
        "ok": any(steps.values()),
        "skill": "platform_overview",
        "steps": steps,
        "data": {
            "datasets": datasets.get("data") if datasets.get("ok") else {"error": datasets.get("error") or datasets.get("status")},
            "recent_runs": runs.get("data") if runs.get("ok") else {"error": runs.get("error") or runs.get("status")},
            "metrics": metrics.get("data") if metrics.get("ok") else {"error": metrics.get("error") or metrics.get("status")},
            "feedback": feedback.get("data") if feedback.get("ok") else {"error": feedback.get("error") or feedback.get("status")},
        },
    }


SKILLS: dict[str, Skill] = {
    "dataset_health": Skill(
        name="dataset_health",
        description=(
            "数据集一键体检：汇总统计 + 质量校验 + 重复项 + 容量用量。用户问"
            "『X 数据集怎么样 / 健不健康 / 有没有问题』时优先用它，一次给全。"
        ),
        parameters={"name": "必填，数据集名"},
        run=_dataset_health,
    ),
    "run_report": Skill(
        name="run_report",
        description=(
            "评估运行一键报告：运行详情（维度均分/状态）+ 头部样例结果 + LLM 叙述"
            "分析。用户问『这次跑得怎么样 / 帮我看下这次评估 / 这个 run 的报告』时用它。"
        ),
        parameters={
            "run_id": "必填，运行 ID",
            "results_page_size": "可选，附带的样例结果条数，默认 5",
        },
        run=_run_report,
    ),
    "platform_overview": Skill(
        name="platform_overview",
        description=(
            "平台总览：数据集清单 + 最近评估运行 + 指标总览（延迟/成本/成功率）+ "
            "客户反馈总览。用户问『整体情况 / 平台概况 / 最近怎么样』时用它。"
        ),
        parameters={"days": "可选，指标回看天数"},
        run=_platform_overview,
    ),
}


def skills_catalog() -> str:
    """把技能清单渲染成给编排 LLM 的文本（与 tools_catalog 同构）。无技能则返回占位。"""
    if not SKILLS:
        return "（暂无预设技能）"
    lines: list[str] = []
    for s in SKILLS.values():
        params = ", ".join(f"{k}({v})" for k, v in s.parameters.items()) or "无"
        lines.append(f"- {s.name}: {s.description} 参数: {params}")
    return "\n".join(lines)
