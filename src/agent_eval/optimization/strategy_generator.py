from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel

from agent_eval.models.optimization import (
    FailureCluster,
    OptimizationStrategy,
    StrategyChange,
)

logger = logging.getLogger(__name__)

STRATEGY_PROMPT = """\
You are an AI agent optimization expert. Based on the failure analysis below,
generate a concrete optimization strategy.

## Current Agent Configuration
System Prompt:
{system_prompt}

Tools:
{tools}

Parameters:
{parameters}

## Failure Clusters (sorted by frequency)
{clusters}

## Previous Optimization Attempts (avoid repeating)
{history}

## Instructions
Generate specific, actionable changes. Each change should have:
- change_type: "prompt_modification" | "tool_description" | "tool_parameter" | "system_parameter"
- target: what to modify (e.g., "system_prompt", "tool.search.description", "temperature")
- after: the new value
- reason: why this change addresses the failure

Return ONLY a JSON object:
{{
  "strategy_type": "prompt" | "tool_config" | "system_param" | "composite",
  "changes": [
    {{
      "change_type": "<type>",
      "target": "<target>",
      "before": "<current value or null>",
      "after": "<new value>",
      "reason": "<why>"
    }}
  ],
  "rationale": "<overall strategy explanation>",
  "expected_improvement": {{"<dimension>": <delta>, ...}},
  "risk_level": "low" | "medium" | "high"
}}
"""


class StrategyGenerator:
    def __init__(self, llm: BaseChatModel):
        self.llm = llm

    async def generate(
        self,
        clusters: list[FailureCluster],
        current_config: dict[str, Any],
        history: list[OptimizationStrategy] | None = None,
    ) -> OptimizationStrategy:
        system_prompt = current_config.get("system_prompt", "(not set)")
        tools_text = "\n".join(
            f"- {t.get('name', 'unknown')}: {t.get('description', '')[:100]}"
            for t in current_config.get("tools", [])
        ) or "(no tools)"
        params_text = json.dumps(
            {k: v for k, v in current_config.items() if k not in ("system_prompt", "tools")},
            indent=2, ensure_ascii=False,
        )

        clusters_text = "\n\n".join(
            f"### {c.category} ({c.count} failures)\n"
            f"Summary: {c.summary}\n"
            f"Fix direction: {c.suggested_fix_direction}"
            for c in clusters
        )

        history_text = "None" if not history else "\n".join(
            f"- [{s.strategy_type}] {s.rationale[:100]}" for s in history
        )

        prompt = STRATEGY_PROMPT.format(
            system_prompt=system_prompt[:2000],
            tools=tools_text,
            parameters=params_text,
            clusters=clusters_text,
            history=history_text,
        )

        response = await self.llm.ainvoke(prompt)
        return self._parse_response(response.content)

    def _parse_response(self, content: str) -> OptimizationStrategy:
        try:
            text = content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            data = json.loads(text)

            changes = [
                StrategyChange(
                    change_type=c["change_type"],
                    target=c["target"],
                    before=c.get("before"),
                    after=c["after"],
                    reason=c.get("reason", ""),
                )
                for c in data.get("changes", [])
            ]

            return OptimizationStrategy(
                strategy_type=data.get("strategy_type", "composite"),
                changes=changes,
                rationale=data.get("rationale", ""),
                expected_improvement=data.get("expected_improvement", {}),
                risk_level=data.get("risk_level", "medium"),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse strategy response: %s", e)
            return OptimizationStrategy(
                strategy_type="composite",
                rationale=f"Failed to parse LLM response: {e}",
                risk_level="high",
            )
