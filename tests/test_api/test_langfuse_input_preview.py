"""langfuse_metrics._input_preview 的提取口径。

回归背景：列表 INPUT 列曾直接序列化整个 trace.input，结果每行都是
``{"messages":[{"role":"user","content":"…`` 这种壳，真正的用户问题被 300
字截断挤掉。现在预览复用导入备选数据集的 _question_from_trace_input。
"""

from agent_eval.api.routers.langfuse_metrics import _input_preview


class TestLangGraphShape:
    """LangGraph 实际入库形状：messages + tool_mode + output_schema。"""

    def test_extracts_user_content_not_json_envelope(self):
        trace_input = {
            "messages": [{"role": "user", "content": "开钥匙和急停后, 踩下会有问题吗"}],
            "tool_mode": "auto",
            "output_schema": {},
        }
        assert _input_preview(trace_input) == "开钥匙和急停后, 踩下会有问题吗"

    def test_takes_last_user_message_in_multiturn(self):
        trace_input = {
            "messages": [
                {"role": "user", "content": "第一轮问题"},
                {"role": "assistant", "content": "第一轮回答"},
                {"role": "user", "content": "第二轮问题"},
            ]
        }
        assert _input_preview(trace_input) == "第二轮问题"

    def test_ignores_trailing_assistant_message(self):
        """末条是 assistant 时仍要回到最后一条 user，不能预览成模型回答。"""
        trace_input = {
            "messages": [
                {"role": "user", "content": "用户问题"},
                {"role": "assistant", "content": "模型回答"},
            ]
        }
        assert _input_preview(trace_input) == "用户问题"

    def test_extracts_from_content_blocks(self):
        trace_input = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "块结构里的问题"}],
                }
            ]
        }
        assert _input_preview(trace_input) == "块结构里的问题"


class TestResumeShape:
    """LangGraph 中断恢复（interrupt/resume）的 trace：无 messages，正文在 resume。"""

    def test_extracts_resume_text(self):
        trace_input = {
            "goto": [],
            "graph": None,
            "resume": "- 请问您的 RT16 是哪个具体型号？ -> RT16B",
            "update": None,
        }
        assert _input_preview(trace_input) == "- 请问您的 RT16 是哪个具体型号？ -> RT16B"

    def test_messages_win_over_resume(self):
        """两者都在时以 messages 为准，resume 只是回退。"""
        trace_input = {
            "messages": [{"role": "user", "content": "正经问题"}],
            "resume": "恢复补充",
        }
        assert _input_preview(trace_input) == "正经问题"

    def test_null_resume_falls_through_to_json(self):
        """resume 为 None 时不能当正文，退回 JSON 保留信息。"""
        preview = _input_preview({"goto": [], "resume": None, "update": None})
        assert preview is not None
        assert "resume" in preview


class TestFallbacks:
    """提取不到正文时不能丢信息，退回原文 / JSON。"""

    def test_plain_string_passthrough(self):
        assert _input_preview("裸字符串输入") == "裸字符串输入"

    def test_falls_back_to_json_when_no_text(self):
        """没有可识别正文字段时保留 JSON，避免预览变空。"""
        preview = _input_preview({"unknown_field": {"nested": 1}})
        assert preview is not None
        assert "unknown_field" in preview

    def test_none_returns_none(self):
        assert _input_preview(None) is None

    def test_blank_string_returns_none(self):
        assert _input_preview("   ") is None

    def test_json_fallback_keeps_cjk_readable(self):
        """回退 JSON 不能转成 \\uXXXX 转义。"""
        preview = _input_preview({"unknown_field": "中文值"})
        assert "中文值" in preview


class TestTruncation:
    def test_long_question_truncated_with_ellipsis(self):
        trace_input = {"messages": [{"role": "user", "content": "问" * 400}]}
        preview = _input_preview(trace_input)
        assert len(preview) == 301
        assert preview.endswith("…")

    def test_short_question_not_truncated(self):
        trace_input = {"messages": [{"role": "user", "content": "短问题"}]}
        assert _input_preview(trace_input) == "短问题"

    def test_respects_custom_max_len(self):
        trace_input = {"messages": [{"role": "user", "content": "abcdefghij"}]}
        assert _input_preview(trace_input, max_len=4) == "abcd…"
