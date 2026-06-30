from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_eval.data.case_generator import CaseGenerator
from agent_eval.evaluation.agent_adapter import AgentResponse
from agent_eval.models.test_case import TestCase


def _make_mock_adapter(response_text: str) -> MagicMock:
    """Mock an agent adapter whose invoke() returns an AgentResponse.

    The generator now drives the agent under test (KG-grounded) rather than a
    bare LLM: it calls ``adapter.invoke([{role, content}])`` and parses the
    JSON array out of ``AgentResponse.content``.
    """
    adapter = MagicMock()
    adapter.invoke = AsyncMock(
        return_value=AgentResponse(content=response_text, latency_ms=1.0)
    )
    return adapter


VALID_JSON_RESPONSE = json.dumps([
    {
        "name": "test-case-1",
        "description": "Tests basic greeting",
        "input_messages": [{"role": "user", "content": "Hello"}],
        "expected_output": "你好，有什么可以帮你？",
        "expected_output_criteria": ["Should respond politely"],
        "tags": ["greeting"],
    },
    {
        "name": "test-case-2",
        "description": "Tests farewell",
        "input_messages": [{"role": "user", "content": "Goodbye"}],
        "expected_output_criteria": ["Should say goodbye"],
        "tags": ["farewell"],
    },
])

VALID_JSON_IN_CODE_BLOCK = f"```json\n{VALID_JSON_RESPONSE}\n```"


def _prompt_of(adapter: MagicMock) -> str:
    """Extract the prompt text from the recorded adapter.invoke call.

    invoke is called as invoke([{"role": "user", "content": <prompt>}]).
    """
    messages = adapter.invoke.call_args[0][0]
    return messages[0]["content"]


class TestParseCases:

    def test_valid_json_array(self):
        gen = CaseGenerator(adapter=MagicMock())
        cases = gen._parse_cases(VALID_JSON_RESPONSE, source="auto_generated")

        assert len(cases) == 2
        assert cases[0].name == "test-case-1"
        assert cases[0].source == "auto_generated"
        assert cases[0].input_messages == [{"role": "user", "content": "Hello"}]
        # expected_output (KG ground-truth) is captured when present
        assert cases[0].expected_output == "你好，有什么可以帮你？"

    def test_json_in_code_block(self):
        gen = CaseGenerator(adapter=MagicMock())
        cases = gen._parse_cases(VALID_JSON_IN_CODE_BLOCK, source="auto_generated")

        assert len(cases) == 2
        assert cases[0].name == "test-case-1"

    def test_json_embedded_in_agent_prose(self):
        """Agents frequently wrap the JSON array in explanatory prose and/or a
        fence. _parse_cases must still recover the cases — this is the dominant
        failure mode when driving a chat agent instead of a tight LLM."""
        text = "好的，以下是测试题：\n" + VALID_JSON_IN_CODE_BLOCK + "\n希望对你有帮助。"
        gen = CaseGenerator(adapter=MagicMock())
        cases = gen._parse_cases(text, source="auto_generated")
        assert len(cases) == 2
        assert cases[0].name == "test-case-1"

    def test_json_array_in_bare_prose_no_fence(self):
        """Even without a code fence, a [...] array embedded in prose is
        recovered via the bracket-substring fallback."""
        text = "当然可以！这是 2 道题：" + VALID_JSON_RESPONSE + " 以上。"
        gen = CaseGenerator(adapter=MagicMock())
        cases = gen._parse_cases(text, source="auto_generated")
        assert len(cases) == 2

    def test_invalid_json_returns_empty(self):
        gen = CaseGenerator(adapter=MagicMock())
        cases = gen._parse_cases("this is not json", source="auto_generated")

        assert cases == []

    def test_single_object_wrapped_in_list(self):
        single = json.dumps({
            "name": "single",
            "input_messages": [{"role": "user", "content": "hi"}],
        })
        gen = CaseGenerator(adapter=MagicMock())
        cases = gen._parse_cases(single, source="auto_generated")

        assert len(cases) == 1
        assert cases[0].name == "single"

    def test_missing_fields_use_defaults(self):
        minimal = json.dumps([{"input_messages": [{"role": "user", "content": "test"}]}])
        gen = CaseGenerator(adapter=MagicMock())
        cases = gen._parse_cases(minimal, source="auto_generated")

        assert len(cases) == 1
        assert cases[0].name == "auto-generated"
        assert cases[0].tags == []
        assert cases[0].expected_output_criteria == []


class TestGenerateFromScenario:

    @pytest.mark.asyncio
    async def test_generates_cases(self):
        adapter = _make_mock_adapter(VALID_JSON_RESPONSE)
        gen = CaseGenerator(adapter=adapter)

        cases = await gen.generate_from_scenario("多轮对话", count=2)

        assert len(cases) == 2
        adapter.invoke.assert_called_once()
        assert "多轮对话" in _prompt_of(adapter)

    @pytest.mark.asyncio
    async def test_empty_scenario_is_allowed(self):
        """test_scenario is now optional — blank scenario still generates,
        with a free-generation hint substituted into the prompt."""
        adapter = _make_mock_adapter(VALID_JSON_RESPONSE)
        gen = CaseGenerator(adapter=adapter)

        cases = await gen.generate_from_scenario("", count=2)

        assert len(cases) == 2
        adapter.invoke.assert_called_once()
        # blank scenario => the free-form hint is in the prompt
        assert "自由出题" in _prompt_of(adapter)

    @pytest.mark.asyncio
    async def test_applies_custom_tags(self):
        adapter = _make_mock_adapter(VALID_JSON_RESPONSE)
        gen = CaseGenerator(adapter=adapter)

        cases = await gen.generate_from_scenario("test", tags=["custom-tag"])

        for case in cases:
            assert "custom-tag" in case.tags

    @pytest.mark.asyncio
    async def test_source_is_auto_generated(self):
        adapter = _make_mock_adapter(VALID_JSON_RESPONSE)
        gen = CaseGenerator(adapter=adapter)

        cases = await gen.generate_from_scenario("test")

        for case in cases:
            assert case.source == "auto_generated"

    @pytest.mark.asyncio
    async def test_handles_unparseable_reply_gracefully(self):
        adapter = _make_mock_adapter("抱歉，我无法生成测试用例。")
        gen = CaseGenerator(adapter=adapter)

        cases = await gen.generate_from_scenario("test")

        assert cases == []

    @pytest.mark.asyncio
    async def test_invokes_agent_as_user_turn(self):
        """The generation instruction must be sent as a user message so the
        agent treats it as a normal question against its knowledge graph."""
        adapter = _make_mock_adapter(VALID_JSON_RESPONSE)
        gen = CaseGenerator(adapter=adapter)

        await gen.generate_from_scenario("test")

        messages = adapter.invoke.call_args[0][0]
        assert isinstance(messages, list)
        assert messages[0]["role"] == "user"
        assert isinstance(messages[0]["content"], str)


class TestGenerateMutations:

    @pytest.fixture
    def source_case(self):
        return TestCase(
            dataset_version="v1",
            name="original-case",
            description="Tests login",
            input_messages=[{"role": "user", "content": "Help me log in"}],
            expected_output="Login successful",
            expected_output_criteria=["mentions success"],
        )

    @pytest.mark.asyncio
    async def test_sets_parent_case_id(self, source_case):
        adapter = _make_mock_adapter(VALID_JSON_RESPONSE)
        gen = CaseGenerator(adapter=adapter)

        cases = await gen.generate_mutations(source_case, count=2)

        for case in cases:
            assert case.parent_case_id == source_case.id

    @pytest.mark.asyncio
    async def test_includes_strategy_in_prompt(self, source_case):
        adapter = _make_mock_adapter(VALID_JSON_RESPONSE)
        gen = CaseGenerator(adapter=adapter)

        await gen.generate_mutations(source_case, strategy="adversarial")

        assert "adversarial" in _prompt_of(adapter)

    @pytest.mark.asyncio
    async def test_applies_custom_tags(self, source_case):
        adapter = _make_mock_adapter(VALID_JSON_RESPONSE)
        gen = CaseGenerator(adapter=adapter)

        cases = await gen.generate_mutations(source_case, tags=["mutation-test"])

        for case in cases:
            assert "mutation-test" in case.tags

    @pytest.mark.asyncio
    async def test_passes_source_content_to_prompt(self, source_case):
        adapter = _make_mock_adapter(VALID_JSON_RESPONSE)
        gen = CaseGenerator(adapter=adapter)

        await gen.generate_mutations(source_case)

        prompt = _prompt_of(adapter)
        assert "Help me log in" in prompt
        assert "original-case" in prompt


class TestJsonRepair:
    """LLM-authored JSON often has unescaped double quotes inside string
    values (the dominant real-world failure: 查询"驱动轮"配件) and trailing
    commas. _extract_json must repair these rather than yield 0 cases."""

    def test_bare_quotes_inside_string_value(self):
        # mirrors the real agent reply that produced generated:0
        bad = (
            '[{"name": "配件查询", '
            '"description": "考察BOM查询", '
            '"input_messages": [{"role": "user", "content": "查驱动轮"}], '
            '"expected_output": "应通过bom_parts_by_name查询"驱动轮"配件，再返回物料号", '
            '"expected_output_criteria": ["调用了工具"], '
            '"tags": ["BOM"]}]'
        )
        gen = CaseGenerator(adapter=MagicMock())
        cases = gen._parse_cases(bad, source="auto_generated")
        assert len(cases) == 1
        assert cases[0].name == "配件查询"
        # the inner quoted phrase survives in expected_output
        assert "驱动轮" in (cases[0].expected_output or "")

    def test_trailing_comma(self):
        bad = '[{"name": "a", "input_messages": [{"role":"user","content":"x"}],},]'
        gen = CaseGenerator(adapter=MagicMock())
        cases = gen._parse_cases(bad, source="auto_generated")
        assert len(cases) == 1
        assert cases[0].name == "a"

    def test_valid_json_untouched_by_repair(self):
        # repair must never corrupt already-valid JSON
        gen = CaseGenerator(adapter=MagicMock())
        cases = gen._parse_cases(VALID_JSON_RESPONSE, source="auto_generated")
        assert len(cases) == 2
        assert cases[0].name == "test-case-1"

    def test_unrepairable_returns_empty(self):
        gen = CaseGenerator(adapter=MagicMock())
        assert gen._parse_cases("总之这是一段没有任何结构的纯中文回复。", source="auto_generated") == []
