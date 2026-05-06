from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_eval.data.case_generator import CaseGenerator
from agent_eval.models.test_case import TestCase


def _make_mock_llm(response_text: str) -> MagicMock:
    llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = response_text
    llm.ainvoke = AsyncMock(return_value=mock_response)
    return llm


VALID_JSON_RESPONSE = json.dumps([
    {
        "name": "test-case-1",
        "description": "Tests basic greeting",
        "input_messages": [{"role": "user", "content": "Hello"}],
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


class TestParseCases:

    def test_valid_json_array(self):
        gen = CaseGenerator(llm=MagicMock())
        cases = gen._parse_cases(VALID_JSON_RESPONSE, source="auto_generated")

        assert len(cases) == 2
        assert cases[0].name == "test-case-1"
        assert cases[0].source == "auto_generated"
        assert cases[0].input_messages == [{"role": "user", "content": "Hello"}]

    def test_json_in_code_block(self):
        gen = CaseGenerator(llm=MagicMock())
        cases = gen._parse_cases(VALID_JSON_IN_CODE_BLOCK, source="auto_generated")

        assert len(cases) == 2
        assert cases[0].name == "test-case-1"

    def test_invalid_json_returns_empty(self):
        gen = CaseGenerator(llm=MagicMock())
        cases = gen._parse_cases("this is not json", source="auto_generated")

        assert cases == []

    def test_single_object_wrapped_in_list(self):
        single = json.dumps({
            "name": "single",
            "input_messages": [{"role": "user", "content": "hi"}],
        })
        gen = CaseGenerator(llm=MagicMock())
        cases = gen._parse_cases(single, source="auto_generated")

        assert len(cases) == 1
        assert cases[0].name == "single"

    def test_missing_fields_use_defaults(self):
        minimal = json.dumps([{"input_messages": [{"role": "user", "content": "test"}]}])
        gen = CaseGenerator(llm=MagicMock())
        cases = gen._parse_cases(minimal, source="auto_generated")

        assert len(cases) == 1
        assert cases[0].name == "auto-generated"
        assert cases[0].tags == []
        assert cases[0].expected_output_criteria == []


class TestGenerateFromScenario:

    @pytest.mark.asyncio
    async def test_generates_cases(self):
        llm = _make_mock_llm(VALID_JSON_RESPONSE)
        gen = CaseGenerator(llm=llm)

        cases = await gen.generate_from_scenario("Test multi-turn dialogue", count=2)

        assert len(cases) == 2
        llm.ainvoke.assert_called_once()
        prompt = llm.ainvoke.call_args[0][0]
        assert "multi-turn dialogue" in prompt

    @pytest.mark.asyncio
    async def test_applies_custom_tags(self):
        llm = _make_mock_llm(VALID_JSON_RESPONSE)
        gen = CaseGenerator(llm=llm)

        cases = await gen.generate_from_scenario("test", tags=["custom-tag"])

        for case in cases:
            assert "custom-tag" in case.tags

    @pytest.mark.asyncio
    async def test_source_is_auto_generated(self):
        llm = _make_mock_llm(VALID_JSON_RESPONSE)
        gen = CaseGenerator(llm=llm)

        cases = await gen.generate_from_scenario("test")

        for case in cases:
            assert case.source == "auto_generated"

    @pytest.mark.asyncio
    async def test_handles_llm_error_gracefully(self):
        llm = _make_mock_llm("Sorry, I cannot generate test cases.")
        gen = CaseGenerator(llm=llm)

        cases = await gen.generate_from_scenario("test")

        assert cases == []


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
        llm = _make_mock_llm(VALID_JSON_RESPONSE)
        gen = CaseGenerator(llm=llm)

        cases = await gen.generate_mutations(source_case, count=2)

        for case in cases:
            assert case.parent_case_id == source_case.id

    @pytest.mark.asyncio
    async def test_includes_strategy_in_prompt(self, source_case):
        llm = _make_mock_llm(VALID_JSON_RESPONSE)
        gen = CaseGenerator(llm=llm)

        await gen.generate_mutations(source_case, strategy="adversarial")

        prompt = llm.ainvoke.call_args[0][0]
        assert "adversarial" in prompt

    @pytest.mark.asyncio
    async def test_applies_custom_tags(self, source_case):
        llm = _make_mock_llm(VALID_JSON_RESPONSE)
        gen = CaseGenerator(llm=llm)

        cases = await gen.generate_mutations(source_case, tags=["mutation-test"])

        for case in cases:
            assert "mutation-test" in case.tags

    @pytest.mark.asyncio
    async def test_passes_source_content_to_prompt(self, source_case):
        llm = _make_mock_llm(VALID_JSON_RESPONSE)
        gen = CaseGenerator(llm=llm)

        await gen.generate_mutations(source_case)

        prompt = llm.ainvoke.call_args[0][0]
        assert "Help me log in" in prompt
        assert "original-case" in prompt
