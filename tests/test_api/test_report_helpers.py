"""评估报告统一语义的定向回归测试。"""
from __future__ import annotations

import sys
from types import ModuleType

import pytest

from agent_eval.api.routers.evaluation import _sample_mean_score, _subset_run_summary
from agent_eval.feishu.report_llm import (
    _rule_based_compare,
    _rule_based_summary,
    generate_compare_report,
)


_POLICY = {
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
    "run_rule": {
        "min_case_pass_rate": 0.5,
        "min_decision_coverage": 1.0,
    },
}


def _matrix() -> list[dict]:
    return [
        {
            "对齐键": "case-1",
            "run-a::status": "scored",
            "run-a::execution_status": "success",
            "run-a::evaluation_status": "completed",
            "run-a::quality.turn0": 0.4,
            "run-a::quality.turn1": 0.8,
            "run-a::latency_ms": 100,
            "run-a::total_tokens": 20,
            "run-a::prompt_tokens": 12,
            "run-a::completion_tokens": 8,
            "run-a::tool_call_count": 2,
        },
        {
            "对齐键": "case-2",
            "run-a::status": "scored",
            "run-a::execution_status": "success",
            "run-a::evaluation_status": "completed",
            "run-a::quality.turn0": 0.2,
            "run-a::quality.turn1": 0.6,
            "run-a::latency_ms": 900,
            "run-a::total_tokens": 200,
        },
    ]


def test_sample_mean_uses_only_score_dimensions():
    slot = {
        "run-a::quality.turn0": 0.2,
        "run-a::quality.turn1": 0.8,
        "run-a::latency_ms": 9999,
        "run-a::total_tokens": 1234,
        "run-a::tool_call_count": 20,
        "run-a::execution_status": "success",
        "run-a::evaluation_status": "completed",
        "run-a::acceptance_decision": "pass",
        "run-a::status": "scored",
        "display label::quality.turn0": 1.0,
    }

    assert _sample_mean_score(slot, "run-a") == pytest.approx(0.5)
    assert _sample_mean_score(slot, "display label") == pytest.approx(1.0)


def test_subset_without_policy_has_facts_but_no_pass_rate():
    summary = _subset_run_summary(_matrix(), "run-a", "A")

    assert summary["total"] == 2
    assert summary["facts"]["execution_success"] == 2
    assert summary["facts"]["evaluation_completed"] == 2
    assert summary["acceptance"] == {
        "configured": False,
        "decided": None,
        "passed": None,
        "failed": None,
        "undetermined": None,
        "decision_coverage": None,
        "pass_rate": None,
        "run_decision": None,
    }
    assert summary["cost_scored"]["count"] == 2
    assert summary["cost_scored"]["avg_latency_ms"] == 500
    assert "cost_accepted" not in summary
    assert "pass_rate" not in summary
    assert "latency_ms" not in summary["dimension_averages"]


def test_subset_with_policy_computes_acceptance_only_from_policy():
    summary = _subset_run_summary(_matrix(), "run-a", "A", _POLICY)

    assert summary["acceptance"]["configured"] is True
    assert summary["acceptance"]["passed"] == 1
    assert summary["acceptance"]["failed"] == 1
    assert summary["acceptance"]["pass_rate"] == 0.5
    assert summary["acceptance"]["decision_coverage"] == 1.0
    assert summary["acceptance"]["run_decision"] == "qualified"
    assert summary["cost_accepted"]["count"] == 1
    assert summary["cost_accepted"]["avg_latency_ms"] == 100
    assert summary["cost_not_accepted"]["count"] == 1


def test_rule_summary_without_policy_explicitly_says_scoring_only():
    report = _rule_based_summary(
        {
            "facts": {
                "total": 2,
                "execution_success": 2,
                "execution_abnormal": 0,
                "execution_unknown": 0,
                "evaluation_completed": 2,
                "evaluation_partial_or_error": 0,
                "scored": 2,
                "skipped": 0,
            },
            "acceptance": {"configured": False, "pass_rate": None},
            "dimension_averages": {
                "quality.turn0": 0.2,
                "quality.turn1": 0.8,
                "quality.conversation": 0.5,
            },
        },
        "run-a",
    )

    assert "未配置验收规则" in report
    assert "quality.turn" not in report
    assert "quality 0.50" in report
    assert "50.0%" not in report


def test_rule_compare_handles_scoring_only_and_accepted_runs_separately():
    report = _rule_based_compare({
        "runs": [
            {
                "name": "scoring-only",
                "facts": {"total": 2, "execution_success": 2, "evaluation_completed": 2},
                "acceptance": {"configured": False, "pass_rate": None},
                "dimension_averages": {"quality.turn0": 0.2, "quality.turn1": 0.8},
            },
            {
                "name": "with-policy",
                "facts": {"total": 2, "execution_success": 2, "evaluation_completed": 2},
                "acceptance": {
                    "configured": True,
                    "pass_rate": 0.5,
                    "run_decision": "qualified",
                },
                "dimension_averages": {"quality.turn0": 0.4, "quality.turn1": 0.6},
            },
        ],
    })

    assert "scoring-only" in report
    assert "仅评分，未配置验收规则" in report
    assert "with-policy" in report
    assert "验收通过率 50.0%" in report
    assert "0.0%" not in report
    assert "quality.turn" not in report


@pytest.mark.asyncio
async def test_compare_report_falls_back_with_new_semantics(monkeypatch):
    async def fail_lookup(_name: str):
        raise RuntimeError("provider lookup failed")

    fake_orchestrator = ModuleType("agent_eval.feishu.orchestrator")
    fake_orchestrator._load_provider_row = fail_lookup  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_eval.feishu.orchestrator", fake_orchestrator)

    report = await generate_compare_report([
        {
            "name": "run-a",
            "facts": {"total": 2, "execution_success": 2, "evaluation_completed": 2},
            "acceptance": {
                "configured": True,
                "pass_rate": 0.5,
                "run_decision": "qualified",
            },
            "dimension_averages": {"quality.turn0": 0.4, "quality.turn1": 0.6},
            "cost_scored": {},
        }
    ])

    assert "基于规则" in report
    assert "验收通过率 50.0%" in report
