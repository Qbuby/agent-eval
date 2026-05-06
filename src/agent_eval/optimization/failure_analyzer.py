from __future__ import annotations

import json
import logging
from collections import Counter

from langchain_core.language_models import BaseChatModel

from agent_eval.models.optimization import FailureCluster, FailureLabel
from agent_eval.models.score import CaseResult, RunSummary
from agent_eval.models.trace import AgentTrace

logger = logging.getLogger(__name__)

FAILURE_CATEGORIES = [
    "tool_selection_error",
    "tool_param_error",
    "missing_tool_call",
    "redundant_tool_call",
    "reasoning_error",
    "hallucination",
    "instruction_following",
    "context_loss",
    "error_handling",
    "other",
]

CLASSIFY_PROMPT = """\
Classify the following agent failure into one of these categories:
{categories}

## Test Case
Input: {input}
Expected: {expected}

## Agent Output
{output}

## Trace Summary
Tool calls: {tool_calls}
Errors: {errors}

## Low-scoring Dimensions
{low_dimensions}

Return ONLY a JSON object:
{{"category": "<category>", "confidence": <0.0-1.0>, "explanation": "<brief>"}}
"""

CLUSTER_SUMMARY_PROMPT = """\
Summarize this cluster of {count} agent failures in the "{category}" category.

## Sample Failures
{samples}

Provide:
1. A concise summary of the common failure pattern
2. A suggested fix direction

Return ONLY a JSON object:
{{"summary": "<pattern description>", "fix_direction": "<what to change>"}}
"""


class FailureAnalyzer:
    def __init__(self, llm: BaseChatModel, failure_threshold: float = 0.7):
        self.llm = llm
        self.failure_threshold = failure_threshold

    async def analyze(
        self,
        run_summary: RunSummary,
        traces: dict[str, AgentTrace],
        test_cases: dict[str, dict],
    ) -> list[FailureCluster]:
        failed = [cr for cr in run_summary.case_results if cr.aggregate_score < self.failure_threshold]

        if not failed:
            return []

        labels: list[tuple[CaseResult, FailureLabel]] = []
        for cr in failed:
            trace = traces.get(cr.test_case_id)
            case_info = test_cases.get(cr.test_case_id, {})
            label = await self._classify(cr, trace, case_info)
            labels.append((cr, label))

        clusters = self._build_clusters(labels, traces, test_cases)

        for cluster in clusters:
            await self._enrich_cluster(cluster)

        return clusters

    async def _classify(
        self, case_result: CaseResult, trace: AgentTrace | None, case_info: dict
    ) -> FailureLabel:
        input_text = ""
        if case_info.get("input_messages"):
            input_text = "\n".join(
                m.get("content", "") for m in case_info["input_messages"] if m.get("role") == "user"
            )

        tool_calls_text = "none"
        errors_text = "none"
        output_text = ""
        if trace:
            tool_calls_text = ", ".join(tc.tool_name for tc in trace.tool_calls) or "none"
            errors_text = ", ".join(
                f"{tc.tool_name}: {tc.error}" for tc in trace.tool_calls if tc.error
            ) or "none"
            output_text = trace.final_output[:500]

        low_dims = "\n".join(
            f"- {dim}: {ds.score:.2f}" for dim, ds in case_result.dimension_scores.items()
            if ds.score < 0.5
        )

        prompt = CLASSIFY_PROMPT.format(
            categories="\n".join(f"- {c}" for c in FAILURE_CATEGORIES),
            input=input_text[:500],
            expected=case_info.get("expected_output", "N/A")[:300],
            output=output_text,
            tool_calls=tool_calls_text,
            errors=errors_text,
            low_dimensions=low_dims or "(none below 0.5)",
        )

        try:
            response = await self.llm.ainvoke(prompt)
            text = response.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            data = json.loads(text)
            return FailureLabel(
                category=data.get("category", "other"),
                confidence=float(data.get("confidence", 0.5)),
                explanation=data.get("explanation", ""),
            )
        except Exception as e:
            logger.warning("Failed to classify failure: %s", e)
            return FailureLabel(category="other", confidence=0.0, explanation=str(e))

    def _build_clusters(
        self,
        labels: list[tuple[CaseResult, FailureLabel]],
        traces: dict[str, AgentTrace],
        test_cases: dict[str, dict],
    ) -> list[FailureCluster]:
        by_category: dict[str, list[tuple[CaseResult, FailureLabel]]] = {}
        for cr, label in labels:
            by_category.setdefault(label.category, []).append((cr, label))

        clusters = []
        for category, items in by_category.items():
            sample_errors = []
            for cr, label in items[:5]:
                trace = traces.get(cr.test_case_id)
                sample_errors.append({
                    "test_case_id": cr.test_case_id,
                    "score": cr.aggregate_score,
                    "classification": label.explanation,
                    "output_preview": (trace.final_output[:200] if trace else ""),
                })

            clusters.append(FailureCluster(
                category=category,
                count=len(items),
                test_case_ids=[cr.test_case_id for cr, _ in items],
                sample_errors=sample_errors,
            ))

        return sorted(clusters, key=lambda c: c.count, reverse=True)

    async def _enrich_cluster(self, cluster: FailureCluster) -> None:
        samples_text = "\n\n".join(
            f"Case {e['test_case_id'][:8]}... (score: {e['score']:.2f})\n"
            f"Classification: {e['classification']}\n"
            f"Output: {e['output_preview']}"
            for e in cluster.sample_errors
        )

        prompt = CLUSTER_SUMMARY_PROMPT.format(
            count=cluster.count,
            category=cluster.category,
            samples=samples_text,
        )

        try:
            response = await self.llm.ainvoke(prompt)
            text = response.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            data = json.loads(text)
            cluster.summary = data.get("summary", "")
            cluster.suggested_fix_direction = data.get("fix_direction", "")
        except Exception as e:
            logger.warning("Failed to enrich cluster %s: %s", cluster.category, e)
            cluster.summary = f"{cluster.count} failures in {cluster.category}"
