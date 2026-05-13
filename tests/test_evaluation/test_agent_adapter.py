"""Unit tests for the SSE LangGraph v2 adapter event parsing.

The static ``_handle_langgraph_event`` method is the hot path that turns
production SSE events (shape matches ``agent_chat_sse_4-*.py``) into our
``(full_text, tool_calls)`` accumulators. These fixtures pin the shape so
regressions surface without a live agent.
"""
from __future__ import annotations

from agent_eval.evaluation.agent_adapter import SSEStreamAdapter


def _run_events(events: list[dict]) -> tuple[list[str], list[dict], dict]:
    full_text: list[str] = []
    tool_calls: list[dict] = []
    active: dict[str, dict] = {}
    for e in events:
        SSEStreamAdapter._handle_langgraph_event(e, full_text, tool_calls, active)
    return full_text, tool_calls, active


def test_chat_model_stream_list_content():
    events = [
        {"event": "on_chat_model_stream", "data": {"chunk": {
            "kwargs": {"content": [
                {"type": "text", "text": "你好"},
                {"type": "text", "text": "，"},
                {"type": "image_url", "image_url": "..."},  # ignored
                {"type": "text", "text": "世界"},
            ]},
        }}},
    ]
    text, _, _ = _run_events(events)
    assert "".join(text) == "你好，世界"


def test_chat_model_stream_string_content():
    events = [
        {"event": "on_chat_model_stream", "data": {"chunk": {"kwargs": {"content": "hi "}}}},
        {"event": "on_chat_model_stream", "data": {"chunk": {"kwargs": {"content": "there"}}}},
    ]
    text, _, _ = _run_events(events)
    assert "".join(text) == "hi there"


def test_tool_start_end_pairing():
    events = [
        {"event": "on_tool_start", "run_id": "r1", "name": "lookup_vehicle",
         "data": {"input": {"vin": "RPL201"}}},
        {"event": "on_tool_start", "run_id": "r2", "name": "get_parts",
         "data": {"input": {"model": "X"}}},
        {"event": "on_tool_end", "run_id": "r1", "data": {"output": "ok"}},
        {"event": "on_tool_end", "run_id": "r2", "data": {"output": {"parts": []}}},
    ]
    _, calls, active = _run_events(events)
    assert [c["tool_name"] for c in calls] == ["lookup_vehicle", "get_parts"]
    assert calls[0]["args"] == {"vin": "RPL201"}
    assert calls[1]["output"] == {"parts": []}
    assert active == {}  # all paired


def test_tool_end_without_matching_start_is_resilient():
    events = [
        {"event": "on_tool_end", "run_id": "ghost", "name": "x", "data": {"output": "x"}},
    ]
    _, calls, _ = _run_events(events)
    assert calls == [{"tool_name": "x", "args": None, "output": "x"}]


def test_unknown_events_are_ignored():
    _run_events([
        {"event": "on_chain_start", "data": {}},
        {"event": "on_parser_start", "data": {}},
    ])


def test_build_payload_langgraph_shape():
    ad = SSEStreamAdapter(url="http://x", mode="langgraph_v2", thread_id="TC-1", language="en")
    payload = ad._build_payload("hello?")
    assert payload == {
        "question": "hello?",
        "configurable": {"thread_id": "TC-1", "language": "en"},
        "stream": True,
    }


def test_build_payload_generic_preserves_template():
    ad = SSEStreamAdapter(
        url="http://x", mode="generic",
        payload_template={"question": "{input}", "model": "gpt-4", "seed": 7},
    )
    payload = ad._build_payload("bonjour")
    assert payload["question"] == "bonjour"
    assert payload["model"] == "gpt-4"
    assert payload["seed"] == 7
    assert "conversation_id" in payload  # auto-added
