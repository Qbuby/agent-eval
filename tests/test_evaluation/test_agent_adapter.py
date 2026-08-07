"""Unit tests for the SSE LangGraph v2 adapter event parsing.

The static ``_handle_langgraph_event`` method is the hot path that turns
production SSE events (shape matches ``agent_chat_sse_4-*.py``) into our
``(full_text, tool_calls)`` accumulators. These fixtures pin the shape so
regressions surface without a live agent.
"""
from __future__ import annotations

import json

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
    # 真实 LangGraph 服务端 body schema 是 extra="forbid"，只接受 question +
    # configurable。多传 stream / user_id 会被判 extra_forbidden 返回 422。
    assert set(payload) == {"question", "configurable"}
    assert payload["question"] == "hello?"
    assert set(payload["configurable"]) == {"thread_id", "language"}
    assert payload["configurable"]["language"] == "en"
    # 传入的 thread_id 只是**可读前缀**：非 sticky 下每次调用现场派生一个新会话
    # 号，故这里是 startswith 而非等值。等值断言会把「会话号一存到底」这个已修
    # 掉的缺陷重新锁回来。
    assert payload["configurable"]["thread_id"].startswith("TC-1-")


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


def test_build_payload_langgraph_merges_template_without_overriding_runtime_fields():
    ad = SSEStreamAdapter(
        url="http://x",
        mode="langgraph_v2",
        thread_id="runtime-thread",
        language="zh",
        payload_template={
            "user_id": "user-{input}",
            "question": "must-not-win",
            "stream": True,
            "configurable": {
                "thread_id": "must-not-win",
                "language": "template-language",
                "tenant": "tenant-{uuid}",
            },
        },
    )

    payload = ad._build_payload("hello")

    # 模板可以补充顶层字段（给需要 stream/user_id 的服务端用），
    # 但改不动本次问题和会话 thread_id。
    assert payload["user_id"] == "user-hello"
    assert payload["question"] == "hello"
    assert payload["stream"] is True
    # 模板给的 thread_id 一律落败：会话号只由 adapter 现场派生，前缀取自
    # 构造参数（"runtime-thread"），模板的 "must-not-win" 不得出现。
    sent = payload["configurable"]["thread_id"]
    assert sent.startswith("runtime-thread-")
    assert "must-not-win" not in sent
    assert payload["configurable"]["language"] == "template-language"
    assert payload["configurable"]["tenant"].startswith("tenant-")


def test_build_payload_langgraph_omits_stream_and_user_id_by_default():
    """默认 body 必须最小化：X-User-Id 走 header，不复制进 body。"""
    ad = SSEStreamAdapter(
        url="http://x",
        mode="langgraph_v2",
        thread_id="TC-1",
        headers={"x-user-id": "agent-eval", "Authorization": "secret"},
    )

    payload = ad._build_payload("hello")

    assert "user_id" not in payload
    assert "stream" not in payload
    assert "Authorization" not in payload
    assert payload["question"] == "hello"


# ── 会话隔离回归 ────────────────────────────────────────────────────────────
# 被测 agent 按 configurable.thread_id 划会话：同一 thread_id 就是同一场对话，
# 带着上一次调用的上下文继续。历史缺陷是 thread_id 作为实例属性一存到底，而
# _invoke_with_retry 在**同一 adapter 实例**上重试，于是重试落回同一会话、agent
# 带着上次失败尝试的残留上下文重答，且全程无日志无标记。下面几条把「每次调用
# 一个新会话」和「多轮回放例外」两侧都钉死。


def test_langgraph_thread_id_differs_per_invocation():
    """默认（单轮）：同一 adapter 连续取号必须各不相同。

    这正是重试污染的修复点——_invoke_with_retry 复用同一 adapter，若取号相同，
    第二次尝试就落回第一次已写脏的会话。
    """
    ad = SSEStreamAdapter(url="http://x", mode="langgraph_v2", thread_id="TC-1")

    first = ad._build_payload("q")["configurable"]["thread_id"]
    second = ad._build_payload("q")["configurable"]["thread_id"]

    assert first != second
    # 传入的 thread_id 降级为可读前缀，仍保留可溯源性。
    assert first.startswith("TC-1-")
    assert second.startswith("TC-1-")


def test_langgraph_thread_id_unique_without_configured_prefix():
    """没给 thread_id 时也必须逐次唯一，不能退化成固定常量。"""
    ad = SSEStreamAdapter(url="http://x", mode="langgraph_v2")

    ids = {ad._build_payload("q")["configurable"]["thread_id"] for _ in range(5)}

    assert len(ids) == 5


def test_sticky_thread_reuses_one_session_for_multiturn():
    """多轮回放（sticky_thread=True）必须整段共用一个会话号。

    multiturn.replay_conversation 每轮只发当轮 user 消息，靠 agent 端按 thread_id
    维持上下文；这里若逐轮换号，多轮评估就失去意义。
    """
    ad = SSEStreamAdapter(
        url="http://x", mode="langgraph_v2", thread_id="conv-1", sticky_thread=True,
    )

    ids = {ad._build_payload("q")["configurable"]["thread_id"] for _ in range(3)}

    assert ids == {"conv-1"}


def test_generic_conversation_id_differs_per_invocation():
    """generic 模式的会话键是 conversation_id，同样逐次唯一。"""
    ad = SSEStreamAdapter(
        url="http://x", mode="generic",
        payload_template={"question": "{input}"},
        thread_id="G-1",
    )

    first = ad._build_payload("q")["conversation_id"]
    second = ad._build_payload("q")["conversation_id"]

    assert first != second


def test_invoke_records_sent_thread_id_and_identity():
    """raw_response 必须交回**实际发出**的会话号与身份。

    落库记的是这个值而不是 runner 生成的前缀——否则事后无法判定某条样例究竟落在
    哪个会话里，评估结果就无法自证会话是否干净。
    """
    async def _run():
        seen: list[dict] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append({
                "body": json.loads(request.content.decode()),
                "user_id": request.headers.get("x-user-id"),
            })
            return httpx.Response(
                200,
                text='data: {"event": "on_chat_model_stream", "data": {"chunk": {"kwargs": {"content": "ok"}}}}\n\ndata: [DONE]\n\n',
                headers={"Content-Type": "text/event-stream"},
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        ad = SSEStreamAdapter(
            url="http://x", mode="langgraph_v2", thread_id="TC-1", client=client,
        )
        try:
            first = await ad.invoke([{"role": "user", "content": "q"}])
            second = await ad.invoke([{"role": "user", "content": "q"}])
        finally:
            await client.aclose()
        return seen, first, second

    seen, first, second = asyncio.run(_run())

    # 记下的值 == 真正发出的值（不是事后猜测）。
    assert first.raw_response["sent_thread_id"] == seen[0]["body"]["configurable"]["thread_id"]
    assert second.raw_response["sent_thread_id"] == seen[1]["body"]["configurable"]["thread_id"]
    # 两次调用落在不同会话里。
    assert first.raw_response["sent_thread_id"] != second.raw_response["sent_thread_id"]
    # 逐次唯一的身份注入到 header（不进 body），且两次各不相同，便于服务端溯源。
    assert seen[0]["user_id"] and seen[0]["user_id"] != seen[1]["user_id"]
    assert first.raw_response["identity"] == seen[0]["user_id"]
    assert "user_id" not in seen[0]["body"]


def test_configured_identity_is_not_overwritten():
    """调用方显式配了 X-User-Id 就尊重它，不做逐次覆盖。"""
    async def _run():
        seen: list[str | None] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("x-user-id"))
            return httpx.Response(
                200,
                text='data: [DONE]\n\n',
                headers={"Content-Type": "text/event-stream"},
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        ad = SSEStreamAdapter(
            url="http://x", mode="langgraph_v2",
            headers={"X-User-Id": "caller-owned"}, client=client,
        )
        try:
            resp = await ad.invoke([{"role": "user", "content": "q"}])
        finally:
            await client.aclose()
        return seen, resp

    seen, resp = asyncio.run(_run())

    assert seen == ["caller-owned"]
    assert resp.raw_response["identity"] == "caller-owned"


def test_identity_injection_does_not_leak_across_invocations():
    """逐次身份只加在本次请求的头副本上，不得写回 self._headers。

    高并发下多个 case 共享一个 pooled client，若把身份写进实例 headers，同一
    adapter 的后续请求就会串味、无法逐次溯源。
    """
    ad = SSEStreamAdapter(url="http://x", mode="langgraph_v2")

    assert not any(k.lower() == "x-user-id" for k in ad._headers)


def test_stream_http_error_preserves_response_detail():
    async def _run():
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                422,
                json={"detail": [{"loc": ["body", "user_id"], "msg": "Field required"}]},
                request=request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            ad = SSEStreamAdapter(
                url="http://agent.test/api/agent/langgraph",
                mode="langgraph_v2",
                client=client,
            )
            try:
                await ad.invoke([{"role": "user", "content": "hello"}])
                raise AssertionError("expected HTTPStatusError")
            except httpx.HTTPStatusError as exc:
                assert exc.response.status_code == 422
                assert "user_id" in str(exc)
                assert "Field required" in str(exc)
                assert getattr(exc, "response_body", "")
        finally:
            await client.aclose()

    asyncio.run(_run())


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
