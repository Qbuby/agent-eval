"""上传评估样例的字段归一化回归测试。"""

from agent_eval.api.routers.evaluation import _normalize_cases


def test_normalize_cases_preserves_reference_criteria():
    cases = _normalize_cases([
        {
            "name": "canonical",
            "question": "问题",
            "expected_output": "答案",
            "expected_output_criteria": [" 要点一 ", "", "要点二"],
        },
        {
            "name": "key-points-alias",
            "question": "问题二",
            "key_points": "单条要点",
        },
        {
            "name": "keywords-alias",
            "question": "问题三",
            "expected_keywords": ["关键词一", "关键词二"],
        },
    ])

    assert cases[0]["expected_output_criteria"] == ["要点一", "要点二"]
    assert cases[1]["expected_output_criteria"] == ["单条要点"]
    assert cases[2]["expected_output_criteria"] == ["关键词一", "关键词二"]
    assert cases[2]["expected_keywords"] == ["关键词一", "关键词二"]
