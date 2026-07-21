"""双模对比评估纯函数单测（纯 stdlib，无 I/O）。

覆盖 comparative.py 的位置随机化还原 + 对比汇总，以及 configurable_judge
的对比 verdict 解析。核心不变量：**位置随机化不影响真实 A/B 结论**——
无论 prompt 中 A/B 是否交换，还原后的 verdict 必须回到相同的真实视角。
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from agent_eval.evaluation import langfuse_runner
from agent_eval.evaluation.comparative import (
    build_comparison_summary,
    pick_swap,
    restore_verdict,
)
from agent_eval.evaluation.configurable_judge import _parse_comparison_result


# ── restore_verdict：位置随机化还原 ──

def _verdict(sa, sb, win, overall):
    return {
        "dimensions": [{"name": "准确性", "score_a": sa, "score_b": sb, "winner": win, "reason": "r"}],
        "overall_winner": overall,
        "reasoning": "全局",
    }


def test_restore_no_swap_is_identity():
    """未交换：verdict 已是真实视角，还原后不变。"""
    v = _verdict(0.8, 0.6, "A", "A")
    out = restore_verdict(v, swapped=False)
    d = out["dimensions"][0]
    assert d["score_a"] == 0.8 and d["score_b"] == 0.6
    assert d["winner"] == "A"
    assert out["overall_winner"] == "A"


def test_restore_swap_flips_ab():
    """交换：judge 眼中的 A 其实是真实 B → score_a↔score_b 互换、winner 翻转。"""
    # judge 按 slot 打分：slot_a 得 0.8 胜。但 swap 时 slot_a 装的是真实 B。
    v = _verdict(0.8, 0.6, "A", "A")
    out = restore_verdict(v, swapped=True)
    d = out["dimensions"][0]
    # 还原后：真实 A = slot_b = 0.6，真实 B = slot_a = 0.8，胜方 B。
    assert d["score_a"] == 0.6 and d["score_b"] == 0.8
    assert d["winner"] == "B"
    assert out["overall_winner"] == "B"


def test_restore_swap_tie_unchanged():
    """tie 在交换下保持 tie。"""
    v = _verdict(0.5, 0.5, "tie", "tie")
    out = restore_verdict(v, swapped=True)
    assert out["dimensions"][0]["winner"] == "tie"
    assert out["overall_winner"] == "tie"


def test_restore_swap_is_involution():
    """交换两次 = 不交换（对合性）：还原逻辑自洽。"""
    v = _verdict(0.9, 0.3, "A", "A")
    once = restore_verdict(v, swapped=True)
    twice = restore_verdict(once, swapped=True)
    d = twice["dimensions"][0]
    assert d["score_a"] == 0.9 and d["score_b"] == 0.3
    assert d["winner"] == "A"


def test_position_randomization_invariant():
    """核心不变量：真实 A 强于 B 时，无论 swap 与否，还原后真实结论一致。

    模拟一个无位置偏见的完美 judge：总把 slot 里更好的回复判胜。真实 A=0.8
    恒强于真实 B=0.5。swap 时 judge 眼中 slot_a=真实B、slot_b=真实A。
    """
    real_a, real_b = 0.8, 0.5
    for swap in (False, True):
        # judge 看到的 slot 顺序
        slot_a = real_b if swap else real_a
        slot_b = real_a if swap else real_b
        # 完美 judge：给每个 slot 打其真值，胜方是分高的 slot
        slot_win = "A" if slot_a > slot_b else "B" if slot_b > slot_a else "tie"
        judge_verdict = _verdict(slot_a, slot_b, slot_win, slot_win)
        restored = restore_verdict(judge_verdict, swapped=swap)
        d = restored["dimensions"][0]
        # 还原后必须回到真实视角
        assert d["score_a"] == real_a, f"swap={swap}"
        assert d["score_b"] == real_b, f"swap={swap}"
        assert d["winner"] == "A", f"swap={swap}"
        assert restored["overall_winner"] == "A", f"swap={swap}"


def test_pick_swap_deterministic_with_seed():
    r1 = random.Random(42)
    r2 = random.Random(42)
    assert [pick_swap(r1) for _ in range(10)] == [pick_swap(r2) for _ in range(10)]


# ── _parse_comparison_result：verdict 解析 ──

def test_parse_dimensions_array():
    body = {
        "dimensions": [
            {"name": "准确性", "score_a": 0.8, "score_b": 0.6, "winner": "A", "reason": "a 更准"},
            {"name": "完整性", "score_a": 0.5, "score_b": 0.9, "winner": "B", "reason": "b 更全"},
        ],
        "overall_winner": "A",
        "reasoning": "综合 A 略胜",
    }
    verdict, err = _parse_comparison_result(body, score_range=None)
    assert err is None
    assert len(verdict["dimensions"]) == 2
    assert verdict["dimensions"][0]["winner"] == "A"
    assert verdict["overall_winner"] == "A"


def test_parse_flat_single_dimension():
    """单维度扁平结构（顶层 score_a/score_b）也能解析。"""
    body = {"score_a": 0.7, "score_b": 0.4, "winner": "A", "reasoning": "r"}
    verdict, err = _parse_comparison_result(body, score_range=None)
    assert err is None
    assert len(verdict["dimensions"]) == 1
    assert verdict["dimensions"][0]["score_a"] == 0.7
    assert verdict["overall_winner"] == "A"


def test_parse_missing_overall_uses_majority_vote():
    """未给 overall_winner 时按各维度 winner 多数投票。"""
    body = {
        "dimensions": [
            {"name": "d1", "score_a": 0.8, "score_b": 0.6, "winner": "A"},
            {"name": "d2", "score_a": 0.7, "score_b": 0.5, "winner": "A"},
            {"name": "d3", "score_a": 0.3, "score_b": 0.9, "winner": "B"},
        ],
    }
    verdict, err = _parse_comparison_result(body, score_range=None)
    assert err is None
    assert verdict["overall_winner"] == "A"  # 2A vs 1B


def test_parse_score_range_normalizes():
    """score_range=[0,10] 时分数归一到 [0,1]。"""
    body = {"score_a": 8, "score_b": 4, "winner": "A"}
    verdict, err = _parse_comparison_result(body, score_range=[0, 10])
    assert err is None
    d = verdict["dimensions"][0]
    assert d["score_a"] == 0.8 and d["score_b"] == 0.4


def test_parse_no_dimensions_errors():
    verdict, err = _parse_comparison_result({"foo": "bar"}, score_range=None)
    assert verdict is None
    assert err is not None


# ── build_comparison_summary：run 级汇总 ──

def _row(overall, dims):
    return {"comparison": {"verdict": {"dimensions": dims, "overall_winner": overall}}}


def test_summary_counts_wins():
    rows = [
        _row("A", [{"name": "准确性", "score_a": 0.8, "score_b": 0.6, "winner": "A"}]),
        _row("A", [{"name": "准确性", "score_a": 0.7, "score_b": 0.5, "winner": "A"}]),
        _row("B", [{"name": "准确性", "score_a": 0.4, "score_b": 0.9, "winner": "B"}]),
        _row("tie", [{"name": "准确性", "score_a": 0.5, "score_b": 0.5, "winner": "tie"}]),
    ]
    s = build_comparison_summary(rows)
    assert s["total"] == 4
    assert s["a_wins"] == 2
    assert s["b_wins"] == 1
    assert s["ties"] == 1
    dim = s["per_dimension"]["准确性"]
    assert dim["a_wins"] == 2 and dim["b_wins"] == 1 and dim["ties"] == 1
    assert dim["n"] == 4
    # mean_a = (0.8+0.7+0.4+0.5)/4 = 0.6
    assert dim["mean_a"] == 0.6


def test_summary_ignores_rows_without_verdict():
    rows = [
        _row("A", [{"name": "d", "score_a": 0.8, "score_b": 0.6, "winner": "A"}]),
        {"comparison": {"verdict": None}},   # 执行/评分失败的对比行
        {"actual_output": "single-mode row, no comparison"},
    ]
    s = build_comparison_summary(rows)
    assert s["total"] == 1
    assert s["a_wins"] == 1


def _evaluator_entry(
    evaluator_id: str,
    version_id: str,
    label: str,
    *,
    status: str = "scored",
    verdict: dict | None = None,
    error: str | None = None,
):
    return {
        "evaluator_id": evaluator_id,
        "evaluator_version_id": version_id,
        "label": label,
        "tag": f"tag-{label}",
        "status": status,
        "verdict": verdict,
        "error": error,
    }


def test_summary_groups_same_dimension_by_evaluator_and_counts_errors():
    first = _verdict(0.9, 0.2, "A", "A")
    second = _verdict(0.3, 0.8, "B", "B")
    rows = [
        {"comparison": {"evaluator_verdicts": [
            _evaluator_entry("e1", "v1", "correctness", verdict=first),
            _evaluator_entry("e2", "v2", "style", verdict=second),
        ]}},
        {"comparison": {"evaluator_verdicts": [
            _evaluator_entry("e1", "v1", "correctness", verdict=first),
            _evaluator_entry(
                "e2", "v2", "style", status="evaluation_error",
                error="judge unavailable",
            ),
        ]}},
    ]

    summary = build_comparison_summary(rows)
    assert "total" not in summary  # 多组不能伪装成一个旧顶层总裁决
    assert len(summary["evaluators"]) == 2
    by_key = {item["evaluator_key"]: item for item in summary["evaluators"]}
    assert by_key["v1"]["scored"] == 2
    assert by_key["v1"]["per_dimension"]["准确性"]["mean_a"] == 0.9
    assert by_key["v2"]["scored"] == 1
    assert by_key["v2"]["evaluation_errors"] == 1
    assert by_key["v2"]["per_dimension"]["准确性"]["mean_a"] == 0.3


def test_summary_legacy_verdict_is_explicit_and_keeps_top_level_compatibility():
    summary = build_comparison_summary([
        _row("A", [{"name": "d", "score_a": 0.8, "score_b": 0.2, "winner": "A"}]),
    ])

    assert summary["total"] == 1
    assert summary["a_wins"] == 1
    assert len(summary["evaluators"]) == 1
    legacy = summary["evaluators"][0]
    assert legacy["evaluator_key"] == "legacy"
    assert legacy["legacy"] is True
    assert legacy["label"] == "legacy"


def _agent_result(name: str) -> dict:
    is_b = name == "B"
    return {
        "output_text": f"answer-{name}",
        "tool_calls": [{"name": f"tool-{name}"}],
        "cot_steps": [],
        "latency_ms": 200 if is_b else 100,
        "first_thinking_token_ms": 20 if is_b else 10,
        "first_answer_token_ms": 40 if is_b else 30,
        "usage": {
            "prompt_tokens": 22 if is_b else 11,
            "completion_tokens": 8 if is_b else 7,
            "total_tokens": 30 if is_b else 18,
            "cache_creation_tokens": 4 if is_b else 2,
            "cache_read_tokens": 6 if is_b else 3,
        },
        "error_message": None,
        "error_type": None,
        "attempts_made": 2 if is_b else 1,
        "thread_id": f"thread-{name}",
    }


@pytest.mark.asyncio
async def test_runner_preserves_two_evaluators_restores_each_and_agent_b_metrics(monkeypatch):
    async def fake_invoke_one_agent(*, agent_cfg, **_kwargs):
        return _agent_result(agent_cfg["name"])

    verdicts = [
        _verdict(0.9, 0.1, "A", "A"),
        _verdict(0.7, 0.2, "A", "A"),
    ]

    async def fake_comparative_judge(**_kwargs):
        return SimpleNamespace(verdict=verdicts.pop(0), error=None)

    monkeypatch.setattr(langfuse_runner, "_invoke_one_agent", fake_invoke_one_agent)
    monkeypatch.setattr(langfuse_runner, "run_comparative_judge", fake_comparative_judge)
    monkeypatch.setattr(langfuse_runner, "pick_swap", lambda: True)

    specs = [
        {"id": "e1", "evaluator_version_id": "v1", "label": "one", "tag": "t1",
         "evaluator_type": "configurable_judge", "params": {"provider_id": "p1"},
         "_provider": object()},
        {"id": "e2", "evaluator_version_id": "v2", "label": "two", "tag": "t2",
         "evaluator_type": "configurable_judge", "params": {"provider_id": "p2"},
         "_provider": object()},
    ]
    result = await langfuse_runner._run_comparative_case(
        case={"id": "c1", "name": "case", "question": "q"},
        agent_cfg={"name": "A"},
        agent_cfg_b={"name": "B"},
        evaluator_specs=specs,
    )

    assert result["status"] == "scored"
    entries = result["comparison"]["evaluator_verdicts"]
    assert [(e["evaluator_id"], e["evaluator_version_id"]) for e in entries] == [
        ("e1", "v1"), ("e2", "v2"),
    ]
    assert [e["verdict"]["overall_winner"] for e in entries] == ["B", "B"]
    assert [e["verdict"]["dimensions"][0]["score_a"] for e in entries] == [0.1, 0.2]
    assert result["comparison"]["verdict"] == entries[-1]["verdict"]
    agent_b = result["comparison"]["agent_b"]
    assert agent_b["first_thinking_token_ms"] == 20
    assert agent_b["first_answer_token_ms"] == 40
    assert agent_b["cache_creation_tokens"] == 4
    assert agent_b["cache_read_tokens"] == 6
    assert agent_b["attempts_made"] == 2


@pytest.mark.asyncio
async def test_runner_partial_judge_failure_keeps_successful_verdict(monkeypatch):
    async def fake_invoke_one_agent(*, agent_cfg, **_kwargs):
        return _agent_result(agent_cfg["name"])

    calls = 0

    async def fake_comparative_judge(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(verdict=_verdict(0.8, 0.4, "A", "A"), error=None)
        return SimpleNamespace(verdict=None, error="provider read failed")

    monkeypatch.setattr(langfuse_runner, "_invoke_one_agent", fake_invoke_one_agent)
    monkeypatch.setattr(langfuse_runner, "run_comparative_judge", fake_comparative_judge)
    monkeypatch.setattr(langfuse_runner, "pick_swap", lambda: False)

    result = await langfuse_runner._run_comparative_case(
        case={"id": "c1", "name": "case", "question": "q"},
        agent_cfg={"name": "A"},
        agent_cfg_b={"name": "B"},
        evaluator_specs=[
            {"id": "e1", "label": "ok", "evaluator_type": "configurable_judge",
             "params": {"provider_id": "p1"}, "_provider": object()},
            {"id": "e2", "label": "bad", "evaluator_type": "configurable_judge",
             "params": {"provider_id": "p2"}, "_provider": object()},
        ],
    )

    assert result["status"] == "evaluation_error"
    assert result["error_type"] == "judge_error"
    entries = result["comparison"]["evaluator_verdicts"]
    assert entries[0]["status"] == "scored"
    assert entries[0]["verdict"]["overall_winner"] == "A"
    assert entries[1]["status"] == "evaluation_error"
    assert "provider read failed" in entries[1]["error"]
    assert result["comparison"]["verdict"] == entries[0]["verdict"]


class _ProviderRepo:
    def __init__(self, provider):
        self.provider = provider

    async def get_evaluator_provider(self, _provider_id):
        return self.provider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("spec", "provider", "message"),
    [
        ({"label": "builtin", "evaluator_type": "exact_match", "params": {}}, None,
         "must use configurable_judge"),
        ({"label": "missing", "evaluator_type": "configurable_judge", "params": {}}, None,
         "missing provider_id"),
        ({"label": "gone", "evaluator_type": "configurable_judge",
          "params": {"provider_id": "11111111-1111-1111-1111-111111111111"}}, None,
         "provider not found"),
        ({"label": "off", "evaluator_type": "configurable_judge",
          "params": {"provider_id": "11111111-1111-1111-1111-111111111111"}},
         SimpleNamespace(is_active=False), "provider is inactive"),
    ],
)
async def test_comparative_start_validation_rejects_invalid_evaluators(spec, provider, message):
    with pytest.raises(ValueError, match=message):
        await langfuse_runner._validate_comparative_evaluator_specs(
            [spec], _ProviderRepo(provider),
        )


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"TOTAL {len(fns)} FAILS {failed}")
    sys.exit(1 if failed else 0)
