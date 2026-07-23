"""空白回复跳过评估的回归测试（#89）。

真实叉车多轮双模对比 run c433dbaa 暴露：某轮 agent 回答空白时，judge 仍照常打分，
甚至因「空白无幻觉信号」把空白侧判为胜出 —— 无效评估、假分。

用户决策：
  * 某轮任一侧 assistant 空白 → 该轮跳过评估（scoped status="skipped"），
    不送 judge、不产 verdict、不计胜负。
  * 空白轮不计入分数聚合；但分侧统计 A/B 有效/空白回答数。
  * 双模、单模两条路径行为一致。

覆盖：双模四组合（A空白/B空白/两侧空白/两侧正常）、单模空白跳过、聚合器 skipped 计数。
mock 点沿用 test_comparative.py 的既有模式：_replay_one_side / run_comparative_judge /
pick_swap（双模）；multiturn.run_configurable_judge（单模）。
"""
from types import SimpleNamespace

import pytest

from agent_eval.evaluation import langfuse_runner, multiturn
from agent_eval.evaluation.comparative import build_comparison_summary


# ── 公共构件 ──

def _verdict(sa=0.8, sb=0.6, win="A", overall="A"):
    return {
        "dimensions": [
            {"name": "准确性", "score_a": sa, "score_b": sb, "winner": win, "reason": "r"}
        ],
        "overall_winner": overall,
        "reasoning": "综述",
    }


def _turn(idx, assistant, user="问题"):
    return {"turn_index": idx, "user": user, "assistant": assistant, "tool_calls": []}


def _spec():
    # provider 只要非 None 即可通过 _compare_once 的 provider 检查；judge 被 mock。
    return {
        "evaluator_id": "e1",
        "evaluator_version_id": "v1",
        "label": "幻觉率对比",
        "tag": "hallucination",
        "evaluator_type": "configurable_judge",
        "params": {},
        "_provider": SimpleNamespace(id="p1"),
    }


def _turn_exps(indices):
    return [
        {"turn_index": i, "criteria": ["c"], "expected_output": "", "expected_tool_calls": []}
        for i in indices
    ]


# ── 双模多轮：空白轮跳过 ──

async def _run_comparative(monkeypatch, a_turns, b_turns, turn_indices):
    """跑 _run_multiturn_comparative_case，返回 (result, judge_calls)。"""
    async def fake_replay(*, agent_cfg, case_name, **_kw):
        turns = a_turns if case_name.endswith("-A") else b_turns
        return {
            "turns": turns, "tool_calls": [], "steps": [],
            "latency_ms": 1.0, "usage": {}, "attempts": 1, "error": None,
        }

    judge_calls = []

    async def fake_judge(*, output_a, output_b, **_kw):
        judge_calls.append((output_a, output_b))
        return SimpleNamespace(verdict=_verdict(), error=None)

    monkeypatch.setattr(langfuse_runner, "_replay_one_side", fake_replay)
    monkeypatch.setattr(langfuse_runner, "run_comparative_judge", fake_judge)
    monkeypatch.setattr(langfuse_runner, "pick_swap", lambda: False)

    result = await langfuse_runner._run_multiturn_comparative_case(
        case={
            "id": "c1", "name": "case", "question": "q",
            "input_messages": [{"role": "user", "content": "问题"}],
            "turn_expectations": _turn_exps(turn_indices),
            "conversation_goal": None,
        },
        agent_cfg={"name": "A"},
        agent_cfg_b={"name": "B"},
        evaluator_specs=[_spec()],
    )
    return result, judge_calls


def _scoped(result):
    return result["comparison"]["evaluator_verdicts"][0]["scoped_verdicts"]


@pytest.mark.asyncio
async def test_comparative_a_blank_turn_skipped(monkeypatch):
    """A 侧某轮空白 → 该轮 skipped，不送 judge。"""
    a = [_turn(0, "有内容"), _turn(1, "   \n\t ")]
    b = [_turn(0, "有内容"), _turn(1, "B 有内容")]
    result, judge_calls = await _run_comparative(monkeypatch, a, b, [0, 1])

    scoped = _scoped(result)
    by_ti = {s["turn_index"]: s for s in scoped}
    assert by_ti[0]["status"] == "scored"
    assert by_ti[1]["status"] == "skipped"
    assert by_ti[1]["verdict"] is None
    # judge 只被有效轮（turn 0）调用一次
    assert len(judge_calls) == 1
    # 计数：A 有效 1 空白 1，B 有效 2 空白 0
    counts = result["comparison"]["answer_counts"]
    assert counts["a_valid"] == 1 and counts["a_blank"] == 1
    assert counts["b_valid"] == 2 and counts["b_blank"] == 0


@pytest.mark.asyncio
async def test_comparative_b_blank_turn_skipped(monkeypatch):
    a = [_turn(0, "A0"), _turn(1, "A1")]
    b = [_turn(0, "B0"), _turn(1, "")]
    result, judge_calls = await _run_comparative(monkeypatch, a, b, [0, 1])

    by_ti = {s["turn_index"]: s for s in _scoped(result)}
    assert by_ti[0]["status"] == "scored"
    assert by_ti[1]["status"] == "skipped"
    assert len(judge_calls) == 1
    counts = result["comparison"]["answer_counts"]
    assert counts["b_valid"] == 1 and counts["b_blank"] == 1


@pytest.mark.asyncio
async def test_comparative_both_blank_all_skipped(monkeypatch):
    """两侧全空白 → evaluator 全 skipped，样例 status=skipped，judge 零调用。"""
    a = [_turn(0, "  "), _turn(1, "")]
    b = [_turn(0, ""), _turn(1, "   ")]
    result, judge_calls = await _run_comparative(monkeypatch, a, b, [0, 1])

    assert len(judge_calls) == 0
    scoped = _scoped(result)
    assert all(s["status"] == "skipped" for s in scoped)
    # evaluator 与样例都判 skipped，而非 evaluation_error
    assert result["comparison"]["evaluator_verdicts"][0]["status"] == "skipped"
    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_comparative_both_valid_scored(monkeypatch):
    """两侧都有内容 → 正常 scored，无跳过。"""
    a = [_turn(0, "A0"), _turn(1, "A1")]
    b = [_turn(0, "B0"), _turn(1, "B1")]
    result, judge_calls = await _run_comparative(monkeypatch, a, b, [0, 1])

    assert len(judge_calls) == 2
    assert all(s["status"] == "scored" for s in _scoped(result))
    assert result["status"] == "scored"
    counts = result["comparison"]["answer_counts"]
    assert counts["a_valid"] == 2 and counts["b_valid"] == 2
    assert counts["a_blank"] == 0 and counts["b_blank"] == 0


# ── 单模多轮：空白轮跳过 ──

@pytest.mark.asyncio
async def test_single_mode_blank_turn_skipped(monkeypatch):
    """单模 score_conversation：空白轮不送 judge、不产 score。"""
    judge_calls = []

    async def fake_judge(*, output_text, evaluator_name, **_kw):
        judge_calls.append((evaluator_name, output_text))
        return SimpleNamespace(
            scores=[SimpleNamespace(value=0.8, reason="r", checks=None)],
            error=None,
        )

    monkeypatch.setattr(multiturn, "run_configurable_judge", fake_judge)

    turns = [_turn(0, "有内容"), _turn(1, "  \n ")]
    scores, reasons, checks, failed, last_err = await multiturn.score_conversation(
        turns=turns,
        conversation_goal=None,
        turn_expectations=_turn_exps([0, 1]),
        evaluator_specs=[_spec()],
        case_metadata=None,
        case_id="c1",
    )
    # 只有 turn 0 出分；turn 1 空白被跳过
    assert "幻觉率对比.turn0" in scores
    assert "幻觉率对比.turn1" not in scores
    assert len(judge_calls) == 1
    # 空白跳过不算 judge 失败
    assert failed == 0


# ── 聚合器：skipped 不进 evaluation_errors ──

def test_summary_skipped_not_counted_as_error():
    rows = [{
        "comparison": {
            "evaluator_verdicts": [{
                "evaluator_id": "e1", "evaluator_version_id": "v1",
                "label": "hall", "tag": "hall", "status": "scored",
                "scoped_verdicts": [
                    {"scope": "turn", "turn_index": 0, "status": "scored",
                     "verdict": _verdict(), "error": None},
                    {"scope": "turn", "turn_index": 1, "status": "skipped",
                     "verdict": None, "error": "A 侧该轮回复空白，跳过对比"},
                ],
            }],
        },
    }]
    summary = build_comparison_summary(rows)
    ev = summary["evaluators"][0]
    assert ev["scored"] == 1
    assert ev["evaluation_errors"] == 0
    assert ev["skipped"] == 1
