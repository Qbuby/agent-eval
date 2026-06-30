"""Langfuse 指标计算纯函数单测（无 DB / 无网络依赖）。

被测模块：``agent_eval.langfuse_metrics.compute``。

导入约定：项目其余测试直接 ``from agent_eval...`` 导入（依赖已安装/可
editable 的包）。为在未安装包的宿主上也能运行，这里额外把仓库的 ``src``
目录插入 ``sys.path`` 作为兜底，二者择一可用即可。
"""

from __future__ import annotations

import sys
from pathlib import Path

# —— 兜底：把 <repo>/src 加入 sys.path，便于未安装包时直接 import ——
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest

from agent_eval.langfuse_metrics.compute import (
    _content_blocks,
    _is_answer_generation,
    compute_trace_metrics,
)


# Langfuse 真实时间格式：结尾带 Z 的 ISO8601
def _t(sec_offset: float) -> str:
    """以 2026-06-12T00:00:00.000Z 为基准，构造偏移 sec_offset 秒的时间串。"""
    base_ms = 0  # 00:00:00.000
    total_ms = round((base_ms / 1000.0 + sec_offset) * 1000)
    s, ms = divmod(total_ms, 1000)
    mm, ss = divmod(s, 60)
    return f"2026-06-12T00:{mm:02d}:{ss:02d}.{ms:03d}Z"


T0 = "2026-06-12T00:00:00.000Z"


def _gen(start, *, output=None, ttft=None, **extra):
    o = {"type": "GENERATION", "startTime": start}
    if output is not None:
        o["output"] = output
    if ttft is not None:
        o["timeToFirstToken"] = ttft
    o.update(extra)
    return o


def _tool(start, *, name="some_tool", level=None, **extra):
    o = {"type": "TOOL", "startTime": start, "name": name}
    if level is not None:
        o["level"] = level
    o.update(extra)
    return o


def _text_output(text):
    return {"content": [{"type": "text", "text": text}]}


def _tool_use_output():
    return {"content": [{"type": "tool_use", "name": "do_thing", "input": {}}]}


def test_first_thinking_vs_answer_distinct():
    # GEN_A 较早，带 tool_use（非答复），ttft=2.0；GEN_B 较晚，纯 text，ttft=1.5
    gen_a = _gen(_t(0.0), output=_tool_use_output(), ttft=2.0)
    gen_b = _gen(_t(3.0), output=_text_output("最终答复"), ttft=1.5)
    # 故意乱序传入，验证内部按 startTime 排序
    m = compute_trace_metrics({}, [gen_b, gen_a])

    # t0 = 最早 observation start = GEN_A start = 00:00:00
    # first_thinking 基于最早 GENERATION (GEN_A): offset 0 + 2.0 = 2.0
    assert m["first_thinking_token_s"] == pytest.approx(2.0)
    # first_answer 基于首个纯 text GENERATION (GEN_B): offset 3.0 + 1.5 = 4.5
    assert m["first_answer_token_s"] == pytest.approx(4.5)
    # 两者必须不同
    assert m["first_thinking_token_s"] != m["first_answer_token_s"]
    assert m["generation_count"] == 2


def test_no_generation():
    # 只有一个 SPAN（确保 t0 存在），无 GENERATION
    span = {"type": "SPAN", "startTime": _t(0.0)}
    m = compute_trace_metrics({}, [span])
    assert m["first_thinking_token_s"] is None
    assert m["first_answer_token_s"] is None
    assert m["generation_count"] == 0


def test_no_tool():
    gen = _gen(_t(0.0), output=_text_output("hi"), ttft=0.5)
    m = compute_trace_metrics({}, [gen])
    assert m["first_tool_call_s"] is None
    assert m["tool_call_count"] == 0
    assert m["tool_success_rate"] is None
    assert m["tool_call_counts"] is None


def test_tool_success_rate():
    tools = [
        _tool(_t(0.1), name="a"),
        _tool(_t(0.2), name="b", level="ERROR"),
        _tool(_t(0.3), name="c"),
    ]
    m = compute_trace_metrics({}, tools)
    assert m["tool_call_count"] == 3
    assert m["tool_error_count"] == 1
    assert m["tool_success_count"] == 2
    assert m["tool_success_rate"] == pytest.approx(round(2 / 3, 4))
    assert m["has_error"] is True


def test_first_tool_call_offset():
    # 用一个 00:00:00 的 SPAN 锁定 trace 起点，TOOL 晚 0.4s
    span = {"type": "SPAN", "startTime": _t(0.0)}
    tool = _tool(_t(0.4))
    m = compute_trace_metrics({"timestamp": T0}, [span, tool])
    assert m["first_tool_call_s"] == pytest.approx(0.4)


def test_tokens_aggregation():
    obs = [
        {"type": "GENERATION", "startTime": _t(0.0),
         "promptTokens": 10, "completionTokens": 5, "totalTokens": 15},
        {"type": "GENERATION", "startTime": _t(1.0),
         "promptTokens": 20, "completionTokens": 7, "totalTokens": 27},
    ]
    m = compute_trace_metrics({}, obs)
    assert m["input_tokens"] == 30
    assert m["output_tokens"] == 12
    assert m["total_tokens"] == 42

    # 全 None → 对应字段 None
    obs_none = [
        {"type": "GENERATION", "startTime": _t(0.0)},
        {"type": "GENERATION", "startTime": _t(1.0)},
    ]
    m2 = compute_trace_metrics({}, obs_none)
    assert m2["input_tokens"] is None
    assert m2["output_tokens"] is None
    assert m2["total_tokens"] is None


def test_total_cost_fallback():
    obs = [
        {"type": "GENERATION", "startTime": _t(0.0), "calculatedTotalCost": 0.001},
        {"type": "GENERATION", "startTime": _t(1.0), "calculatedTotalCost": 0.002},
    ]
    # trace.totalCost 为 None → 回退累加 observation.calculatedTotalCost
    m = compute_trace_metrics({"totalCost": None}, obs)
    assert m["total_cost"] == pytest.approx(0.003)

    # trace.totalCost 有值时优先取它
    m2 = compute_trace_metrics({"totalCost": 9.5}, obs)
    assert m2["total_cost"] == pytest.approx(9.5)


def test_cache_fields_none():
    obs = [{"type": "GENERATION", "startTime": _t(0.0),
            "promptTokens": 10, "completionTokens": 5}]
    m = compute_trace_metrics({}, obs)
    assert m["cache_read_tokens"] is None
    assert m["cache_creation_tokens"] is None
    assert m["cache_hit_rate"] is None


def test_content_blocks_variants():
    # {"content": [...]} → 取列表（过滤非 dict）
    blocks = _content_blocks({"content": [{"type": "text", "text": "x"}, "junk"]})
    assert blocks == [{"type": "text", "text": "x"}]

    # {"content": "str"} → 单个 text 块
    assert _content_blocks({"content": "hello"}) == [{"type": "text", "text": "hello"}]

    # 直接 list → 过滤保留 dict 块
    direct = _content_blocks([{"type": "text", "text": "a"}, 123, {"type": "tool_use"}])
    assert direct == [{"type": "text", "text": "a"}, {"type": "tool_use"}]

    # None → []
    assert _content_blocks(None) == []

    # 无法解析（非 str/dict/list，如 int）→ []
    assert _content_blocks(123) == []


def test_is_answer_generation():
    # 含 tool_use → False
    assert _is_answer_generation({"output": _tool_use_output()}) is False
    # 纯 text → True
    assert _is_answer_generation({"output": _text_output("答复内容")}) is True
    # 空 output → False
    assert _is_answer_generation({"output": None}) is False
    assert _is_answer_generation({}) is False
