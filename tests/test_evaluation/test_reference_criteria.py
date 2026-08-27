"""固化答案关键点注入 judge 的回归测试。"""
from agent_eval.evaluation.configurable_judge import (
    _build_comparative_messages,
    _build_messages,
)


def _user_content(messages: list[dict[str, str]]) -> str:
    return next(message["content"] for message in messages if message["role"] == "user")


def test_default_template_renders_frozen_reference_criteria_inline():
    """默认模板/默认映射下，冻结要点内联进「评判要点」段，不走兜底追加。"""
    messages = _build_messages(
        params={},
        input_text="问题",
        output_text="回答",
        expected_output="期望答案",
        metadata={"reference_criteria": ["必须提到 A", "不得声称 B"]},
    )

    user = _user_content(messages)
    assert "## 评判要点（如有，逐条核对回答是否满足）" in user
    assert "1. 必须提到 A" in user
    assert "2. 不得声称 B" in user
    # 模板已消费 → 不再重复追加兜底段，要点各只出现一次。
    assert "固化参考要点（必须逐条核对）" not in user
    assert user.count("必须提到 A") == 1
    assert user.count("不得声称 B") == 1


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


def test_criteria_source_renders_single_turn_reference_criteria():
    """默认映射的 {{Criteria}} 走 reference_criteria，单轮样例级要点可直接渲染。"""
    messages = _build_messages(
        params={},
        input_text="问题",
        output_text="回答",
        expected_output="期望答案",
        metadata={"reference_criteria": ["必须提到 A", "不得声称 B"]},
    )

    user = _user_content(messages)
    # 默认模板已含 {{Criteria}} 段，要点由变量渲染而非兜底追加。
    assert "## 评判要点" in user
    assert user.count("必须提到 A") == 1
    assert "固化参考要点（必须逐条核对）" not in user


def test_criteria_source_prefers_turn_criteria_over_case_level():
    """多轮逐轮打分时该轮要点优先于样例级要点。"""
    messages = _build_messages(
        params={
            "evaluation_prompt": "要点：{{Criteria}}",
            "variable_mapping": {"Criteria": "reference_criteria"},
        },
        input_text="问题",
        output_text="回答",
        expected_output="",
        metadata={
            "turn_criteria": "本轮要点",
            "reference_criteria": ["样例级要点"],
        },
    )

    user = _user_content(messages)
    assert "本轮要点" in user
    assert "样例级要点" not in user


def test_criteria_source_renders_empty_when_case_has_no_key_points():
    """样例没填关键点时渲染空串，不报错也不追加兜底段。"""
    messages = _build_messages(
        params={},
        input_text="问题",
        output_text="回答",
        expected_output="期望答案",
        metadata={"reference_criteria": []},
    )

    user = _user_content(messages)
    assert "## 评判要点" in user
    assert "固化参考要点（必须逐条核对）" not in user


def test_criteria_source_marks_consumed_and_skips_fallback():
    """映射到 reference_criteria 即视为已消费，兜底段不再重复追加。"""
    messages = _build_messages(
        params={
            "evaluation_prompt": "要点：{{Criteria}}",
            "variable_mapping": {"Criteria": "reference_criteria"},
        },
        input_text="问题",
        output_text="回答",
        expected_output="期望答案",
        metadata={"reference_criteria": ["唯一关键点"]},
    )

    user = _user_content(messages)
    assert user.count("唯一关键点") == 1
    assert "固化参考要点（必须逐条核对）" not in user


def test_custom_template_without_criteria_still_gets_fallback():
    """自定义模板没声明要点变量时，兜底追加仍必须生效（锁死既有保护）。"""
    messages = _build_messages(
        params={
            "evaluation_prompt": "只看回答：{{Generation}}",
            "variable_mapping": {"Generation": "output"},
        },
        input_text="问题",
        output_text="回答",
        expected_output="期望答案",
        metadata={"reference_criteria": ["漏声明也要核对"]},
    )

    user = _user_content(messages)
    assert "固化参考要点（必须逐条核对）" in user
    assert "1. 漏声明也要核对" in user
