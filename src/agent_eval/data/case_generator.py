from __future__ import annotations

import json
import logging
from typing import Any

from agent_eval.evaluation.agent_adapter import AgentResponse
from agent_eval.models.optimization import FailureCluster
from agent_eval.models.test_case import TestCase

logger = logging.getLogger(__name__)

# The generator now drives the *same* agent endpoint used for evaluation
# (an SSE/OpenAI-compatible agent, typically backed by a knowledge graph)
# instead of a raw LLM. We send the generation instruction as a normal user
# question; the agent answers from its own domain knowledge / KG and we parse
# the JSON array out of its reply. This is what makes the generated cases
# domain-grounded rather than the agent-agnostic questions a bare LLM invents.

SCENARIO_GEN_PROMPT = """\
你是被测智能体本身。请基于你自己的知识库 / 知识图谱，出 {count} 道用于
检验你这类智能体能力的测试题。每道题给出问题和对应的标准答案。

{seed_block}## 测试场景 / 主题
{scenario}

## 补充上下文
{context}

## 输出格式
只返回一个 JSON 数组，不要输出任何其它文字、解释或 Markdown 代码围栏。
数组每个元素必须包含：
- "name": 简短描述性名称
- "description": 这道题考察什么
- "input_messages": [{{"role": "user", "content": "向智能体提的问题"}}]
- "expected_output": 基于你知识库的标准答案（字符串）
- "expected_output_criteria": 判定回答是否正确的自然语言标准（列表）
- "tags": 相关标签（列表）

要求：题目必须来自你实际掌握的领域知识，覆盖常见问法、边界情况、易错点；
若给了种子样例，保持相同领域 / 语言 / 风格，但变化问题内容，不要照抄。
若未给定测试场景，则围绕你最核心的领域能力自由出题。
再次强调：只返回 JSON 数组本身。
"""

MUTATION_GEN_PROMPT = """\
你是被测智能体本身。下面给出一道已有的测试题，请用 "{strategy}" 策略，
基于你自己的知识库生成 {count} 个变体。

策略说明：
- rephrase: 同一意图，不同措辞 / 语言风格
- edge_case: 边界值、异常输入、极短 / 极长
- adversarial: 故意混淆、误导、试图让智能体出错的输入
- mixed: 上述混合

## 原始测试题
名称: {name}
描述: {description}
输入消息:
{input_messages}

期望答案: {expected_output}
判定标准: {criteria}

## 输出格式
只返回一个 JSON 数组，不要输出任何其它文字、解释或 Markdown 代码围栏。
数组每个元素必须包含：
- "name": 简短描述性名称（标明是变体）
- "description": 这个变体具体考察什么
- "input_messages": [{{"role": "user", "content": "..."}}]
- "expected_output": 基于你知识库的标准答案（字符串）
- "expected_output_criteria": 判定标准（列表）
- "tags": 相关标签（列表，包含 "mutation:{strategy}"）

再次强调：只返回 JSON 数组本身。
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
    """Generates test cases by asking the *agent under test* to author them.

    ``adapter`` is one of the evaluation agent adapters
    (``SSEStreamAdapter`` / ``OpenAICompatibleAdapter``). We send each
    generation instruction as a user turn and parse the JSON array out of the
    agent's reply, so the questions come from the agent's own knowledge graph
    rather than a generic LLM.
    """

    def __init__(self, adapter: Any):
        self.adapter = adapter

    async def _ask_agent(self, prompt: str) -> str:
        """Send the generation instruction as a single user turn and return
        the agent's raw text reply (to be parsed as a JSON array)."""
        resp: AgentResponse = await self.adapter.invoke(
            [{"role": "user", "content": prompt}]
        )
        return resp.content or ""

    async def generate_from_scenario(
        self,
        scenario: str,
        *,
        count: int = 5,
        context: str = "",
        tags: list[str] | None = None,
        seed_cases: list[TestCase] | None = None,
    ) -> list[TestCase]:
        seed_block = ""
        if seed_cases:
            # Pick up to 5 cases as seed/few-shot — enough to anchor the
            # domain and style without dominating the prompt window.
            sample = seed_cases[:5]
            lines = ["## 种子样例（本数据集已有的样例，请据此泛化，不要照抄）"]
            for i, c in enumerate(sample, 1):
                user_msg = ""
                for m in (c.input_messages or []):
                    if isinstance(m, dict) and m.get("role") == "user":
                        user_msg = str(m.get("content", "")).strip()
                        break
                if not user_msg:
                    continue
                # Keep each example compact
                user_msg = user_msg if len(user_msg) <= 400 else user_msg[:400] + "…"
                lines.append(f"{i}. {user_msg}")
                if c.expected_output:
                    eo = c.expected_output if len(c.expected_output) <= 200 else c.expected_output[:200] + "…"
                    lines.append(f"   期望答案: {eo}")
            lines.append("")
            seed_block = "\n".join(lines) + "\n"

        prompt = SCENARIO_GEN_PROMPT.format(
            count=count,
            scenario=scenario.strip() or "（未指定，围绕你最核心的领域能力自由出题）",
            context=context or "无",
            seed_block=seed_block,
        )
        content = await self._ask_agent(prompt)
        cases = self._parse_cases(content, source="auto_generated")
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
        response = await self._ask_agent(prompt)
        cases = self._parse_cases(response, source="auto_generated")
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
        response = await self._ask_agent(prompt)
        cases = self._parse_cases(response, source="failure_derived")
        for case in cases:
            case.tags.append(f"failure:{cluster.category}")
        return cases

    def _parse_cases(self, content: str, source: str) -> list[TestCase]:
        raw_cases = self._extract_json(content)
        if raw_cases is None:
            logger.warning("Failed to parse agent response as JSON")
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

    @staticmethod
    def _extract_json(content: str) -> Any | None:
        """Best-effort extraction of a JSON array/object from an agent reply.

        Agents (unlike a tightly-prompted LLM) frequently wrap the payload in
        prose ("好的，以下是测试题：…") and/or a ```json fence. We try, in order:
        1. the whole stripped text, 2. the content of a ``` fenced block,
        3. the first top-level [...] / {...} substring. Returns the parsed
        object, or None if nothing parses."""
        if not content:
            return None
        text = content.strip()

        # 1. straight parse (covers a clean JSON-only reply)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. fenced code block ```json ... ``` (anywhere in the text)
        import re
        fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
        if fence:
            try:
                return json.loads(fence.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 3. first balanced [...] array, else first {...} object
        for open_ch, close_ch in (("[", "]"), ("{", "}")):
            start = text.find(open_ch)
            end = text.rfind(close_ch)
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
        return None
