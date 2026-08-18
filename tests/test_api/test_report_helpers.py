"""评估报告统一语义的定向回归测试。"""
from __future__ import annotations

import sys
from types import ModuleType

import pytest

from agent_eval.api.routers.evaluation import _sample_mean_score, _subset_run_summary
from agent_eval.feishu.report_llm import (
    _collapse_cmp_dims,
    _compact_comparison,
    _has_comparison_summary,
    _rule_based_compare,
    _rule_based_comparison_run,
    _rule_based_summary,
    generate_compare_report,
    generate_run_report,
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
    # 未配置验收规则的 run 不得被编造出通过率：锚定到该 run 所在行判断，
    # 否则 "0.0%" 会被另一行的 "50.0%" 子串命中，断言恒假。
    scoring_line = next(li for li in report.splitlines() if "scoring-only" in li)
    assert "通过率" not in scoring_line
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


def _cmp_summary() -> dict:
    """双模对比 summary_scores 夹具：含逐轮维度、非法评估器项、部分缺值的 perf。"""
    return {
        "facts": {"total": 3, "scored": 2, "skipped": 1},
        "comparison_summary": {
            "answer_counts": {"a_valid": 5, "a_blank": 1, "b_valid": 6, "b_blank": 0},
            "evaluators": [
                {
                    "label": "整体质量对比",
                    "scored": 2,
                    "skipped": 1,
                    "evaluation_errors": 0,
                    "a_wins": 1,
                    "b_wins": 3,
                    "ties": 1,
                    "per_dimension": {
                        "轮1·准确性": {
                            "a_wins": 1, "b_wins": 2, "ties": 0,
                            "mean_a": 0.4, "mean_b": 0.8, "n": 2,
                        },
                        "轮2·准确性": {
                            "a_wins": 0, "b_wins": 1, "ties": 1,
                            "mean_a": 0.6, "mean_b": 0.6, "n": 2,
                        },
                        "会话·连贯性": {
                            "a_wins": 0, "b_wins": 0, "ties": 1,
                            "mean_a": 0.5, "mean_b": 0.9, "n": 1,
                        },
                    },
                },
                "不是字典的脏数据",
            ],
            "perf": {
                "total_tokens": {
                    "a": {"sum": 200, "mean": 100.0, "n": 2},
                    "b": {"sum": 300, "mean": 150.0, "n": 2},
                    "delta": {"value": 50.0, "percent": 0.5},
                },
                "latency_ms": {
                    "a": {"sum": 1000, "mean": 500.0, "n": 2},
                    "b": {"sum": 800, "mean": 400.0, "n": 2},
                    "delta": {"value": -100.0, "percent": -0.2},
                },
                # A/B 均值都缺 → 规则报告应整行跳过，不能打印 None
                "tool_call_count": {"a": {}, "b": {}, "delta": {}},
            },
        },
    }


def test_has_comparison_summary_only_true_for_real_ab_payload():
    assert _has_comparison_summary(_cmp_summary()) is True
    # 单模 summary、空评估器列表、类型不符都必须否决，否则单模会误走对比分支
    assert _has_comparison_summary({"facts": {"total": 2}, "acceptance": {}}) is False
    assert _has_comparison_summary({"comparison_summary": {"evaluators": []}}) is False
    assert _has_comparison_summary({"comparison_summary": []}) is False
    assert _has_comparison_summary(None) is False


def test_collapse_cmp_dims_merges_turns_and_keeps_session_dimension():
    per_dim = _cmp_summary()["comparison_summary"]["evaluators"][0]["per_dimension"]
    collapsed = _collapse_cmp_dims(per_dim)

    assert set(collapsed) == {"准确性", "会话·连贯性"}
    # 轮1+轮2 的胜负累加，均分按 n 加权：A (0.4*2+0.6*2)/4=0.5，B (0.8*2+0.6*2)/4=0.7
    assert collapsed["准确性"] == {
        "a_wins": 1, "b_wins": 3, "ties": 1,
        "mean_a": 0.5, "mean_b": 0.7,
    }
    assert collapsed["会话·连贯性"]["mean_a"] == 0.5
    assert collapsed["会话·连贯性"]["mean_b"] == 0.9


def test_collapse_cmp_dims_tolerates_missing_and_dirty_slots():
    collapsed = _collapse_cmp_dims({
        "轮1·准确性": {"a_wins": 2, "b_wins": 1, "ties": 0},          # 无 n/均分
        "轮2·准确性": {"a_wins": 1, "b_wins": 0, "ties": 1, "n": 0,
                       "mean_a": 0.9, "mean_b": 0.1},                 # n=0 不得计入均分
        "轮3·准确性": "脏数据",
    })

    assert collapsed["准确性"]["a_wins"] == 3
    assert collapsed["准确性"]["b_wins"] == 1
    assert collapsed["准确性"]["ties"] == 1
    # 没有任何有效 n → 均分为 None，而不是 0 或 ZeroDivisionError
    assert collapsed["准确性"]["mean_a"] is None
    assert collapsed["准确性"]["mean_b"] is None
    assert _collapse_cmp_dims(None) == {}


def test_compact_comparison_keeps_ab_facts_and_drops_dirty_evaluators():
    payload = _compact_comparison(_cmp_summary())

    assert len(payload["evaluators"]) == 1
    ev = payload["evaluators"][0]
    assert ev["label"] == "整体质量对比"
    assert (ev["a_wins"], ev["b_wins"], ev["ties"]) == (1, 3, 1)
    assert (ev["scored"], ev["skipped"], ev["evaluation_errors"]) == (2, 1, 0)
    # 喂 LLM 的维度必须已合并，逐轮键不能透传
    assert set(ev["per_dimension"]) == {"准确性", "会话·连贯性"}
    assert payload["answer_counts"]["a_blank"] == 1
    assert payload["perf"]["latency_ms"]["delta"]["percent"] == -0.2
    assert payload["facts"]["total"] == 3
    # 单模字段不应混进对比载荷
    assert "acceptance" not in payload
    assert "dimension_averages" not in payload

    no_perf = _cmp_summary()
    no_perf["comparison_summary"]["perf"] = {}
    assert "perf" not in _compact_comparison(no_perf)


def test_rule_based_comparison_run_reports_wins_dims_and_perf():
    report = _rule_based_comparison_run(_cmp_summary(), "cmp-run")

    assert "# 双模对比分析 · cmp-run" in report
    assert "A/B 有效回答：A 5 有效 / 1 空白；B 6 有效 / 0 空白" in report
    assert "## 整体质量对比" in report
    assert "胜负：A 胜 1 / B 胜 3 / 平 1（跳过 1，评分失败 0）" in report
    assert "· 准确性：A 胜 1 / B 胜 3 / 平 1，均分 A 0.5 / B 0.7" in report
    assert "· 会话·连贯性：A 胜 0 / B 胜 0 / 平 1，均分 A 0.5 / B 0.9" in report
    # 维度已合并 → 逐轮前缀不出现；脏数据评估器不产出小节
    assert "轮1·" not in report
    assert "不是字典的脏数据" not in report

    assert "## 性能成本对比（A vs B）" in report
    assert "· 总 token：A 均 100.0 / B 均 150.0（B 相对 A +50.0%）" in report
    assert "· 时延(ms)：A 均 500.0 / B 均 400.0（B 相对 A -20.0%）" in report
    # A/B 均值皆缺的指标整行跳过，不能出现 None
    assert "工具调用数" not in report
    assert "None" not in report


@pytest.mark.asyncio
async def test_run_report_uses_comparison_fallback_when_provider_missing(monkeypatch):
    async def missing_provider(_name: str):
        return None

    fake_orchestrator = ModuleType("agent_eval.feishu.orchestrator")
    fake_orchestrator._load_provider_row = missing_provider  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_eval.feishu.orchestrator", fake_orchestrator)

    # judge_clients 真实可导入性不影响本用例语义，固定住避免环境差异改变分支
    fake_judge = ModuleType("agent_eval.evaluation.judge_clients")

    class _JudgeClientError(Exception):
        pass

    fake_judge.JudgeClientError = _JudgeClientError  # type: ignore[attr-defined]
    fake_judge.build_judge_client = lambda *a, **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_eval.evaluation.judge_clients", fake_judge)

    report = await generate_run_report(_cmp_summary(), run_name="cmp-run")

    # 对比运行必须落到对比兜底，不能退化成单模报告
    assert "# 双模对比分析 · cmp-run" in report
    assert "胜负：A 胜 1 / B 胜 3 / 平 1" in report
    assert "未配置验收规则" not in report
