"""评估三层语义（执行事实 / 评分事实 / 显式验收）的确定性回归。

只依赖 ``agent_eval.evaluation.acceptance``（纯 stdlib，无 fastapi/httpx），
因此在最小环境和容器里都能被 pytest 收集执行。核心不变量：
- 未配置 acceptance_policy 时**绝不**产生通过率 / 达标结论；
- 配置后 pass_rate = 通过 / 已决策，未决策不摊进分母；
- Agent 执行异常与 Judge 评分异常互斥计数，不混为一个 status。
"""
from __future__ import annotations

import math

import pytest

from agent_eval.evaluation.acceptance import (
    aggregate_semantics,
    project_case,
    project_stored_summary,
    reduce_dimension_score,
    validate_acceptance_policy,
)


POLICY = {
    "version": 1,
    "mode": "threshold",
    "case_rule": "all",
    "criteria": [{
        "evaluator_id": "quality-evaluator",
        "dimension_key": "quality",
        "direction": "higher_better",
        "threshold": 0.5,
        "reducer": "conversation_or_mean",
    }],
    "run_rule": {"min_case_pass_rate": 0.5, "min_decision_coverage": 1.0},
}


# ── validate_acceptance_policy ────────────────────────────────────────

def test_validate_none_means_scoring_only():
    assert validate_acceptance_policy(None) is None


def test_validate_accepts_wellformed_policy():
    out = validate_acceptance_policy(POLICY)
    assert out is not None
    assert out["criteria"][0]["threshold"] == 0.5


@pytest.mark.parametrize("bad", [
    {"version": 2, "mode": "threshold", "case_rule": "all",
     "criteria": [{"evaluator_id": "e", "dimension_key": "d",
                   "direction": "higher_better", "threshold": 0.5}],
     "run_rule": {"min_case_pass_rate": 0.5, "min_decision_coverage": 1.0}},
    {"version": 1, "mode": "threshold", "case_rule": "all",
     "criteria": [], "run_rule": {"min_case_pass_rate": 0.5, "min_decision_coverage": 1.0}},
])
def test_validate_rejects_bad_policy(bad):
    with pytest.raises(ValueError):
        validate_acceptance_policy(bad)


# ── reduce_dimension_score ────────────────────────────────────────────

def test_reduce_prefers_exact_then_conversation_then_turn_mean():
    assert reduce_dimension_score({"quality": 0.7}, "quality") == 0.7
    assert reduce_dimension_score({"quality.conversation": 0.6}, "quality") == 0.6
    assert reduce_dimension_score(
        {"quality.turn0": 0.2, "quality.turn1": 0.8}, "quality"
    ) == pytest.approx(0.5)
    assert reduce_dimension_score({"other": 1.0}, "quality") is None


# ── project_case ──────────────────────────────────────────────────────

def test_project_case_without_policy_has_no_acceptance():
    p = project_case(stored_status="scored", error_type=None,
                     scores={"quality": 0.9})
    assert p["execution_status"] == "success"
    assert p["evaluation_status"] == "completed"
    assert p["acceptance_decision"] is None
    assert p["criterion_results"] == []


def test_project_case_with_policy_thresholds():
    passed = project_case(stored_status="scored", error_type=None,
                          scores={"quality": 0.9}, acceptance_policy=POLICY)
    assert passed["acceptance_decision"] == "pass"

    failed = project_case(stored_status="scored", error_type=None,
                          scores={"quality": 0.2}, acceptance_policy=POLICY)
    assert failed["acceptance_decision"] == "fail"


def test_project_case_execution_abnormal_blocks_acceptance():
    p = project_case(stored_status="agent_unreachable", error_type=None,
                     scores={}, acceptance_policy=POLICY)
    assert p["execution_status"] == "abnormal"
    assert p["evaluation_status"] == "not_run"
    assert p["acceptance_decision"] == "undetermined"


def test_project_case_judge_error_is_evaluation_error_not_execution():
    # judge 挂了不等于 Agent 执行异常：不得计入 execution=abnormal，
    # 且必须在评分层暴露为 error。无分数时执行层无法证明成功 → unknown（保守，非 abnormal）。
    p = project_case(stored_status="error", error_type="judge_error", scores={})
    assert p["execution_status"] != "abnormal"
    assert p["evaluation_status"] == "error"
    # 有分数在手时，执行层可确证成功，评分层仍报 judge error。
    p2 = project_case(stored_status="error", error_type="judge_error", scores={"quality": 0.8})
    assert p2["execution_status"] == "success"
    assert p2["evaluation_status"] == "error"


# ── aggregate_semantics ───────────────────────────────────────────────

def _cases():
    return [
        {"status": "scored", "scores": {"quality": 0.9}},
        {"status": "scored", "scores": {"quality": 0.2}},
        {"status": "agent_unreachable", "scores": {}},
    ]


def test_aggregate_without_policy_reports_facts_but_no_pass_rate():
    agg = aggregate_semantics(_cases())
    facts = agg["facts"]
    assert facts["total"] == 3
    assert facts["execution_success"] == 2
    assert facts["execution_abnormal"] == 1
    assert facts["evaluation_completed"] == 2
    acc = agg["acceptance"]
    assert acc["configured"] is False
    assert acc["pass_rate"] is None
    assert acc["run_decision"] is None


def test_aggregate_with_policy_pass_rate_excludes_undetermined():
    agg = aggregate_semantics(_cases(), POLICY)
    acc = agg["acceptance"]
    assert acc["configured"] is True
    assert acc["passed"] == 1
    assert acc["failed"] == 1
    assert acc["undetermined"] == 1  # 执行异常样例
    assert acc["decided"] == 2
    assert acc["pass_rate"] == 0.5
    # coverage = 2/3 < min_decision_coverage(1.0) → undetermined
    assert acc["run_decision"] == "undetermined"


def test_aggregate_full_coverage_qualifies():
    cases = [
        {"status": "scored", "scores": {"quality": 0.8}},
        {"status": "scored", "scores": {"quality": 0.6}},
    ]
    acc = aggregate_semantics(cases, POLICY)["acceptance"]
    assert acc["decision_coverage"] == 1.0
    assert acc["run_decision"] == "qualified"


# ── project_stored_summary（历史兼容） ────────────────────────────────

def test_project_stored_summary_backfills_without_policy():
    # 老 run 只有 counts + 分布，无 facts/acceptance。
    legacy = {
        "counts": {"total": 4, "skipped": 1, "unreachable": 1},
        "score_distribution": {
            "buckets": ["0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1"],
            "by_dimension": {"quality": [0, 0, 1, 1, 0]},
        },
    }
    out = project_stored_summary(legacy)
    assert out["facts"]["total"] == 4
    assert out["facts"]["execution_abnormal"] == 1  # unreachable
    assert out["acceptance"]["configured"] is False
    assert out["acceptance"]["pass_rate"] is None


def test_project_stored_summary_idempotent_when_already_projected():
    already = {"facts": {"total": 2}, "acceptance": {"configured": False}}
    out = project_stored_summary(already)
    assert out["facts"]["total"] == 2
    assert out["acceptance"]["configured"] is False


def test_run_rule_bounds_are_validated():
    bad = dict(POLICY)
    bad = {**POLICY, "run_rule": {"min_case_pass_rate": 1.5, "min_decision_coverage": 1.0}}
    with pytest.raises(ValueError):
        validate_acceptance_policy(bad)
    assert math.isfinite(0.5)  # sanity
