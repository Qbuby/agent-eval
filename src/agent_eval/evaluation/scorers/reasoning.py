from __future__ import annotations

import json

from langchain_core.language_models import BaseChatModel

from agent_eval.evaluation.scorers.base import DimensionScorer
from agent_eval.models.score import DimensionScore
from agent_eval.models.test_case import TestCase
from agent_eval.models.trace import AgentTrace

REASONING_JUDGE_PROMPT = """\
You are evaluating the reasoning quality of an AI agent's execution trace.

## User Input
{input}

## Reasoning Chain
{reasoning_chain}

## Tool Outputs Received
{tool_outputs}

## Final Output
{final_output}

Evaluate the reasoning on these dimensions:
1. Logical coherence: Does each step follow logically from the previous?
2. Information utilization: Does the agent effectively use tool outputs?
3. Hallucination: Does the agent fabricate information not from tools or input?
4. Efficiency: Does the reasoning avoid unnecessary detours?

Rate each dimension 0.0-1.0 and provide an overall score.

Return ONLY a JSON object:
{{
  "coherence": <float>,
  "utilization": <float>,
  "hallucination_free": <float>,
  "efficiency": <float>,
  "overall": <float>,
  "reason": "<brief explanation>"
}}
"""


class ReasoningQualityScorer(DimensionScorer):
    dimension = "reasoning_quality"

    def __init__(self, llm: BaseChatModel):
        self.llm = llm

    async def score(self, test_case: TestCase, trace: AgentTrace) -> DimensionScore:
        if not trace.reasoning_steps:
            return DimensionScore(
                dimension=self.dimension, score=0.0, scoring_method="llm",
                details={"note": "no reasoning steps recorded"},
            )

        input_text = "\n".join(
            m.get("content", "") for m in test_case.input_messages if m.get("role") == "user"
        )

        reasoning_chain = "\n---\n".join(
            f"Step {i+1}: {step.content}" for i, step in enumerate(trace.reasoning_steps) if step.content
        )

        tool_outputs = "\n".join(
            f"[{tc.tool_name}]: {(tc.tool_output or '')[:500]}" for tc in trace.tool_calls
        )

        prompt = REASONING_JUDGE_PROMPT.format(
            input=input_text,
            reasoning_chain=reasoning_chain or "(empty)",
            tool_outputs=tool_outputs or "(none)",
            final_output=trace.final_output[:1000],
        )

        response = await self.llm.ainvoke(prompt)
        try:
            text = response.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            data = json.loads(text)
            return DimensionScore(
                dimension=self.dimension,
                score=float(data.get("overall", 0.5)),
                scoring_method="llm",
                details={
                    "coherence": data.get("coherence"),
                    "utilization": data.get("utilization"),
                    "hallucination_free": data.get("hallucination_free"),
                    "efficiency": data.get("efficiency"),
                    "reason": data.get("reason", ""),
                },
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return DimensionScore(
                dimension=self.dimension, score=0.5, scoring_method="llm",
                details={"error": "failed to parse LLM judge response"},
            )
