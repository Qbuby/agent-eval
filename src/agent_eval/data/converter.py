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
    # 受管单值类别（多轮对话集用）：存类别名字符串，空则不写。
    if case.category:
        metadata["category"] = case.category
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
        category=meta.get("category"),
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


# ---------------------------------------------------------------------------
# Langfuse dataset item <-> TestCase
#
# Langfuse 的 DatasetItem 形状与 LangSmith Example 不同：
#   - inputs/outputs 双栏 → input/expected_output 双栏（字段名变、outputs 复数→单数）
#   - 没有 split 一等公民概念 → split 降级进 metadata["split"]
#   - DatasetItem 同时有 dataset_id + dataset_name
# 为避免字段映射在两套后端间分叉，这里直接复用上面的 case_to_example /
# example_to_test_case 作为单一事实源，只做 input/output 的形状适配。
# ---------------------------------------------------------------------------


def case_to_dataset_item(case: TestCase, split: str | None = None) -> dict[str, Any]:
    """TestCase → Langfuse create_dataset_item 入参三元组 + id。

    复用 case_to_example 的字段映射；inputs→input、outputs→expected_output。
    Langfuse 无 split，故把 split 收进 metadata（load 时按需过滤）。id 透传
    case.id（uuid），既作 Langfuse item 的全局唯一 id，也支撑 upsert 去重。
    """
    params = case_to_example(case, split=split)
    metadata = dict(params["metadata"])
    if params.get("split"):
        metadata["split"] = params["split"]
    return {
        "id": case.id,
        "input": params["inputs"],
        "expected_output": params["outputs"],
        "metadata": metadata,
    }


class _ItemAsExample:
    """把 Langfuse DatasetItem 适配成 example_to_test_case 期望的 Example 形状，
    从而复用同一套反序列化逻辑（input→inputs / expected_output→outputs）。"""

    def __init__(self, item: Any):
        self.inputs = getattr(item, "input", None) or {}
        self.outputs = getattr(item, "expected_output", None) or {}
        self.metadata = getattr(item, "metadata", None) or {}
        self.id = getattr(item, "id", "")
        # Langfuse 用 dataset_name 标识归属，比 dataset_id 更可读，作为 dataset_version。
        self.dataset_id = getattr(item, "dataset_name", "") or ""


def dataset_item_to_test_case(item: Any) -> TestCase:
    """Langfuse DatasetItem → TestCase（复用 example_to_test_case）。"""
    return example_to_test_case(_ItemAsExample(item))
