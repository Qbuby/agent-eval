from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel

from agent_eval.models.optimization import FailureCluster
from agent_eval.models.test_case import TestCase

logger = logging.getLogger(__name__)

SCENARIO_GEN_PROMPT = """\
You are a test case generator for an AI agent evaluation system.

Given the following scenario description, generate {count} diverse test cases
that thoroughly test the described capability.

## Scenario
{scenario}

## Additional Context
{context}

## Output Format
Return a JSON array. Each element must have:
- "name": short descriptive name
- "description": what this case tests
- "input_messages": [{{"role": "user", "content": "..."}}]
- "expected_output_criteria": list of natural language criteria for judging correctness
- "tags": list of relevant tags

Cover: happy path, edge cases, error conditions, multi-turn if applicable.
Return ONLY the JSON array, no other text.
"""

MUTATION_GEN_PROMPT = """\
You are a test case generator for an AI agent evaluation system.

Given the following existing test case, generate {count} variants using the
"{strategy}" strategy.

Strategy descriptions:
- rephrase: same intent, different wording / language style
- edge_case: boundary values, unusual inputs, minimal / maximal lengths
- adversarial: inputs designed to confuse, mislead, or break the agent
- mixed: a mix of all the above

## Original Test Case
Name: {name}
Description: {description}
Input Messages:
{input_messages}

Expected Output: {expected_output}
Expected Criteria: {criteria}

## Output Format
Return a JSON array. Each element must have:
- "name": short descriptive name (indicate it is a variant)
- "description": what this variant specifically tests
- "input_messages": [{{"role": "user", "content": "..."}}]
- "expected_output_criteria": list of natural language criteria
- "tags": list of relevant tags (include "mutation:{strategy}")

Return ONLY the JSON array, no other text.
"""

FAILURE_GEN_PROMPT = """\
You are a test case generator for an AI agent evaluation system.

Given the following failure cluster analysis, generate {count} new test cases that target
the same failure pattern but with different inputs to improve coverage.

## Failure Cluster
Category: {category}
Summary: {summary}
Fix Direction: {fix_direction}

## Sample Errors
{sample_errors}

## Output Format
Return a JSON array. Each element must have:
- "name": short descriptive name
- "description": what this case tests
- "input_messages": [{{"role": "user", "content": "..."}}]
- "expected_output_criteria": list of natural language criteria
- "tags": list of relevant tags

Return ONLY the JSON array, no other text.
"""


class CaseGenerator:

    def __init__(self, llm: BaseChatModel):
        self.llm = llm

    async def generate_from_scenario(
        self,
        scenario: str,
        *,
        count: int = 5,
        context: str = "",
        tags: list[str] | None = None,
    ) -> list[TestCase]:
        prompt = SCENARIO_GEN_PROMPT.format(
            count=count,
            scenario=scenario,
            context=context or "None provided",
        )
        response = await self.llm.ainvoke(prompt)
        cases = self._parse_cases(response.content, source="auto_generated")
        if tags:
            for case in cases:
                case.tags.extend(tags)
        return cases

    async def generate_mutations(
        self,
        source_case: TestCase,
        *,
        count: int = 3,
        strategy: str = "mixed",
        tags: list[str] | None = None,
    ) -> list[TestCase]:
        input_text = json.dumps(source_case.input_messages, ensure_ascii=False, indent=2)
        criteria_text = "\n".join(f"- {c}" for c in source_case.expected_output_criteria) or "N/A"
        prompt = MUTATION_GEN_PROMPT.format(
            count=count,
            name=source_case.name,
            description=source_case.description,
            input_messages=input_text,
            expected_output=source_case.expected_output or "N/A",
            criteria=criteria_text,
            strategy=strategy,
        )
        response = await self.llm.ainvoke(prompt)
        cases = self._parse_cases(response.content, source="auto_generated")
        for case in cases:
            case.parent_case_id = source_case.id
            if tags:
                case.tags.extend(tags)
        return cases

    async def generate_from_failures(
        self,
        clusters: list[FailureCluster],
        cases_per_cluster: int = 3,
    ) -> list[TestCase]:
        generated: list[TestCase] = []
        for cluster in clusters:
            cases = await self._generate_for_cluster(cluster, cases_per_cluster)
            generated.extend(cases)
        return generated

    async def _generate_for_cluster(
        self, cluster: FailureCluster, count: int
    ) -> list[TestCase]:
        sample_errors_text = "\n".join(
            f"- {json.dumps(e, ensure_ascii=False)}" for e in cluster.sample_errors[:5]
        )
        prompt = FAILURE_GEN_PROMPT.format(
            count=count,
            category=cluster.category,
            summary=cluster.summary,
            fix_direction=cluster.suggested_fix_direction,
            sample_errors=sample_errors_text,
        )
        response = await self.llm.ainvoke(prompt)
        cases = self._parse_cases(response.content, source="failure_derived")
        for case in cases:
            case.tags.append(f"failure:{cluster.category}")
        return cases

    def _parse_cases(self, content: str, source: str) -> list[TestCase]:
        try:
            text = content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            raw_cases = json.loads(text)
        except (json.JSONDecodeError, IndexError):
            logger.warning("Failed to parse LLM response as JSON")
            return []

        if not isinstance(raw_cases, list):
            raw_cases = [raw_cases]

        cases = []
        for raw in raw_cases:
            if not isinstance(raw, dict):
                continue
            cases.append(
                TestCase(
                    dataset_version="",
                    name=raw.get("name", "auto-generated"),
                    description=raw.get("description", ""),
                    tags=raw.get("tags", []),
                    source=source,
                    input_messages=raw.get("input_messages", []),
                    expected_output=raw.get("expected_output"),
                    expected_output_criteria=raw.get("expected_output_criteria", []),
                )
            )
        return cases
