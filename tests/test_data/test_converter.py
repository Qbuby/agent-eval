from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent_eval.data.converter import (
    case_to_example,
    example_to_test_case,
    external_example_to_test_case,
    is_native_example,
)
from agent_eval.models.test_case import EvalWeights, TestCase, ToolCallExpectation


@dataclass
class FakeExample:
    """Mimics a LangSmith Example object for testing."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    dataset_id: uuid.UUID = field(default_factory=uuid.uuid4)
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def _make_test_case(**overrides: Any) -> TestCase:
    defaults = {
        "dataset_version": "v1",
        "name": "test-login",
        "description": "Test user login flow",
        "tags": ["auth", "functional"],
        "source": "manual",
        "input_messages": [
            {"role": "user", "content": "Help me log in"},
        ],
        "expected_output": "Login successful",
        "expected_output_criteria": ["mentions success", "no error"],
        "expected_tool_calls": [
            ToolCallExpectation(
                tool_name="authenticate",
                args_matcher={"username": "test"},
                order=0,
                required=True,
                allow_retry=False,
            ),
        ],
        "max_latency_ms": 5000,
        "max_tokens": 2048,
        "max_tool_calls": 5,
        "scoring_mode": "hybrid",
        "eval_weights": EvalWeights(
            output_correctness=0.4,
            tool_sequence_correctness=0.2,
            reasoning_quality=0.2,
            performance=0.1,
            error_recovery=0.1,
        ),
        "parent_case_id": "parent-123",
    }
    defaults.update(overrides)
    return TestCase(**defaults)


class TestTestCaseToExample:

    def test_basic_conversion(self):
        case = _make_test_case()
        result = case_to_example(case)

        assert "inputs" in result
        assert "outputs" in result
        assert "metadata" in result

    def test_inputs_mapping(self):
        case = _make_test_case()
        result = case_to_example(case)

        assert result["inputs"]["messages"] == case.input_messages

    def test_inputs_with_agent_config_override(self):
        case = _make_test_case(agent_config_override={"temperature": 0.5})
        result = case_to_example(case)

        assert result["inputs"]["agent_config_override"] == {"temperature": 0.5}

    def test_inputs_without_agent_config_override(self):
        case = _make_test_case(agent_config_override=None)
        result = case_to_example(case)

        assert "agent_config_override" not in result["inputs"]

    def test_outputs_mapping(self):
        case = _make_test_case()
        result = case_to_example(case)

        assert result["outputs"]["expected_output"] == "Login successful"
        assert result["outputs"]["expected_criteria"] == ["mentions success", "no error"]
        assert len(result["outputs"]["expected_tool_calls"]) == 1
        assert result["outputs"]["expected_tool_calls"][0]["tool_name"] == "authenticate"

    def test_outputs_empty_when_no_expectations(self):
        case = _make_test_case(
            expected_output=None,
            expected_output_criteria=[],
            expected_tool_calls=[],
        )
        result = case_to_example(case)

        assert result["outputs"] == {}

    def test_metadata_mapping(self):
        case = _make_test_case()
        result = case_to_example(case)
        meta = result["metadata"]

        assert meta["agent_eval_version"] == "1"
        assert meta["source"] == "manual"
        assert meta["tags"] == ["auth", "functional"]
        assert meta["scoring_mode"] == "hybrid"
        assert meta["max_latency_ms"] == 5000
        assert meta["max_tokens"] == 2048
        assert meta["max_tool_calls"] == 5
        assert meta["parent_case_id"] == "parent-123"
        assert meta["eval_weights"]["output_correctness"] == 0.4

    def test_metadata_omits_none_optional_fields(self):
        case = _make_test_case(
            max_latency_ms=None, max_tokens=None, max_tool_calls=None, parent_case_id=None
        )
        result = case_to_example(case)
        meta = result["metadata"]

        assert "max_latency_ms" not in meta
        assert "max_tokens" not in meta
        assert "max_tool_calls" not in meta
        assert "parent_case_id" not in meta

    def test_split_included_when_provided(self):
        case = _make_test_case()
        result = case_to_example(case, split="safety")

        assert result["split"] == "safety"

    def test_split_absent_when_not_provided(self):
        case = _make_test_case()
        result = case_to_example(case)

        assert "split" not in result


class TestExampleToTestCase:

    def test_basic_conversion(self):
        ex = FakeExample(
            inputs={"messages": [{"role": "user", "content": "hello"}]},
            outputs={"expected_output": "world"},
            metadata={"agent_eval_version": "1", "source": "manual", "tags": ["test"]},
        )
        case = example_to_test_case(ex)

        assert case.id == str(ex.id)
        assert case.dataset_version == str(ex.dataset_id)
        assert case.input_messages == [{"role": "user", "content": "hello"}]
        assert case.expected_output == "world"
        assert case.source == "manual"
        assert case.tags == ["test"]

    def test_tool_calls_deserialization(self):
        ex = FakeExample(
            inputs={"messages": []},
            outputs={
                "expected_tool_calls": [
                    {
                        "tool_name": "search",
                        "args_matcher": {"query": "test"},
                        "order": 0,
                        "required": True,
                        "allow_retry": False,
                    }
                ]
            },
            metadata={},
        )
        case = example_to_test_case(ex)

        assert len(case.expected_tool_calls) == 1
        assert case.expected_tool_calls[0].tool_name == "search"
        assert case.expected_tool_calls[0].args_matcher == {"query": "test"}

    def test_eval_weights_deserialization(self):
        ex = FakeExample(
            inputs={"messages": []},
            outputs={},
            metadata={
                "eval_weights": {
                    "output_correctness": 0.5,
                    "tool_sequence_correctness": 0.2,
                    "reasoning_quality": 0.1,
                    "performance": 0.1,
                    "error_recovery": 0.1,
                }
            },
        )
        case = example_to_test_case(ex)

        assert case.eval_weights.output_correctness == 0.5

    def test_defaults_for_missing_metadata(self):
        ex = FakeExample(inputs={"messages": []}, outputs={}, metadata={})
        case = example_to_test_case(ex)

        assert case.source == "manual"
        assert case.scoring_mode == "hybrid"
        assert case.tags == []
        assert case.name == ""

    def test_optional_fields_from_metadata(self):
        ex = FakeExample(
            inputs={"messages": []},
            outputs={},
            metadata={
                "max_latency_ms": 3000,
                "max_tokens": 1024,
                "max_tool_calls": 10,
                "parent_case_id": "abc-123",
            },
        )
        case = example_to_test_case(ex)

        assert case.max_latency_ms == 3000
        assert case.max_tokens == 1024
        assert case.max_tool_calls == 10
        assert case.parent_case_id == "abc-123"


class TestRoundTrip:

    def test_roundtrip_preserves_data(self):
        original = _make_test_case()
        example_data = case_to_example(original)

        fake_example = FakeExample(
            inputs=example_data["inputs"],
            outputs=example_data["outputs"],
            metadata=example_data["metadata"],
        )
        restored = example_to_test_case(fake_example)

        assert restored.name == original.name
        assert restored.description == original.description
        assert restored.tags == original.tags
        assert restored.source == original.source
        assert restored.input_messages == original.input_messages
        assert restored.expected_output == original.expected_output
        assert restored.expected_output_criteria == original.expected_output_criteria
        assert restored.max_latency_ms == original.max_latency_ms
        assert restored.max_tokens == original.max_tokens
        assert restored.max_tool_calls == original.max_tool_calls
        assert restored.scoring_mode == original.scoring_mode
        assert restored.parent_case_id == original.parent_case_id

        assert restored.eval_weights.output_correctness == original.eval_weights.output_correctness
        assert restored.eval_weights.performance == original.eval_weights.performance

        assert len(restored.expected_tool_calls) == len(original.expected_tool_calls)
        assert restored.expected_tool_calls[0].tool_name == original.expected_tool_calls[0].tool_name
        assert restored.expected_tool_calls[0].args_matcher == original.expected_tool_calls[0].args_matcher

    def test_roundtrip_minimal_case(self):
        original = TestCase(
            dataset_version="v1",
            name="minimal",
            input_messages=[{"role": "user", "content": "hi"}],
        )
        example_data = case_to_example(original)
        fake_example = FakeExample(
            inputs=example_data["inputs"],
            outputs=example_data["outputs"],
            metadata=example_data["metadata"],
        )
        restored = example_to_test_case(fake_example)

        assert restored.name == "minimal"
        assert restored.input_messages == [{"role": "user", "content": "hi"}]
        assert restored.expected_output is None
        assert restored.expected_tool_calls == []


class TestIsNativeExample:

    def test_native_example(self):
        ex = FakeExample(metadata={"agent_eval_version": "1"})
        assert is_native_example(ex) is True

    def test_external_example(self):
        ex = FakeExample(metadata={"some_key": "value"})
        assert is_native_example(ex) is False

    def test_empty_metadata(self):
        ex = FakeExample(metadata={})
        assert is_native_example(ex) is False


class TestExternalExampleToTestCase:

    def test_messages_input(self):
        ex = FakeExample(
            inputs={"messages": [{"role": "user", "content": "What is AI?"}]},
            outputs={"answer": "Artificial Intelligence"},
        )
        case = external_example_to_test_case(ex)

        assert case.input_messages == [{"role": "user", "content": "What is AI?"}]
        assert case.expected_output == "Artificial Intelligence"
        assert case.source == "external"

    def test_question_answer_format(self):
        ex = FakeExample(
            inputs={"question": "What is Python?"},
            outputs={"answer": "A programming language"},
        )
        case = external_example_to_test_case(ex)

        assert case.input_messages == [{"role": "user", "content": "What is Python?"}]
        assert case.expected_output == "A programming language"

    def test_input_output_format(self):
        ex = FakeExample(
            inputs={"input": "Translate hello to French"},
            outputs={"output": "Bonjour"},
        )
        case = external_example_to_test_case(ex)

        assert case.input_messages == [{"role": "user", "content": "Translate hello to French"}]
        assert case.expected_output == "Bonjour"

    def test_query_response_format(self):
        ex = FakeExample(
            inputs={"query": "capital of France"},
            outputs={"response": "Paris"},
        )
        case = external_example_to_test_case(ex)

        assert case.input_messages == [{"role": "user", "content": "capital of France"}]
        assert case.expected_output == "Paris"

    def test_single_key_string_input(self):
        ex = FakeExample(
            inputs={"custom_field": "some question"},
            outputs={},
        )
        case = external_example_to_test_case(ex)

        assert case.input_messages == [{"role": "user", "content": "some question"}]

    def test_chat_history_format(self):
        ex = FakeExample(
            inputs={
                "chat_history": [
                    {"type": "human", "content": "Hi"},
                    {"type": "ai", "content": "Hello!"},
                    {"type": "human", "content": "How are you?"},
                ]
            },
            outputs={"output": "I'm fine"},
        )
        case = external_example_to_test_case(ex)

        assert len(case.input_messages) == 3
        assert case.input_messages[0] == {"role": "user", "content": "Hi"}
        assert case.input_messages[1] == {"role": "assistant", "content": "Hello!"}
        assert case.input_messages[2] == {"role": "user", "content": "How are you?"}

    def test_langchain_message_types(self):
        ex = FakeExample(
            inputs={
                "messages": [
                    {"type": "system", "content": "You are helpful"},
                    {"type": "human", "content": "Hello"},
                ]
            },
            outputs={},
        )
        case = external_example_to_test_case(ex)

        assert case.input_messages[0] == {"role": "system", "content": "You are helpful"}
        assert case.input_messages[1] == {"role": "user", "content": "Hello"}

    def test_no_output(self):
        ex = FakeExample(
            inputs={"input": "test"},
            outputs={},
        )
        case = external_example_to_test_case(ex)

        assert case.expected_output is None

    def test_criteria_from_outputs(self):
        ex = FakeExample(
            inputs={"input": "test"},
            outputs={"criteria": ["must be polite", "must be accurate"]},
        )
        case = external_example_to_test_case(ex)

        assert case.expected_output_criteria == ["must be polite", "must be accurate"]

    def test_criteria_from_metadata(self):
        ex = FakeExample(
            inputs={"input": "test"},
            outputs={},
            metadata={"assertions": ["check A", "check B"]},
        )
        case = external_example_to_test_case(ex)

        assert case.expected_output_criteria == ["check A", "check B"]

    def test_dataset_name_preserved(self):
        ex = FakeExample(inputs={"input": "test"}, outputs={})
        case = external_example_to_test_case(ex, dataset_name="my-external-ds")

        assert case.dataset_version == "my-external-ds"

    def test_name_auto_generated(self):
        ex = FakeExample(
            inputs={"input": "What is the meaning of life?"},
            outputs={},
            metadata={},
        )
        case = external_example_to_test_case(ex)

        assert case.name.startswith("ext-")
        assert str(ex.id)[:8] in case.name

    def test_name_from_metadata(self):
        ex = FakeExample(
            inputs={"input": "test"},
            outputs={},
            metadata={"name": "custom-name"},
        )
        case = external_example_to_test_case(ex)

        assert case.name == "custom-name"

    def test_tuple_message_format(self):
        ex = FakeExample(
            inputs={"messages": [("user", "hello"), ("assistant", "hi")]},
            outputs={},
        )
        case = external_example_to_test_case(ex)

        assert case.input_messages == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    def test_string_messages(self):
        ex = FakeExample(
            inputs={"messages": ["hello", "how are you"]},
            outputs={},
        )
        case = external_example_to_test_case(ex)

        assert case.input_messages == [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "how are you"},
        ]

    def test_output_from_messages(self):
        ex = FakeExample(
            inputs={"input": "test"},
            outputs={
                "messages": [
                    {"role": "assistant", "content": "Here is the answer"},
                ]
            },
        )
        case = external_example_to_test_case(ex)

        assert case.expected_output == "Here is the answer"
