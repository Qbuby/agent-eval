from __future__ import annotations

import json

from langchain_core.language_models import BaseChatModel

from agent_eval.evaluation.scorers.base import DimensionScorer
from agent_eval.models.score import DimensionScore
from agent_eval.models.test_case import TestCase
from agent_eval.models.trace import AgentTrace

RECOVERY_JUDGE_PROMPT = """\
An AI agent encountered tool errors during execution. Evaluate its error recovery.

## Errors Encountered
{errors}

## Actions After Errors
{post_error_actions}

## Final Output
{final_output}

Rate the recovery strategy 0.0-1.0:
- 1.0 = excellent recovery (retried with fix, used alternative, or gracefully degraded)
- 0.5 = partial recovery (acknowledged error but output quality suffered)
- 0.0 = no recovery (crashed, ignored error, or produced wrong output)

Return ONLY: {{"score": <float>, "reason": "<explanation>"}}
"""


class ErrorRecoveryScorer(DimensionScorer):
    dimension = "error_recovery"

    def __init__(self, llm: BaseChatModel | None = None):
        self.llm = llm

    async def score(self, test_case: TestCase, trace: AgentTrace) -> DimensionScore:
        failed_tools = [tc for tc in trace.tool_calls if tc.error]

        if not failed_tools:
            return DimensionScore(
                dimension=self.dimension, score=1.0, scoring_method="rule",
                details={"note": "no errors encountered"},
            )

        rule_score = self._rule_score(trace, failed_tools)
        details: dict = {"rule_score": rule_score, "error_count": len(failed_tools)}

        if self.llm:
            llm_score, reason = await self._llm_judge(trace, failed_tools)
            details["llm_score"] = llm_score
            details["llm_reason"] = reason
            final = rule_score * 0.4 + llm_score * 0.6
            method = "hybrid"
        else:
            final = rule_score
            method = "rule"

        return DimensionScore(
            dimension=self.dimension, score=round(final, 4),
            scoring_method=method, details=details,
        )

    def _rule_score(self, trace: AgentTrace, failed_tools: list) -> float:
        if trace.error:
            return 0.0

        recovery_signals = 0
        for i, tc in enumerate(trace.tool_calls):
            if tc.error:
                remaining = trace.tool_calls[i + 1:]
                if any(r.tool_name == tc.tool_name and not r.error for r in remaining):
                    recovery_signals += 1
                elif any(not r.error for r in remaining):
                    recovery_signals += 0.5

        if not failed_tools:
            return 1.0
        return min(1.0, recovery_signals / len(failed_tools))

    async def _llm_judge(self, trace: AgentTrace, failed_tools: list) -> tuple[float, str]:
        errors_text = "\n".join(
            f"- {tc.tool_name}: {tc.error}" for tc in failed_tools
        )

        first_error_idx = next(
            (i for i, tc in enumerate(trace.tool_calls) if tc.error), 0
        )
        post_error = trace.tool_calls[first_error_idx + 1:]
        post_error_text = "\n".join(
            f"- {tc.tool_name}({'error: ' + tc.error if tc.error else 'success'})"
            for tc in post_error
        ) or "(no further actions)"

        prompt = RECOVERY_JUDGE_PROMPT.format(
            errors=errors_text,
            post_error_actions=post_error_text,
            final_output=trace.final_output[:500],
        )

        response = await self.llm.ainvoke(prompt)
        try:
            text = response.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            data = json.loads(text)
            return float(data["score"]), data.get("reason", "")
        except (json.JSONDecodeError, KeyError, ValueError):
            return 0.5, "failed to parse LLM judge response"
