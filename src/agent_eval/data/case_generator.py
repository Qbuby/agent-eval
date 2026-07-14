from __future__ import annotations

import json
import logging
import uuid
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

    async def fill_expected_from_agent(
        self,
        cases: list[TestCase],
        *,
        agent_cfg: dict[str, Any],
        http_client: Any = None,
        concurrency: int = 3,
    ) -> list[TestCase]:
        """Run each case's question(s) against the agent-under-test and store the
        agent's *actual* reply as the expected output — turning ``expected_output``
        from "a description of the right answer" into "the answer the agent gives".

        - Single-turn (one user message): invoke once, overwrite ``expected_output``.
        - Multi-turn (multiple user messages): replay the whole conversation once
          (server-memory agents keep context via a fixed thread_id) and write each
          turn's assistant reply back into the matching ``turn_expectations[i]``
          (aligned by ``turn_index``); also fill the case-level ``expected_output``
          with the final turn's reply so single-value consumers still see something.

        Empty / failed replies never overwrite an existing value (guards against a
        truncated stream silently blanking a good answer). Cases are processed
        concurrently (bounded by ``concurrency``). Mutates and returns ``cases``.
        """
        import asyncio

        from agent_eval.evaluation import multiturn
        from agent_eval.evaluation.langfuse_runner import (
            _invoke_with_retry,
            _make_adapter,
            _retry_policy_from_cfg,
        )

        if not cases:
            return cases

        agent_type = (agent_cfg or {}).get("type", "sse")
        retry_policy = _retry_policy_from_cfg(agent_cfg)
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _invoke(adp: Any, msgs: list[dict[str, Any]]):
            return await _invoke_with_retry(adp, msgs, policy=retry_policy)

        def _user_msgs(case: TestCase) -> list[dict[str, Any]]:
            return [
                m for m in (case.input_messages or [])
                if isinstance(m, dict) and m.get("role") == "user"
            ]

        async def _fill_single(case: TestCase) -> None:
            users = _user_msgs(case)
            if not users:
                return
            # Reuse self.adapter (already open, thread_id=None) for single-turn;
            # each single-turn invoke is independent so no context bleed.
            question = str(users[-1].get("content", "")).strip()
            if not question:
                return
            try:
                resp, _ = await _invoke(self.adapter, [{"role": "user", "content": question}])
            except Exception as e:  # noqa: BLE001 — one bad case shouldn't sink the batch
                logger.warning("fill_expected single-turn failed for %s: %s", case.name, e)
                return
            answer = (resp.content or "").strip()
            if answer:
                case.expected_output = answer

        async def _fill_multi(case: TestCase) -> None:
            # Multi-turn needs a *fresh* adapter with a stable thread_id so the
            # server-side agent keeps conversation context across turns.
            thread_id = f"gen-{(case.name or 'conv')[:24]}-{uuid.uuid4().hex[:8]}"
            adapter = _make_adapter(agent_cfg, thread_id=thread_id, client=http_client)
            try:
                replay = await multiturn.replay_conversation(
                    adapter=adapter,
                    agent_type=agent_type,
                    input_messages=case.input_messages or [],
                    invoke=_invoke,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("fill_expected multi-turn failed for %s: %s", case.name, e)
                return
            finally:
                try:
                    await adapter.close()
                except Exception:
                    pass

            turns = replay.get("turns") or []
            # Map input_messages index -> assistant reply for this turn.
            by_index = {
                t["turn_index"]: (t.get("assistant") or "").strip()
                for t in turns
                if isinstance(t, dict) and t.get("turn_index") is not None
            }
            # Per-turn backfill: align turn_expectations[i].turn_index to replay.
            for te in (case.turn_expectations or []):
                ans = by_index.get(te.turn_index)
                if ans:
                    te.expected_output = ans
            # Case-level expected_output = last turn's reply (single-value view).
            if turns:
                last = (turns[-1].get("assistant") or "").strip()
                if last:
                    case.expected_output = last

        async def _run(case: TestCase) -> None:
            async with sem:
                if len(_user_msgs(case)) > 1:
                    await _fill_multi(case)
                else:
                    await _fill_single(case)

        await asyncio.gather(*(_run(c) for c in cases))
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
        prose ("好的，以下是测试题：…") and/or a ```json fence. For each
        candidate (whole text → fenced block → first [...]/{...} substring) we
        try a strict ``json.loads`` first, then a lenient repair pass that fixes
        the JSON errors LLMs commonly emit — most importantly unescaped double
        quotes inside string values (observed: 查询"驱动轮"配件) and trailing
        commas. Returns the parsed object, or None if nothing parses."""
        if not content:
            return None
        text = content.strip()

        import re

        candidates: list[str] = [text]
        fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
        if fence:
            candidates.append(fence.group(1).strip())
        for open_ch, close_ch in (("[", "]"), ("{", "}")):
            start = text.find(open_ch)
            end = text.rfind(close_ch)
            if start != -1 and end != -1 and end > start:
                candidates.append(text[start : end + 1])

        for cand in candidates:
            # strict first — never let the repair pass touch valid JSON
            try:
                return json.loads(cand)
            except json.JSONDecodeError:
                pass
            repaired = CaseGenerator._repair_json(cand)
            if repaired is not None and repaired != cand:
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass
        return None

    @staticmethod
    def _repair_json(s: str) -> str | None:
        """Repair the JSON mistakes LLMs commonly make, without a 3rd-party dep.

        Handles:
        * trailing commas before ] or }
        * bare (unescaped) double quotes inside string values — the dominant
          failure (e.g. ``"...查询"驱动轮"配件..."``). We scan char-by-char
          tracking string state; a `"` seen inside a string that is NOT a
          legitimate closer (i.e. not followed by a structural char
          , : ] } or EOF) is escaped to ``\\"``.

        Returns the repaired string, or None if the input doesn't look like JSON.
        """
        if not s:
            return None
        out: list[str] = []
        in_str = False
        escaped = False
        n = len(s)
        for i, ch in enumerate(s):
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == '"':
                if not in_str:
                    in_str = True
                    out.append(ch)
                else:
                    # look ahead past whitespace for the next non-space char
                    j = i + 1
                    while j < n and s[j] in " \t\r\n":
                        j += 1
                    nxt = s[j] if j < n else ""
                    if nxt in ",:]}" or nxt == "":
                        # legitimate string close
                        in_str = False
                        out.append(ch)
                    else:
                        # bare quote inside a string value → escape it
                        out.append('\\"')
                continue
            out.append(ch)

        repaired = "".join(out)
        # drop trailing commas:  , ]  /  , }
        import re
        repaired = re.sub(r",(\s*[\]}])", r"\1", repaired)
        return repaired
