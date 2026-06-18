from __future__ import annotations

from typing import Any

from agent_eval.data._utils import normalize_messages
from agent_eval.models.test_case import (
    EvalWeights,
    TestCase,
    ToolCallExpectation,
    TurnExpectation,
)

SCHEMA_VERSION = "1"

# inputs 中常见的消息类字段名
_MESSAGE_KEYS = ("messages", "input_messages", "chat_history", "conversation")
# inputs 中常见的单文本输入字段名
_TEXT_INPUT_KEYS = ("input", "question", "query", "prompt", "text", "user_input", "human_input")
# outputs 中常见的输出字段名
_TEXT_OUTPUT_KEYS = ("output", "answer", "response", "result", "text", "content", "expected_output")


def case_to_example(case: TestCase, split: str | None = None) -> dict[str, Any]:
    inputs: dict[str, Any] = {"messages": case.input_messages}
    if case.agent_config_override:
        inputs["agent_config_override"] = case.agent_config_override

    outputs: dict[str, Any] = {}
    if case.expected_output:
        outputs["expected_output"] = case.expected_output
    if case.expected_output_criteria:
        outputs["expected_criteria"] = case.expected_output_criteria
    if case.expected_tool_calls:
        outputs["expected_tool_calls"] = [tc.model_dump() for tc in case.expected_tool_calls]
    if case.conversation_goal:
        outputs["conversation_goal"] = case.conversation_goal
    if case.turn_expectations:
        outputs["turn_expectations"] = [te.model_dump() for te in case.turn_expectations]

    metadata: dict[str, Any] = {
        "agent_eval_version": SCHEMA_VERSION,
        "source": case.source,
        "tags": case.tags,
        "eval_weights": case.eval_weights.model_dump(),
        "scoring_mode": case.scoring_mode,
    }
    # 多轮对话样例标记：便于前端/导入按类型区分，单轮老数据不带这些字段
    if case.conversation_goal or case.turn_expectations:
        metadata["case_type"] = "conversation"
        metadata["turn_count"] = len(case.input_messages)
    if case.name:
        metadata["name"] = case.name
    if case.description:
        metadata["description"] = case.description
    for field_name in ("max_tool_calls", "max_latency_ms", "max_tokens", "parent_case_id"):
        value = getattr(case, field_name, None)
        if value is not None:
            metadata[field_name] = value

    result: dict[str, Any] = {"inputs": inputs, "outputs": outputs, "metadata": metadata}
    if split:
        result["split"] = split
    return result


def example_to_test_case(example: Any) -> TestCase:
    inputs = example.inputs or {}
    outputs = example.outputs or {}
    meta = example.metadata or {}

    tool_calls_raw = outputs.get("expected_tool_calls", [])
    tool_calls = [ToolCallExpectation(**tc) for tc in tool_calls_raw]

    # 多轮字段：无标记的单轮老数据这两个字段缺省为空，向后兼容
    turn_exp_raw = outputs.get("turn_expectations", [])
    turn_expectations = [TurnExpectation(**te) for te in turn_exp_raw]

    return TestCase(
        id=str(example.id),
        dataset_version=str(example.dataset_id) if hasattr(example, "dataset_id") else "",
        name=meta.get("name", ""),
        description=meta.get("description", ""),
        tags=meta.get("tags", []),
        source=meta.get("source", "manual"),
        input_messages=inputs.get("messages", []),
        agent_config_override=inputs.get("agent_config_override"),
        expected_output=outputs.get("expected_output"),
        expected_output_criteria=outputs.get("expected_criteria", []),
        expected_tool_calls=tool_calls,
        conversation_goal=outputs.get("conversation_goal"),
        turn_expectations=turn_expectations,
        max_tool_calls=meta.get("max_tool_calls"),
        max_latency_ms=meta.get("max_latency_ms"),
        max_tokens=meta.get("max_tokens"),
        eval_weights=EvalWeights(**meta.get("eval_weights", {})),
        scoring_mode=meta.get("scoring_mode", "hybrid"),
        parent_case_id=meta.get("parent_case_id"),
    )


def is_native_example(example: Any) -> bool:
    """Check whether an example was created by this system."""
    meta = getattr(example, "metadata", None) or {}
    return meta.get("agent_eval_version") is not None


def external_example_to_test_case(example: Any, dataset_name: str = "") -> TestCase:
    """Convert a LangSmith Example with arbitrary schema into a TestCase.

    Handles common patterns found in external datasets:
    - inputs with "messages" list (chat format)
    - inputs with a single text field ("input", "question", "query", etc.)
    - outputs with a single text field ("output", "answer", "response", etc.)
    - outputs with "messages" list
    """
    inputs = example.inputs or {}
    outputs = example.outputs or {}
    meta = getattr(example, "metadata", None) or {}

    input_messages = _extract_messages(inputs)
    expected_output = _extract_output_text(outputs)

    name = meta.get("name", "")
    if not name:
        preview = ""
        if input_messages:
            preview = input_messages[-1].get("content", "")[:40]
        name = f"ext-{str(example.id)[:8]}" + (f"-{preview}" if preview else "")

    return TestCase(
        id=str(example.id),
        dataset_version=dataset_name,
        name=name,
        description=meta.get("description", ""),
        tags=meta.get("tags", []),
        source="external",
        input_messages=input_messages,
        expected_output=expected_output,
        expected_output_criteria=_extract_criteria(outputs, meta),
    )


def _extract_messages(inputs: dict[str, Any]) -> list[dict[str, str]]:
    """Try to extract a messages list from various input formats."""
    for key in _MESSAGE_KEYS:
        if key in inputs and isinstance(inputs[key], list):
            return normalize_messages(inputs[key])

    for key in _TEXT_INPUT_KEYS:
        if key in inputs:
            text = str(inputs[key])
            if text:
                return [{"role": "user", "content": text}]

    if len(inputs) == 1:
        val = next(iter(inputs.values()))
        if isinstance(val, str):
            return [{"role": "user", "content": val}]
        if isinstance(val, list):
            return normalize_messages(val)

    if inputs:
        return [{"role": "user", "content": str(inputs)}]

    return []


def _extract_output_text(outputs: dict[str, Any]) -> str | None:
    """Try to extract expected output text from various output formats."""
    for key in _TEXT_OUTPUT_KEYS:
        if key in outputs:
            val = outputs[key]
            if isinstance(val, str) and val:
                return val

    if "messages" in outputs and isinstance(outputs["messages"], list):
        msgs = outputs["messages"]
        for msg in reversed(msgs):
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if content:
                    return str(content)

    if len(outputs) == 1:
        val = next(iter(outputs.values()))
        if isinstance(val, str) and val:
            return val

    return None


def _extract_criteria(outputs: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    """Try to extract evaluation criteria from outputs or metadata."""
    for key in ("criteria", "expected_criteria", "assertions", "checks"):
        for source in (outputs, meta):
            if key in source and isinstance(source[key], list):
                return [str(c) for c in source[key]]
    return []
