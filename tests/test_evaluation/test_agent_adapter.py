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


# ─── 带内控制帧（OmniAgent 把异常/中断写在 HTTP 200 的流里）──────────────────
# StreamingResponse 一开就发响应头，所以服务端异常和人工中断都赶不上
# raise_for_status，只能在流里发现。这三种帧不是 astream_events 事件，
# _handle_langgraph_event 按 obj["event"] 分派时落不进任何分支 → 被静默丢弃 →
# 上层只看到 content == ""，与「agent 正常答了空话」不可区分，判成 skipped 甚至
# 假 pass。下面几条把「识别」「落库标记」「读流是否继续」三侧都钉死。


def _sse_body(*frames: str) -> str:
    """拼一段 SSE 正文，末尾补 [DONE]（真实服务端正常收尾的样子）。"""
    return "".join(f"data: {f}\n\n" for f in frames) + "data: [DONE]\n\n"


def _text_frame(text: str) -> str:
    return json.dumps({
        "event": "on_chat_model_stream",
        "data": {"chunk": {"kwargs": {"content": text}}},
    })


def _invoke_sse(body: str, *, mode: str = "langgraph_v2"):
    """用 MockTransport 回放一段 SSE 正文，返回 AgentResponse。"""
    async def _run():
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text=body, headers={"Content-Type": "text/event-stream"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        kwargs: dict = {"url": "http://x", "mode": mode, "client": client}
        if mode == "generic":
            kwargs["payload_template"] = {"question": "{input}"}
        ad = SSEStreamAdapter(**kwargs)
        try:
            return await ad.invoke([{"role": "user", "content": "q"}])
        finally:
            await client.aclose()

    return asyncio.run(_run())


def test_detect_control_frame_recognizes_three_kinds():
    """三种帧的形状按服务端实际发出的键名识别（status / interrupt / message）。"""
    assert SSEStreamAdapter._detect_control_frame(
        {"status": "error", "error": "boom"},
    ) == {"kind": "error", "detail": "boom"}

    interrupt = {"type": "tool_approval", "tool": "shell"}
    assert SSEStreamAdapter._detect_control_frame(
        {"event": "interrupted", "interrupt": interrupt},
    ) == {"kind": "interrupted", "detail": interrupt}

    assert SSEStreamAdapter._detect_control_frame(
        {"event": "enqueued", "message": "thread busy"},
    ) == {"kind": "enqueued", "detail": "thread busy"}


def test_detect_control_frame_ignores_normal_events():
    """正常事件必须返回 None，否则整条流会被误判成失败。"""
    for obj in (
        {"event": "on_chat_model_stream", "data": {"chunk": {}}},
        {"event": "on_tool_start", "run_id": "r1", "name": "t"},
        {"event": "on_chat_model_end", "data": {}},
        {"status": "ok"},
        {},
    ):
        assert SSEStreamAdapter._detect_control_frame(obj) is None


def test_error_frame_marks_agent_error():
    """服务端异常帧要落成 agent_error（明细原样交回），且不算 incomplete。"""
    resp = _invoke_sse(_sse_body(
        json.dumps({"status": "error", "error": "RuntimeError: sandbox died"}),
    ))

    assert resp.raw_response["control_frames"] == [
        {"kind": "error", "detail": "RuntimeError: sandbox died"},
    ]
    assert resp.raw_response["agent_error"] == "RuntimeError: sandbox died"
    # error 是「agent 挂了」，与「停下等人工输入」是两种处置，不该混成一个标记。
    assert "incomplete" not in resp.raw_response


def test_error_frame_does_not_stop_stream():
    """error 帧后面还有内容就继续读——实现里 error 走 continue 而非 break。

    服务端可能先报一个子步骤异常再接着答，把已产出的文本丢掉会让本可散文兜底
    的样例退化成零内容。
    """
    resp = _invoke_sse(_sse_body(
        _text_frame("前半"),
        json.dumps({"status": "error", "error": "step failed"}),
        _text_frame("后半"),
    ))

    assert resp.content == "前半后半"
    assert resp.raw_response["agent_error"] == "step failed"


def test_interrupted_frame_marks_incomplete_and_stops_stream():
    """中断帧后必须立刻收流：agent 在等人工输入，继续读只会阻塞到超时。

    「不再读」这件事用 interrupted 之后那帧文本是否进 content 来判定——它若被
    累积，说明 break 没生效。
    """
    resp = _invoke_sse(_sse_body(
        _text_frame("中断前"),
        json.dumps({"event": "interrupted",
                    "interrupt": {"type": "user_question", "question": "确认吗？"}}),
        _text_frame("不该被读到"),
    ))

    assert resp.content == "中断前"
    assert resp.raw_response["incomplete"] is True
    assert resp.raw_response["control_frames"] == [{
        "kind": "interrupted",
        "detail": {"type": "user_question", "question": "确认吗？"},
    }]
    # 等人工输入不是 agent 挂了，不该冒充 error。
    assert "agent_error" not in resp.raw_response


def test_enqueued_frame_marks_incomplete_and_stops_stream():
    """排队帧同理：该 thread 有未答中断，本次请求不会产出答案。"""
    resp = _invoke_sse(_sse_body(
        json.dumps({"event": "enqueued", "message": "pending ask on this thread"}),
        _text_frame("不该被读到"),
    ))

    assert resp.content == ""
    assert resp.raw_response["incomplete"] is True
    assert resp.raw_response["control_frames"] == [
        {"kind": "enqueued", "detail": "pending ask on this thread"},
    ]
    assert "agent_error" not in resp.raw_response


def test_clean_stream_carries_no_control_markers():
    """正常流一个控制标记都不能带，否则上层会把好样例判成失败。"""
    resp = _invoke_sse(_sse_body(_text_frame("正常回答")))

    assert resp.content == "正常回答"
    for key in ("control_frames", "agent_error", "incomplete", "truncated"):
        assert key not in resp.raw_response


def test_generic_mode_does_not_scan_control_frames():
    """控制帧识别只属于 langgraph_v2：generic 端的 status 字段语义不同，
    误判会把正常流判成失败。"""
    resp = _invoke_sse(
        _sse_body(
            json.dumps({"status": "error", "error": "not ours"}),
            json.dumps({"payload": {"response": "hello"}}),
            json.dumps({"payload": {"type": "done"}}),
        ),
        mode="generic",
    )

    assert resp.content == "hello"
    assert "control_frames" not in resp.raw_response
    assert "agent_error" not in resp.raw_response


def test_error_frame_without_detail_still_flags_error():
    """服务端漏带 error 字段时，agent_error 仍须是真值。

    注释承诺 agent_error 是「不必解析明细就能判 error 的布尔位」，若明细缺失时
    落成 None，上层布尔判定会把挂掉的样例当成正常，正是这个改动要防的假 pass。
    """
    resp = _invoke_sse(_sse_body(json.dumps({"status": "error"})))

    assert resp.raw_response["control_frames"] == [{"kind": "error", "detail": None}]
    assert resp.raw_response["agent_error"], (
        "error 帧无明细时 agent_error 退化成假值，上层会漏判"
    )


def test_multiple_control_frames_are_all_recorded():
    """多帧并存时逐条留痕，且两个标记各自独立成立（error 且 incomplete）。"""
    resp = _invoke_sse(_sse_body(
        json.dumps({"status": "error", "error": "first"}),
        json.dumps({"status": "error", "error": "second"}),
        json.dumps({"event": "interrupted", "interrupt": {"type": "tool_approval"}}),
    ))

    kinds = [c["kind"] for c in resp.raw_response["control_frames"]]
    assert kinds == ["error", "error", "interrupted"]
    # 取第一条明细（最早的失败原因，后续多半是它的次生结果）。
    assert resp.raw_response["agent_error"] == "first"
    assert resp.raw_response["incomplete"] is True
