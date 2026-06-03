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


# ─── usage_metadata accumulation across multiple model_end events ─────────


def test_usage_acc_multi_step_anthropic_shape():
    """Tool-calling agents emit on_chat_model_end per step. We sum them.

    Shape mirrors LangChain Anthropic models: input_tokens / output_tokens
    + input_token_details.{cache_read, cache_creation}.
    """
    full_text: list[str] = []
    tool_calls: list[dict] = []
    active: dict[str, dict] = {}
    usage = {"input_tokens": 0, "output_tokens": 0,
             "cache_read_tokens": 0, "cache_creation_tokens": 0}
    seen = []
    for ev in [
        {"event": "on_chat_model_end", "data": {"output": {"kwargs": {
            "usage_metadata": {
                "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                "input_token_details": {"cache_read": 30, "cache_creation": 20},
            }}}}},
        {"event": "on_chat_model_end", "data": {"output": {"kwargs": {
            "usage_metadata": {
                "input_tokens": 200, "output_tokens": 80, "total_tokens": 280,
                "input_token_details": {"cache_read": 60, "cache_creation": 0},
            }}}}},
    ]:
        seen.append(SSEStreamAdapter._handle_langgraph_event(
            ev, full_text, tool_calls, active, usage,
        ))
    assert seen == [True, True]
    assert usage == {
        "input_tokens": 300, "output_tokens": 130,
        "cache_read_tokens": 90, "cache_creation_tokens": 20,
    }


def test_usage_acc_handles_missing_details():
    full_text: list[str] = []
    tool_calls: list[dict] = []
    active: dict[str, dict] = {}
    usage = {"input_tokens": 0, "output_tokens": 0,
             "cache_read_tokens": 0, "cache_creation_tokens": 0}
    res = SSEStreamAdapter._handle_langgraph_event(
        {"event": "on_chat_model_end", "data": {"output": {"kwargs": {
            "usage_metadata": {"input_tokens": 5, "output_tokens": 7},
        }}}},
        full_text, tool_calls, active, usage,
    )
    assert res is True
    assert usage["input_tokens"] == 5 and usage["output_tokens"] == 7
    assert usage["cache_read_tokens"] == 0 and usage["cache_creation_tokens"] == 0


def test_usage_acc_no_metadata_returns_false():
    """Some events have data but no usage_metadata — must not flip the flag."""
    res = SSEStreamAdapter._handle_langgraph_event(
        {"event": "on_chat_model_end", "data": {"output": {"kwargs": {}}}},
        [], [], {}, {"input_tokens": 0, "output_tokens": 0,
                     "cache_read_tokens": 0, "cache_creation_tokens": 0},
    )
    assert res is False


def test_handler_compat_without_usage_acc():
    """Old callers that don't pass usage_acc must still work."""
    full_text: list[str] = []
    SSEStreamAdapter._handle_langgraph_event(
        {"event": "on_chat_model_stream", "data": {"chunk": {"kwargs": {"content": "hi"}}}},
        full_text, [], {},
    )
    assert full_text == ["hi"]


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


# ─── shared-client ownership (high-concurrency connection pooling) ──────────
# When _execute_run injects one pooled client for the whole run, a single
# case's adapter.close() MUST NOT close it — otherwise the second case reuses
# a closed client and the whole run fails. Conversely, an adapter that built
# its own client (CLI / tests) must close it to avoid leaking connections.

import asyncio  # noqa: E402

import httpx  # noqa: E402

from agent_eval.evaluation.agent_adapter import OpenAICompatibleAdapter  # noqa: E402


def test_injected_client_not_closed_by_adapter():
    async def _run():
        shared = httpx.AsyncClient()
        try:
            for cls, kwargs in (
                (OpenAICompatibleAdapter, {"base_url": "http://x"}),
                (SSEStreamAdapter, {"url": "http://x", "mode": "langgraph_v2"}),
            ):
                ad = cls(client=shared, **kwargs)
                assert ad._client is shared
                assert ad._owns_client is False
                await ad.close()
                assert not shared.is_closed, f"{cls.__name__}.close() closed the shared client"
        finally:
            await shared.aclose()

    asyncio.run(_run())


def test_owned_client_closed_by_adapter():
    async def _run():
        ad = OpenAICompatibleAdapter(base_url="http://x")
        assert ad._owns_client is True
        inner = ad._client
        await ad.close()
        assert inner.is_closed, "owned client should be closed by adapter.close()"

    asyncio.run(_run())
