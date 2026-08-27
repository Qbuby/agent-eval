"""补评对比模式（rescore）的回归。

覆盖：对比 run 里某 scoped verdict 因 provider 波动记 evaluation_error 时，
补评应复用已存 A/B 回答，用对比 judge 重打该条、原地回写 comparison，
并把恢复计数返回。不重跑 agent、不写 EvaluationScoreRow。

mock 掉 run_comparative_judge / pick_swap，不起真 LLM。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_eval.evaluation import langfuse_runner


def _spec(label="幻觉率对比", provider=object()):
    return {
        "label": label,
        "tag": label,
        "evaluator_type": "configurable_judge",
        "params": {},
        "_provider": provider,
    }


def _comparison_with_failed_turn():
    """一个 evaluator，逐轮：turn0 已 scored，turn1 evaluation_error（待补）。"""
    return {
        "multi_turn": True,
        "position_swapped": False,
        "agent_b": {
            "output": "",
            "conversation": {"turns": [
                {"turn_index": 0, "user": "u0", "assistant": "b0", "tool_calls": []},
                {"turn_index": 1, "user": "u1", "assistant": "b1", "tool_calls": []},
            ]},
        },
        "evaluator_verdicts": [{
            "label": "幻觉率对比",
            "tag": "幻觉率对比",
            "status": "evaluation_error",
            "verdict": None,
            "error": "turn1: provider read failed",
            "scoped_verdicts": [
                {"scope": "turn", "turn_index": 0, "status": "scored",
                 "verdict": {"dimensions": [{"name": "维度", "score_a": 0.8,
                             "score_b": 0.6, "winner": "A"}],
                             "overall_winner": "A", "reasoning": "r0"}},
                {"scope": "turn", "turn_index": 1, "status": "evaluation_error",
                 "verdict": None, "error": "provider read failed"},
            ],
        }],
    }


def _full_trace():
    """A 侧逐轮 + turn_expectations（补评据此重建 judge 输入）。"""
    return {
        "conversation": {
            "turns": [
                {"turn_index": 0, "user": "u0", "assistant": "a0", "tool_calls": []},
                {"turn_index": 1, "user": "u1", "assistant": "a1", "tool_calls": []},
            ],
            "goal": None,
            "turn_expectations": [
                {"turn_index": 0, "criteria": ["c0"], "expected_output": "",
                 "expected_tool_calls": []},
                {"turn_index": 1, "criteria": ["c1"], "expected_output": "",
                 "expected_tool_calls": []},
            ],
        },
    }


@pytest.mark.asyncio
async def test_rescore_comparison_recovers_failed_scoped_verdict(monkeypatch):
    async def fake_judge(**_kw):
        return SimpleNamespace(
            verdict={
                "dimensions": [{"name": "维度", "score_a": 0.7, "score_b": 0.9, "winner": "B"}],
                "overall_winner": "B", "reasoning": "补评结论",
            },
            error=None,
        )

    monkeypatch.setattr(langfuse_runner, "run_comparative_judge", fake_judge)
    monkeypatch.setattr(langfuse_runner, "pick_swap", lambda: False)

    comparison = _comparison_with_failed_turn()
    recovered, still_missing, errors = await langfuse_runner._rescore_comparison_result(
        comparison=comparison,
        judge_specs=[_spec()],
        full_trace=_full_trace(),
        result_id="r1",
    )

    assert recovered == 1
    assert still_missing is False
    assert errors == []
    # 原地回写：turn1 从 evaluation_error → scored，带上补评 verdict。
    scoped = comparison["evaluator_verdicts"][0]["scoped_verdicts"]
    t1 = next(s for s in scoped if s.get("turn_index") == 1)
    assert t1["status"] == "scored"
    assert t1["verdict"]["overall_winner"] == "B"
    # turn0 原本已 scored，不动。
    t0 = next(s for s in scoped if s.get("turn_index") == 0)
    assert t0["verdict"]["overall_winner"] == "A"
    # evaluator 顶层因不再有 error 且有 scored → scored。
    assert comparison["evaluator_verdicts"][0]["status"] == "scored"


@pytest.mark.asyncio
async def test_rescore_comparison_still_failing_stays_error(monkeypatch):
    async def fake_judge(**_kw):
        return SimpleNamespace(verdict=None, error="provider still down")

    monkeypatch.setattr(langfuse_runner, "run_comparative_judge", fake_judge)
    monkeypatch.setattr(langfuse_runner, "pick_swap", lambda: False)

    comparison = _comparison_with_failed_turn()
    recovered, still_missing, errors = await langfuse_runner._rescore_comparison_result(
        comparison=comparison,
        judge_specs=[_spec()],
        full_trace=_full_trace(),
        result_id="r1",
    )

    assert recovered == 0
    assert still_missing is True
    # errors 透出可操作原因：维度名 + 去掉维度前缀的错误。
    assert len(errors) == 1
    assert errors[0]["dimension"].endswith(".turn1")
    assert errors[0]["error"] == "provider still down"
    scoped = comparison["evaluator_verdicts"][0]["scoped_verdicts"]
    t1 = next(s for s in scoped if s.get("turn_index") == 1)
    assert t1["status"] == "evaluation_error"
