"""Langfuse trace 指标计算（纯函数，无 I/O）。

输入 Langfuse 拉取到的 trace dict + 该 trace 的 observations 明细 list，输出可
直接 upsert 进 ``langfuse_trace_metrics``（见 db_models/tables.py 的
``LangfuseTraceMetricRow``）的指标 dict。

本模块**只做计算**：不碰 DB、不碰网络、不读环境。返回 dict 的键名与
``LangfuseTraceMetricRow`` 的列名一一对应，但**不含** tenant_id / id /
environment / trace_timestamp / 各时间戳列（那些由拉取层填）。每个内部 ``_``
前缀函数都设计成可独立单测。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone


def _parse_dt(s) -> datetime | None:
    """解析 ISO8601 时间串为 UTC aware datetime；失败/空返回 None。

    Langfuse 用 ``"2026-06-12T00:01:06.449Z"`` 这种结尾带 Z 的格式，
    ``datetime.fromisoformat`` 在旧版不认 Z，故先把结尾 Z 换成 ``+00:00``。
    解析出 naive datetime 时补上 UTC tzinfo。
    """
    if not s or not isinstance(s, str):
        return None
    try:
        text = s.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _content_blocks(output) -> list[dict]:
    """把 observation.output 归一成 content block 列表。

    output 可能形态：
      - ``{"content": [block, ...]}``  —— 取 content 列表
      - ``{"content": "字符串"}``      —— 字符串视为单个 text 块
      - 直接是 ``[block, ...]``         —— 本身就是块列表
      - message dict（含 type/text 等） —— 当成单个块
    无法解析一律返回 ``[]``。
    """
    if output is None:
        return []
    # 直接是列表：逐项保留 dict 块
    if isinstance(output, list):
        return [b for b in output if isinstance(b, dict)]
    if isinstance(output, dict):
        content = output.get("content")
        if isinstance(content, list):
            return [b for b in content if isinstance(b, dict)]
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        # 没有 content 字段但自身看起来就是一个块（带 type）
        if "type" in output:
            return [output]
        return []
    # 顶层就是字符串
    if isinstance(output, str):
        return [{"type": "text", "text": output}]
    return []


def _is_answer_generation(obs: dict) -> bool:
    """判断一个 GENERATION observation 是否「在产出最终答复」。

    取其 output 的 content blocks：有文本块（text.strip() 非空）且无 tool_use
    块时，视为答复生成；只要带 tool_use 就不算（那是在调工具而非作答）。
    """
    blocks = _content_blocks((obs or {}).get("output"))
    has_tool = any(b.get("type") == "tool_use" for b in blocks)
    has_text = any(
        b.get("type") == "text" and isinstance(b.get("text"), str) and b.get("text").strip()
        for b in blocks
    )
    return has_text and not has_tool


def _trace_start(trace: dict, observations: list[dict]) -> datetime | None:
    """trace 起点：所有 observation.startTime 的最小值；都缺则回退 trace.timestamp。"""
    starts = [
        dt
        for o in observations or []
        if (dt := _parse_dt((o or {}).get("startTime"))) is not None
    ]
    if starts:
        return min(starts)
    return _parse_dt((trace or {}).get("timestamp"))


def _sorted_by_start(observations: list[dict]) -> list[dict]:
    """按 startTime 升序排；解析失败的排到末尾。"""
    _max = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(
        observations or [],
        key=lambda o: (_parse_dt((o or {}).get("startTime")) or _max),
    )


def _sum_optional(observations: list[dict], key: str) -> int | None:
    """对 observations 的某 token 字段求和；全为 None 则返回 None。"""
    has_any = any((o or {}).get(key) is not None for o in observations or [])
    if not has_any:
        return None
    return sum((o or {}).get(key) or 0 for o in observations or [])


def compute_trace_metrics(trace: dict, observations: list[dict]) -> dict:
    """输入 Langfuse trace dict + 该 trace 的 observations 明细 list，
    输出可直接 upsert 进 langfuse_trace_metrics 的指标 dict（不含
    tenant_id/id/environment/trace_timestamp/时间戳列）。
    """
    trace = trace or {}
    obs = observations or []

    # —— token 汇总 ——
    input_tokens = _sum_optional(obs, "promptTokens")
    output_tokens = _sum_optional(obs, "completionTokens")
    total_tokens = _sum_optional(obs, "totalTokens")
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)

    # —— 成本：优先 trace.totalCost，否则累加各 observation 的算定成本 ——
    total_cost = trace.get("totalCost")
    if total_cost is None:
        has_obs_cost = any(
            (o or {}).get("calculatedTotalCost") is not None for o in obs
        )
        if has_obs_cost:
            total_cost = sum((o or {}).get("calculatedTotalCost") or 0 for o in obs)

    # —— 结构计数 ——
    observation_count = len(obs)
    generation_count = sum(1 for o in obs if (o or {}).get("type") == "GENERATION")
    tool_obs = [o for o in obs if (o or {}).get("type") == "TOOL"]
    tool_call_count = len(tool_obs)
    tool_error_count = sum(1 for o in tool_obs if (o or {}).get("level") == "ERROR")
    tool_success_count = tool_call_count - tool_error_count
    tool_success_rate = (
        round(tool_success_count / tool_call_count, 4) if tool_call_count > 0 else None
    )

    # tool_call_counts: {工具名: 次数}，空则 None
    name_counter = Counter((o or {}).get("name") for o in tool_obs)
    tool_call_counts = dict(name_counter) if name_counter else None

    has_error = any((o or {}).get("level") == "ERROR" for o in obs)

    # —— 时间类指标，统一相对 trace 起点 ——
    t0 = _trace_start(trace, obs)
    ordered = _sorted_by_start(obs)

    # 首工具调用时间
    first_tool_call_s = None
    if t0 is not None:
        for o in ordered:
            if (o or {}).get("type") == "TOOL":
                tool_start = _parse_dt((o or {}).get("startTime"))
                if tool_start is not None:
                    first_tool_call_s = round((tool_start - t0).total_seconds(), 4)
                break

    # 首思考 token 时间：第一个 GENERATION 的 offset + timeToFirstToken
    first_thinking_token_s = None
    if t0 is not None:
        for o in ordered:
            if (o or {}).get("type") == "GENERATION":
                gen_start = _parse_dt((o or {}).get("startTime"))
                if gen_start is not None:
                    offset = (gen_start - t0).total_seconds()
                    ttft = (o or {}).get("timeToFirstToken") or 0
                    first_thinking_token_s = round(offset + ttft, 4)
                break

    # 首答 token 时间：第一个满足 _is_answer_generation 的 GENERATION
    first_answer_token_s = None
    if t0 is not None:
        for o in ordered:
            if (o or {}).get("type") == "GENERATION" and _is_answer_generation(o):
                gen_start = _parse_dt((o or {}).get("startTime"))
                if gen_start is not None:
                    offset = (gen_start - t0).total_seconds()
                    ttft = (o or {}).get("timeToFirstToken") or 0
                    first_answer_token_s = round(offset + ttft, 4)
                break

    # —— latency ——
    latency = trace.get("latency")
    latency_s = round(latency, 4) if latency is not None else None

    return {
        "latency_s": latency_s,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 6) if total_cost is not None else None,
        "observation_count": observation_count,
        "generation_count": generation_count,
        "tool_call_count": tool_call_count,
        "tool_error_count": tool_error_count,
        "tool_success_count": tool_success_count,
        "tool_success_rate": tool_success_rate,
        "tool_call_counts": tool_call_counts,
        "has_error": has_error,
        "first_tool_call_s": first_tool_call_s,
        "first_thinking_token_s": first_thinking_token_s,
        "first_answer_token_s": first_answer_token_s,
        # 缓存三列占位，当前恒 None（trace 未上报缓存 token）
        "cache_read_tokens": None,
        "cache_creation_tokens": None,
        "cache_hit_rate": None,
    }
