from __future__ import annotations

import pytest

from agent_eval.governance.validator import ExampleValidator, ValidationSeverity


class TestExampleValidator:
    def setup_method(self):
        self.validator = ExampleValidator()

    def test_valid_example(self):
        example = {
            "name": "test case",
            "input_messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
            "expected_output": "Hi there",
        }
        result = self.validator.validate_example(example)
        assert result.valid is True
        assert result.needs_review is False
        assert len(result.issues) == 0

    def test_missing_input_messages(self):
        example = {"name": "test"}
        result = self.validator.validate_example(example)
        assert result.valid is False
        assert result.needs_review is True
        assert any(i.field == "input_messages" for i in result.issues)

    def test_empty_input_messages(self):
        example = {"name": "test", "input_messages": []}
        result = self.validator.validate_example(example)
        assert result.valid is False
        assert any("empty" in i.message for i in result.issues)

    def test_invalid_message_format(self):
        example = {"name": "test", "input_messages": ["not a dict"]}
        result = self.validator.validate_example(example)
        assert result.valid is False

    def test_message_missing_role(self):
        example = {"name": "test", "input_messages": [{"content": "hello"}]}
        result = self.validator.validate_example(example)
        assert result.valid is False
        assert any("role" in i.message for i in result.issues)

    def test_message_missing_content(self):
        example = {"name": "test", "input_messages": [{"role": "user"}]}
        result = self.validator.validate_example(example)
        assert result.valid is False
        assert any("content" in i.message for i in result.issues)

    def test_unexpected_role_is_warning(self):
        example = {
            "name": "test",
            "input_messages": [{"role": "custom_role", "content": "hello"}],
        }
        result = self.validator.validate_example(example)
        assert result.valid is True
        warnings = [i for i in result.issues if i.severity == ValidationSeverity.WARNING]
        assert len(warnings) == 1

    def test_require_expected_output(self):
        validator = ExampleValidator(config={"require_expected_output": True})
        example = {
            "name": "test",
            "input_messages": [{"role": "user", "content": "hello"}],
        }
        result = validator.validate_example(example)
        assert result.valid is False
        assert any(i.field == "expected_output" for i in result.issues)

    def test_expected_output_criteria_satisfies_requirement(self):
        validator = ExampleValidator(config={"require_expected_output": True})
        example = {
            "name": "test",
            "input_messages": [{"role": "user", "content": "hello"}],
            "expected_output_criteria": ["contains greeting"],
        }
        result = validator.validate_example(example)
        assert result.valid is True

    def test_invalid_tool_calls_format(self):
        example = {
            "name": "test",
            "input_messages": [{"role": "user", "content": "hello"}],
            "expected_tool_calls": "not a list",
        }
        result = self.validator.validate_example(example)
        assert result.valid is False
        assert any(i.field == "expected_tool_calls" for i in result.issues)

    def test_tool_call_missing_name(self):
        example = {
            "name": "test",
            "input_messages": [{"role": "user", "content": "hello"}],
            "expected_tool_calls": [{"args": {}}],
        }
        result = self.validator.validate_example(example)
        assert result.valid is False

    def test_empty_name_warning(self):
        example = {
            "name": "",
            "input_messages": [{"role": "user", "content": "hello"}],
        }
        result = self.validator.validate_example(example)
        assert result.valid is True
        assert any(i.field == "name" for i in result.issues)

    def test_max_messages_warning(self):
        validator = ExampleValidator(config={"max_messages": 2})
        example = {
            "name": "test",
            "input_messages": [
                {"role": "user", "content": "1"},
                {"role": "assistant", "content": "2"},
                {"role": "user", "content": "3"},
            ],
        }
        result = validator.validate_example(example)
        assert result.valid is True
        assert any("exceeds" in i.message for i in result.issues)

    def test_validate_batch(self):
        examples = [
            {"name": "good", "input_messages": [{"role": "user", "content": "hi"}]},
            {"name": "bad", "input_messages": []},
            {"name": "also_good", "input_messages": [{"role": "user", "content": "hey"}]},
        ]
        report = self.validator.validate_batch(examples)
        assert report.total == 3
        assert report.valid == 2
        assert report.needs_review == 1
        assert "input_messages" in report.issues_by_field
