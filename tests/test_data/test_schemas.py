from __future__ import annotations

import pytest

from agent_eval.data.schemas import validate_and_parse


class TestValidateAndParse:

    def test_valid_single_case(self):
        data = {
            "name": "test-1",
            "input_messages": [{"role": "user", "content": "hello"}],
        }
        result = validate_and_parse(data)

        assert result.valid is True
        assert len(result.cases) == 1
        assert result.cases[0].name == "test-1"
        assert result.errors == []

    def test_valid_array(self):
        data = [
            {"name": "a", "input_messages": [{"role": "user", "content": "hi"}]},
            {"name": "b", "input_messages": [{"role": "user", "content": "bye"}]},
        ]
        result = validate_and_parse(data)

        assert result.valid is True
        assert len(result.cases) == 2

    def test_invalid_type_string(self):
        result = validate_and_parse("not a dict or list")

        assert result.valid is False
        assert len(result.errors) == 1
        assert "JSON object or array" in result.errors[0]

    def test_invalid_item_in_array(self):
        data = [
            {"name": "good", "input_messages": [{"role": "user", "content": "hi"}]},
            "not a dict",
        ]
        result = validate_and_parse(data)

        assert result.valid is False
        assert len(result.cases) == 1
        assert len(result.errors) == 1

    def test_missing_required_field(self):
        data = {"name": "no-messages"}
        result = validate_and_parse(data)

        assert result.valid is False
        assert len(result.errors) == 1

    def test_dataset_version_auto_set(self):
        data = {"name": "test", "input_messages": [{"role": "user", "content": "test"}]}
        result = validate_and_parse(data)

        assert result.valid is True
        assert result.cases[0].dataset_version == ""

    def test_empty_array(self):
        result = validate_and_parse([])

        assert result.valid is True
        assert len(result.cases) == 0

    def test_full_case_with_all_fields(self):
        data = {
            "name": "full",
            "description": "A full test case",
            "input_messages": [{"role": "user", "content": "test"}],
            "expected_output": "response",
            "expected_output_criteria": ["criterion 1"],
            "tags": ["tag1", "tag2"],
            "scoring_mode": "llm",
            "source": "manual",
        }
        result = validate_and_parse(data)

        assert result.valid is True
        case = result.cases[0]
        assert case.name == "full"
        assert case.expected_output == "response"
        assert case.scoring_mode == "llm"
        assert case.tags == ["tag1", "tag2"]
