from __future__ import annotations

import json
import re

from langchain_core.language_models import BaseChatModel

from agent_eval.evaluation.scorers.base import DimensionScorer
from agent_eval.models.score import DimensionScore
from agent_eval.models.test_case import TestCase
from agent_eval.models.trace import AgentTrace

LLM_JUDGE_PROMPT = """\
You are evaluating the correctness of an AI agent's output.

## User Input
{input}

## Agent Output
{output}

## Reference Answer (if available)
{reference}

## Evaluation Criteria
{criteria}

Rate the output on a scale of 0.0 to 1.0 where:
- 1.0 = perfectly correct, meets all criteria
- 0.5 = partially correct, meets some criteria
- 0.0 = completely wrong

Return ONLY a JSON object: {{"score": <float>, "reason": "<brief explanation>"}}
"""


class OutputCorrectnessScorer(DimensionScorer):
    dimension = "output_correctness"

    def __init__(self, llm: BaseChatModel | None = None):
        self.llm = llm

    async def score(self, test_case: TestCase, trace: AgentTrace) -> DimensionScore:
        scores: list[tuple[str, float]] = []
        details: dict = {}

        if test_case.expected_output:
            rule_score = self._rule_match(test_case.expected_output, trace.final_output)
            scores.append(("rule", rule_score))
            details["rule_score"] = rule_score

        if test_case.expected_output_criteria and self.llm:
            llm_score, reason = await self._llm_judge(test_case, trace)
            scores.append(("llm", llm_score))
            details["llm_score"] = llm_score
            details["llm_reason"] = reason

        if not scores:
            return DimensionScore(
                dimension=self.dimension, score=0.5, scoring_method="none",
                details={"note": "no expected output defined"},
            )

        if len(scores) == 1:
            method, final = scores[0]
        else:
            final = scores[0][1] * 0.4 + scores[1][1] * 0.6
            method = "hybrid"

        return DimensionScore(
            dimension=self.dimension, score=final, scoring_method=method, details=details
        )

    def _rule_match(self, expected: str, actual: str) -> float:
        if not actual:
            return 0.0
        expected_lower = expected.lower().strip()
        actual_lower = actual.lower().strip()

        if expected_lower == actual_lower:
            return 1.0

        if expected_lower in actual_lower:
            return 0.8

        expected_words = set(re.findall(r"\w+", expected_lower))
        actual_words = set(re.findall(r"\w+", actual_lower))
        if not expected_words:
            return 0.0
        overlap = len(expected_words & actual_words) / len(expected_words)
        return round(overlap * 0.7, 4)

    async def _llm_judge(self, test_case: TestCase, trace: AgentTrace) -> tuple[float, str]:
        input_text = "\n".join(
            m.get("content", "") for m in test_case.input_messages if m.get("role") == "user"
        )
        criteria_text = "\n".join(f"- {c}" for c in test_case.expected_output_criteria)

        prompt = LLM_JUDGE_PROMPT.format(
            input=input_text,
            output=trace.final_output,
            reference=test_case.expected_output or "N/A",
            criteria=criteria_text or "General correctness",
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
