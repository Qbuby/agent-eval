"""评估事实与显式验收策略的统一投影。

本模块只处理纯数据，不访问数据库。历史结果和新运行都必须经由这里投影，
避免再次把 Agent 执行、Judge 评分和业务验收混成一个 ``status``。
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping


_EXECUTION_ERROR_STATUSES = {
    "execution_error",
    "agent_unreachable",
    "agent_timeout",
}
_EVALUATION_ERROR_STATUSES = {"evaluation_error"}
_TURN_SUFFIX = re.compile(r"\.turn\d+$")


def validate_acceptance_policy(policy: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """校验并复制 v1 验收策略；``None`` 表示仅评分。"""
    if policy is None:
        return None
    if not isinstance(policy, Mapping):
        raise ValueError("acceptance_policy must be an object or null")
    if policy.get("version") != 1:
        raise ValueError("acceptance_policy.version must be 1")
    if policy.get("mode") != "threshold":
        raise ValueError("acceptance_policy.mode must be threshold")
    if policy.get("case_rule") != "all":
        raise ValueError("acceptance_policy.case_rule must be all")

    raw_criteria = policy.get("criteria")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise ValueError("acceptance_policy.criteria must not be empty")

    criteria: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_criteria):
        if not isinstance(raw, Mapping):
            raise ValueError(f"acceptance_policy.criteria[{index}] must be an object")
        evaluator_id = str(raw.get("evaluator_id") or "").strip()
        dimension_key = str(raw.get("dimension_key") or "").strip()
        direction = raw.get("direction")
        reducer = raw.get("reducer", "conversation_or_mean")
        if not evaluator_id:
            raise ValueError(f"acceptance_policy.criteria[{index}].evaluator_id is required")
        if not dimension_key:
            raise ValueError(f"acceptance_policy.criteria[{index}].dimension_key is required")
        if direction not in {"higher_better", "lower_better"}:
            raise ValueError(
                f"acceptance_policy.criteria[{index}].direction must be "
                "higher_better or lower_better"
            )
        if reducer != "conversation_or_mean":
            raise ValueError(
                f"acceptance_policy.criteria[{index}].reducer must be conversation_or_mean"
            )
        try:
            threshold = float(raw["threshold"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"acceptance_policy.criteria[{index}].threshold must be numeric"
            ) from exc
        if not math.isfinite(threshold):
            raise ValueError(
                f"acceptance_policy.criteria[{index}].threshold must be finite"
            )
        identity = (evaluator_id, dimension_key)
        if identity in seen:
            raise ValueError("acceptance_policy criteria must be unique")
        seen.add(identity)
        criteria.append({
            "evaluator_id": evaluator_id,
            "evaluator_version_id": (
                str(raw.get("evaluator_version_id"))
                if raw.get("evaluator_version_id") is not None
                else None
            ),
            "dimension_key": dimension_key,
            "direction": direction,
            "threshold": threshold,
            "reducer": reducer,
        })

    raw_run_rule = policy.get("run_rule")
    if not isinstance(raw_run_rule, Mapping):
        raise ValueError("acceptance_policy.run_rule is required")
    run_rule: dict[str, float] = {}
    for key in ("min_case_pass_rate", "min_decision_coverage"):
        try:
            value = float(raw_run_rule[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"acceptance_policy.run_rule.{key} must be numeric") from exc
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"acceptance_policy.run_rule.{key} must be between 0 and 1")
        run_rule[key] = value

    return {
        "version": 1,
        "mode": "threshold",
        "case_rule": "all",
        "criteria": criteria,
        "run_rule": run_rule,
    }


def reduce_dimension_score(
    scores: Mapping[str, float],
    dimension_key: str,
    reducer: str = "conversation_or_mean",
) -> float | None:
    """按策略稳定地取单轮分数或折叠多轮分数。"""
    if reducer != "conversation_or_mean":
        return None
    if dimension_key in scores:
        return float(scores[dimension_key])
    conversation_key = f"{dimension_key}.conversation"
    if conversation_key in scores:
        return float(scores[conversation_key])
    turn_values = [
        float(value)
        for key, value in scores.items()
        if key.startswith(f"{dimension_key}.") and _TURN_SUFFIX.search(key)
    ]
    if not turn_values:
        return None
    return sum(turn_values) / len(turn_values)


def project_case(
    *,
    stored_status: str | None,
    error_type: str | None,
    scores: Mapping[str, float] | None,
    acceptance_policy: Mapping[str, Any] | None = None,
    decision_source: str | None = None,
) -> dict[str, Any]:
    """把持久化结果保守投影为执行、评分和可选验收三层语义。"""
    status = (stored_status or "").strip()
    score_map = dict(scores or {})
    source = decision_source or (
        "legacy_derived" if status in {"pass", "fail", "error"} else "current"
    )

    if status in _EXECUTION_ERROR_STATUSES or (
        status == "error" and error_type != "judge_error"
    ):
        execution_status = "abnormal"
    elif status in {
        "scored", "skipped", "evaluation_error", "pass", "fail"
    } or score_map:
        execution_status = "success"
    else:
        execution_status = "unknown"

    if execution_status == "abnormal":
        evaluation_status = "not_run"
    elif status in _EVALUATION_ERROR_STATUSES or error_type == "judge_error":
        evaluation_status = "error"
    elif status == "skipped":
        evaluation_status = "skipped"
    elif score_map and status in {"scored", "pass", "fail"}:
        evaluation_status = "completed"
    elif score_map:
        evaluation_status = "unknown"
    elif status in {"pass", "fail", "error"}:
        evaluation_status = "unknown"
    else:
        evaluation_status = "skipped" if execution_status == "success" else "unknown"

    policy = validate_acceptance_policy(acceptance_policy)
    acceptance_decision: str | None = None
    criterion_results: list[dict[str, Any]] = []
    if policy is not None:
        if execution_status != "success" or evaluation_status != "completed":
            acceptance_decision = "undetermined"
        else:
            missing = False
            failed = False
            for criterion in policy["criteria"]:
                value = reduce_dimension_score(
                    score_map,
                    criterion["dimension_key"],
                    criterion["reducer"],
                )
                if value is None:
                    verdict = "undetermined"
                    missing = True
                elif criterion["direction"] == "higher_better":
                    verdict = "pass" if value >= criterion["threshold"] else "fail"
                    failed = failed or verdict == "fail"
                else:
                    verdict = "pass" if value <= criterion["threshold"] else "fail"
                    failed = failed or verdict == "fail"
                criterion_results.append({
                    "dimension_key": criterion["dimension_key"],
                    "value": value,
                    "direction": criterion["direction"],
                    "threshold": criterion["threshold"],
                    "verdict": verdict,
                })
            acceptance_decision = (
                "undetermined" if missing else "fail" if failed else "pass"
            )

    return {
        "execution_status": execution_status,
        "evaluation_status": evaluation_status,
        "acceptance_decision": acceptance_decision,
        "decision_source": source,
        "criterion_results": criterion_results,
    }


def aggregate_semantics(
    cases: Iterable[Mapping[str, Any]],
    acceptance_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """聚合互斥事实计数及运行级验收结论。"""
    policy = validate_acceptance_policy(acceptance_policy)
    case_list = list(cases)
    projections = [
        project_case(
            stored_status=case.get("status"),
            error_type=case.get("error_type"),
            scores=case.get("scores") or {},
            acceptance_policy=policy,
            decision_source=case.get("decision_source"),
        )
        for case in case_list
    ]

    facts = {
        "total": len(case_list),
        "execution_success": sum(p["execution_status"] == "success" for p in projections),
        "execution_abnormal": sum(p["execution_status"] == "abnormal" for p in projections),
        "execution_unknown": sum(p["execution_status"] == "unknown" for p in projections),
        "evaluation_completed": sum(
            p["evaluation_status"] == "completed" for p in projections
        ),
        "evaluation_partial_or_error": sum(
            p["evaluation_status"] in {"error", "unknown"} for p in projections
        ),
        "scored": sum(p["evaluation_status"] == "completed" for p in projections),
        "skipped": sum(p["evaluation_status"] == "skipped" for p in projections),
    }

    if policy is None:
        acceptance = {
            "configured": False,
            "decided": None,
            "passed": None,
            "failed": None,
            "undetermined": None,
            "decision_coverage": None,
            "pass_rate": None,
            "run_decision": None,
        }
    else:
        passed = sum(p["acceptance_decision"] == "pass" for p in projections)
        failed = sum(p["acceptance_decision"] == "fail" for p in projections)
        undetermined = sum(
            p["acceptance_decision"] == "undetermined" for p in projections
        )
        decided = passed + failed
        total = len(projections)
        coverage = decided / total if total else 0.0
        pass_rate = passed / decided if decided else None
        rule = policy["run_rule"]
        if (
            not total
            or pass_rate is None
            or coverage < rule["min_decision_coverage"]
        ):
            run_decision = "undetermined"
        elif pass_rate >= rule["min_case_pass_rate"]:
            run_decision = "qualified"
        else:
            run_decision = "unqualified"
        acceptance = {
            "configured": True,
            "decided": decided,
            "passed": passed,
            "failed": failed,
            "undetermined": undetermined,
            "decision_coverage": round(coverage, 4),
            "pass_rate": round(pass_rate, 4) if pass_rate is not None else None,
            "run_decision": run_decision,
        }

    return {"facts": facts, "acceptance": acceptance, "projections": projections}


def project_stored_summary(
    summary: Mapping[str, Any] | None,
    acceptance_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """为历史汇总补充新语义，不修改原始持久化数据。

    老运行没有逐样例事实计数。优先用分数分布中覆盖样例最多的维度估算
    ``scored``；无法确认的剩余样例归入评分异常，而不是验收失败。
    """
    projected = dict(summary or {})
    if isinstance(projected.get("facts"), Mapping) and isinstance(
        projected.get("acceptance"), Mapping
    ):
        return projected

    counts = projected.get("counts") if isinstance(projected.get("counts"), Mapping) else {}
    total = int(counts.get("total") or 0)
    skipped = min(total, max(0, int(counts.get("skipped") or 0)))
    unreachable = min(total, max(0, int(counts.get("unreachable") or 0)))

    distribution = projected.get("score_distribution")
    by_dimension = (
        distribution.get("by_dimension")
        if isinstance(distribution, Mapping)
        and isinstance(distribution.get("by_dimension"), Mapping)
        else {}
    )
    scored = 0
    for buckets in by_dimension.values():
        if isinstance(buckets, list):
            scored = max(scored, sum(int(value or 0) for value in buckets))
    if not by_dimension and projected.get("dimension_averages"):
        # 只有均分而没有逐维分布时不能推断覆盖样例数。
        scored = 0
    scored = min(total, max(0, scored))
    execution_success = max(0, total - unreachable)
    evaluation_abnormal = max(0, execution_success - scored - skipped)

    projected["facts"] = {
        "total": total,
        "execution_success": execution_success,
        "execution_abnormal": unreachable,
        "execution_unknown": 0,
        "evaluation_completed": scored,
        "evaluation_partial_or_error": evaluation_abnormal,
        "scored": scored,
        "skipped": skipped,
        "decision_source": "legacy_derived",
    }
    if acceptance_policy is None:
        projected["acceptance"] = {
            "configured": False,
            "decided": None,
            "passed": None,
            "failed": None,
            "undetermined": None,
            "decision_coverage": None,
            "pass_rate": None,
            "run_decision": None,
        }
    return projected
