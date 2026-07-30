"""固化答案关键点注入 judge 的回归测试。"""
from agent_eval.evaluation.configurable_judge import (
    _build_comparative_messages,
    _build_messages,
)


def _user_content(messages: list[dict[str, str]]) -> str:
    return next(message["content"] for message in messages if message["role"] == "user")


def test_single_judge_appends_frozen_reference_criteria():
    messages = _build_messages(
        params={},
        input_text="问题",
        output_text="回答",
        expected_output="期望答案",
        metadata={"reference_criteria": ["必须提到 A", "不得声称 B"]},
    )

    user = _user_content(messages)
    assert "固化参考要点（必须逐条核对）" in user
    assert "1. 必须提到 A" in user
    assert "2. 不得声称 B" in user


def test_single_judge_does_not_duplicate_explicit_reference_criteria_mapping():
    messages = _build_messages(
        params={
            "evaluation_prompt": "问题：{{Query}}\n关键点：{{Criteria}}",
            "variable_mapping": {
                "Query": "input",
                "Criteria": "metadata.reference_criteria",
            },
        },
        input_text="问题",
        output_text="回答",
        expected_output="期望答案",
        metadata={"reference_criteria": ["唯一关键点"]},
    )

    user = _user_content(messages)
    assert user.count("唯一关键点") == 1
    assert "固化参考要点（必须逐条核对）" not in user


def test_comparative_judge_appends_frozen_reference_criteria():
    messages = _build_comparative_messages(
        params={},
        input_text="问题",
        output_a="回答 A",
        output_b="回答 B",
        expected_output="期望答案",
        metadata={"reference_criteria": ["比较时必须检查 C"]},
    )

    user = _user_content(messages)
    assert "固化参考要点（必须逐条核对）" in user
    assert "1. 比较时必须检查 C" in user
