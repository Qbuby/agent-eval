from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from agent_eval.data.content_blocks import content_to_text


@dataclass
class AgentResponse:
    content: str
    latency_ms: float
    token_count: int | None = None
    raw_response: Any = None


class AgentHTTPStatusError(httpx.HTTPStatusError):
    """HTTP 状态错误，同时保留流式响应正文供上层展示。"""

    def __init__(
        self,
        message: str,
        *,
        request: httpx.Request,
        response: httpx.Response,
        response_body: str,
    ) -> None:
        super().__init__(message, request=request, response=response)
        self.response_body = response_body

    def __str__(self) -> str:
        base = super().__str__()
        if not self.response_body:
            return base
        return f"{base}\nResponse body: {self.response_body}"


def _render_payload_value(value: Any, question: str | list[dict[str, Any]]) -> Any:
    """递归展开 payload 模板中的运行时占位符。

    带图样例的 question 是多模态 content 数组，无法嵌进模板字符串；此时
    ``{input}`` 用数组里的文本块拼接后的纯文本替换（模板通常只用它填提示语），
    真正的多模态数组由 ``_build_payload`` 直接写进 ``question`` 字段。
    """
    if isinstance(value, str):
        text = content_to_text(question)
        return value.replace("{input}", text).replace(
            "{uuid}", uuid.uuid4().hex[:12]
        )
    if isinstance(value, dict):
        return {key: _render_payload_value(item, question) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_payload_value(item, question) for item in value]
    return value


class OpenAICompatibleAdapter:
    """Calls an OpenAI-compatible /v1/chat/completions endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "default",
        timeout: float = 120,
        extra_headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update(extra_headers)
        # Per-request headers. When a shared client is injected (high-concurrency
        # runs reuse one pooled client), headers go on each request rather than
        # the client, since one client serves many adapters.
        self._headers = headers
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(headers=headers, timeout=timeout)
            self._owns_client = True

    async def invoke(self, messages: list[dict[str, Any]]) -> AgentResponse:
        url = f"{self.base_url}/chat/completions"
        payload = {"model": self.model, "messages": messages, "stream": False}

        start = time.perf_counter()
        resp = await self._client.post(url, json=payload, headers=self._headers)
        latency_ms = (time.perf_counter() - start) * 1000
        resp.raise_for_status()

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        token_count = usage.get("total_tokens")

        return AgentResponse(
            content=content, latency_ms=latency_ms,
            token_count=token_count, raw_response=data,
        )

    async def close(self):
        if self._owns_client:
            await self._client.aclose()


class SSEStreamAdapter:
    """Calls an SSE streaming agent.

    Two payload+event modes:

    - ``mode="generic"`` (default, legacy): payload is built from
      ``payload_template`` with ``{input}`` / ``{uuid}`` substitution; events
      are JSON dicts with a ``payload.response`` text field and a
      ``payload.type=="done"`` terminator. This is what the old eval flow used.

    - ``mode="langgraph_v2"``: matches the production LangGraph agent that the
      ``D:/files/EPtestcases/agent_chat_sse_*.py`` scripts target. Payload
      shape:

          {"question": <text>,
           "configurable": {"thread_id": <id>, "language": <text>}}

      服务端请求体是 ``extra="forbid"`` 的严格 schema，只接受这两个字段，
      流式由 ``Accept: text/event-stream`` 协商、用户身份由 ``X-User-Id``
      header 传递，因此运行时不再注入 ``stream`` 等协议外字段；个别服务确需
      额外字段时通过 ``payload_template`` 显式声明。

      and events follow LangChain's ``astream_events v2`` format, so we read
      ``data.chunk.kwargs.content`` text items from ``on_chat_model_stream``
      events. ``on_tool_start``/``on_tool_end`` events are collected into
      ``tool_calls`` so downstream evaluators can compare against expected
      tool sequences.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        payload_template: dict[str, Any] | None = None,
        timeout: float = 120,
        mode: str = "generic",
        thread_id: str | None = None,
        language: str = "请用中文回复",
        client: httpx.AsyncClient | None = None,
        sticky_thread: bool = False,
    ):
        self.url = url
        self.payload_template = payload_template or {}
        self.timeout = timeout
        self.mode = mode
        self.thread_id = thread_id
        self.language = language
        # 会话复用策略。被测 agent 按 configurable.thread_id 划会话——同一
        # thread_id 就是同一场对话，带着上一次调用的上下文继续。
        #   sticky_thread=False（默认）：每次 invoke 派生一个**新**会话号，
        #     单轮样例之间、以及同一样例的每次重试，都落在互不相干的干净会话里。
        #   sticky_thread=True：整个 adapter 生命周期共用一个会话号，仅供
        #     multiturn.replay_conversation 逐轮回放使用（agent 端要靠它维持
        #     上下文，每轮只发当轮 user 消息）。
        # 历史缺陷：thread_id 曾作为实例属性一存到底，非多轮路径下每次重试都
        # 复用同一会话号，agent 带着上次未完成尝试的残留上下文重答，且全程无
        # 日志无标记（见 _invoke_with_retry 在同一 adapter 上循环）。
        self.sticky_thread = bool(sticky_thread)
        self._invocations = 0
        req_headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if headers:
            req_headers.update(headers)
        self._headers = req_headers
        # 调用方显式配了身份就尊重它；否则每次请求注入一个唯一的 X-User-Id，
        # 使被测服务端日志能反查到底是哪一次评测调用。header 必须 latin-1 可编码
        # （h11 硬要求），故身份用纯 ASCII 的 uuid，不复用可能含中文的 thread_id。
        self._inject_identity = not any(
            k.lower() == "x-user-id" for k in req_headers
        )
        # Reuse an injected pooled client on high-concurrency runs; otherwise
        # own a private client (CLI / tests). Headers are applied per-request
        # so a shared client can serve many adapters with different auth.
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(headers=req_headers, timeout=timeout)
            self._owns_client = True

    def _next_thread_id(self) -> str:
        """本次调用要用的会话号。

        ``sticky_thread=True``（多轮回放）时整段对话共用 ``self.thread_id``；
        否则**每次调用现场派生一个新号**，把传入的 thread_id 仅当作可读前缀，
        再缀上单调计数与随机段。这样同一 adapter 的第 N 次调用（含每一次重试）
        都落在互不相干的干净会话里，且从会话号本身就能看出这是第几次调用。
        """
        base = self.thread_id
        if self.sticky_thread and base:
            return base
        self._invocations += 1
        suffix = f"i{self._invocations}-{uuid.uuid4().hex[:8]}"
        return f"{base}-{suffix}" if base else f"eval_{suffix}"

    def _build_payload(
        self, question: str | list[dict[str, Any]], thread_id: str | None = None,
    ) -> dict[str, Any]:
        """构造请求体。``question`` 可以是纯文本，也可以是多模态 content 数组
        （Anthropic canonical blocks：``[{"type":"text",...},{"type":"image",...}]``）。

        数组形态原样进 ``question`` 字段——被测 agent 的 ``ChatAgentRequest.question``
        声明为 ``str | list[dict]``，其 content 预处理会把 image/document/video 的
        URL 块下载落沙箱、按 provider 归一化格式，评测侧不需要做任何转换。
        ``payload_template`` 的 ``{input}`` 占位符只在纯文本时代换（数组无法做字符串
        替换），故渲染时用文本摘要，避免把 JSON 塞进模板槽位。

        ``thread_id`` 由 ``invoke`` 经 ``_next_thread_id`` 算好后传入，以便调用方
        拿到**实际发出**的会话号写进 raw_response；单独调用本方法（测试）时省略，
        此时按同一策略现场派生。
        """
        rendered = _render_payload_value(self.payload_template, question)
        if not isinstance(rendered, dict):
            rendered = {}

        if self.mode == "langgraph_v2":
            # LangGraph 服务端的请求体是 extra="forbid" 的严格 schema，只接受
            # question 与 configurable。任何多余顶层字段都会被判 422
            # （extra_forbidden），历史上运行时硬注入的 stream=True 正是如此。
            # 因此这里不再自行添加协议外字段：流式由 Accept: text/event-stream
            # 协商，用户身份由 X-User-Id header 传递；确有服务需要 stream 等
            # 额外字段时，通过 payload_template 显式声明。
            payload = {
                key: value
                for key, value in rendered.items()
                if key not in {"question", "configurable"}
            }
            configurable: dict[str, Any] = {
                "thread_id": thread_id or self._next_thread_id(),
                "language": self.language,
            }
            template_configurable = rendered.get("configurable")
            if isinstance(template_configurable, dict):
                configurable.update({
                    key: value
                    for key, value in template_configurable.items()
                    if key != "thread_id"
                })

            payload.update({
                "question": question,
                "configurable": configurable,
            })
            return payload

        # generic mode (legacy)
        payload = rendered
        if "question" not in payload and "messages" not in payload:
            payload["question"] = question

        if "conversation_id" not in payload:
            # generic 模式的会话键是 conversation_id，同样每次调用一个新值；
            # 用 invoke 算好的那个，使 raw_response 记录的与发出的是同一个值。
            payload["conversation_id"] = thread_id or self._next_thread_id()

        return payload

    async def invoke(self, messages: list[dict[str, Any]]) -> AgentResponse:
        # 取最后一条 user 消息的 content 原样送出：可能是 str，也可能是多模态
        # content 数组（带图样例）。不在此拍平成字符串，否则图片信息丢失。
        question: str | list[dict[str, Any]] = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                question = content if isinstance(content, (str, list)) else str(content)
                break

        # 本次调用实际发出的会话号与身份，先算出来再建 payload/header，使
        # raw_response 记录的与真正发出的必然是同一个值（而非事后猜测）。
        sent_thread_id = self._next_thread_id()
        payload = self._build_payload(question, thread_id=sent_thread_id)
        req_headers = self._headers
        identity: str | None = next(
            (v for k, v in self._headers.items() if k.lower() == "x-user-id"), None
        )
        if self._inject_identity:
            # 每次请求一个唯一身份，使被测服务端日志能定位到具体这一次调用。
            # 只在本次请求的头副本上加，不污染 self._headers（共享 client 下
            # 同一 adapter 会发多次请求，逐次身份必须各不相同）。
            identity = f"agent-eval-{uuid.uuid4().hex[:16]}"
            req_headers = {**self._headers, "X-User-Id": identity}
        full_text: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        active_tools: dict[str, dict[str, Any]] = {}
        # Ordered timeline of CoT steps for the trace detail UI. Each entry is
        # one of: {type:"thought", content, started_at, duration_ms},
        # {type:"tool_call", tool_name, args, output, started_at, duration_ms}.
        # The final thought is renamed to type="answer" after the stream ends.
        steps: list[dict[str, Any]] = []
        # Open thought buffer state — set on on_chat_model_start, appended to
        # on on_chat_model_stream, flushed into ``steps`` on on_chat_model_end.
        # ``first_token_ms`` is filled the first time a stream chunk carries
        # text after the model_start event; it survives the flush as
        # ``step.first_token_ms`` for the runner to read.
        thought_state: dict[str, Any] = {
            "open": False, "buf": [], "started_at": None, "first_token_ms": None,
        }
        # LangGraph emits one on_chat_model_end per LLM step; for multi-step
        # agents (which is the common case here) we accumulate token counts
        # across all steps so the run summary matches the agent's true cost.
        usage_acc = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        usage_seen = False
        # OmniAgent 的带内控制帧（服务端异常 / 0.x 中断与排队）以及 v1 的
        # command_result / structured_output 协议帧。它们都混在 HTTP 200 的 SSE
        # 流里，不属于 LangGraph astream_events，须在事件解析前单独截获。
        control_frames: list[dict[str, Any]] = []
        protocol_frames: list[dict[str, Any]] = []

        start = time.perf_counter()
        # 流式读取途中对端切断（judge 的大 payload 常触发被测 agent 或上游网关
        # 在发完 [DONE] 前 RST 连接）会抛 httpx.ReadError / RemoteProtocolError。
        # 不让它冒泡——保留已累积的 full_text/steps/usage，标记 truncated，交给
        # 上层散文兜底 (_salvage_prose_score) 从部分内容抽分，而不是整维 skipped。
        # 零字节即断时 full_text 为空，自然降级为空 content（上层判 skipped）。
        # 注意：ConnectError/ConnectTimeout（真连不上，无 partial 可救）与
        # HTTPStatusError（上游明确 4xx/5xx 拒绝）不在此捕获，照常冒泡。
        truncated = False
        try:
            async with self._client.stream("POST", self.url, json=payload, headers=req_headers) as resp:
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    # 流式响应默认尚未读取，直接访问 response.text 会抛
                    # ResponseNotRead。趁上下文仍打开读取并限制正文长度，使
                    # FastAPI 的 422 detail 能进入结果错误，同时避免无界放大。
                    raw_body = await resp.aread()
                    response_body = raw_body.decode(
                        resp.encoding or "utf-8", errors="replace"
                    )[:2000]
                    raise AgentHTTPStatusError(
                        str(exc),
                        request=exc.request,
                        response=exc.response,
                        response_body=response_body,
                    ) from exc
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        if self.mode == "generic" and data.strip():
                            full_text.append(data)
                        continue

                    if self.mode == "langgraph_v2":
                        # OmniAgent 把异常与中断**写在流里**（HTTP 200 早已发出，
                        # 见 omniagent/api/services/response_service.py）：
                        #   {"status": "error", "error": ...}      服务端异常
                        #   {"event": "interrupted", "interrupt": ...} 等待人工输入
                        #   {"event": "enqueued", "message": ...}  该会话有未答中断，本次排队
                        # 这三种帧都不是 astream_events 事件，_handle_langgraph_event
                        # 认不出而静默丢弃，结果是「跑失败」长得和「答了空话」一样。
                        # 必须在这里显式截获并记录，让上层把样例判成 error 而不是
                        # 假 pass / 无差别 skipped。
                        control = self._detect_control_frame(obj)
                        if control is not None:
                            control_frames.append(control)
                            if control["kind"] in ("interrupted", "enqueued"):
                                # 0.x agent 停下等输入 / 被排队，本次调用不会再产出答案。
                                # v1 HTTP 入口已取消中断，但保留兼容读取旧服务。
                                break
                            continue
                        protocol = self._detect_protocol_frame(obj)
                        if protocol is not None:
                            protocol_frames.append(protocol)
                            if protocol["kind"] == "command_result":
                                text = protocol.get("detail")
                                if isinstance(text, str) and text:
                                    full_text.append(text)
                            continue
                        if self._handle_langgraph_event(
                            obj, full_text, tool_calls, active_tools, usage_acc,
                            steps, thought_state, start,
                        ):
                            usage_seen = True
                    else:
                        payload_data = obj.get("payload", {})
                        if payload_data.get("type") == "done":
                            break
                        response_text = payload_data.get("response", "")
                        if isinstance(response_text, str) and response_text:
                            full_text.append(response_text)
        except (httpx.ReadError, httpx.RemoteProtocolError):
            truncated = True

        latency_ms = (time.perf_counter() - start) * 1000
        # Flush any unterminated thought (rare — server cut the stream early).
        if thought_state.get("open"):
            buf_text = "".join(thought_state.get("buf") or []).strip()
            if buf_text:
                steps.append({
                    "type": "thought",
                    "content": buf_text,
                    "started_at": thought_state.get("started_at"),
                    "duration_ms": None,
                    "first_token_ms": thought_state.get("first_token_ms"),
                })
        # Promote the final thought (the one that produced the user-visible
        # answer) so the UI can style it as the answer rather than chain
        # reasoning. Heuristic: last step of type "thought" whose content is
        # non-empty. If there are no tool_calls between it and the end of
        # stream, this is reliably the answer.
        for s in reversed(steps):
            if s.get("type") == "thought" and (s.get("content") or "").strip():
                s["type"] = "answer"
                break
        content = "".join(full_text).strip()
        # Build raw_response carrying tool_calls, usage, and the ordered CoT
        # step list so the runner can persist it into test_results.full_trace.
        raw: dict[str, Any] = {}
        # 本次**实际发出**的会话号与身份，无条件记录。放在 tool_calls/steps/usage
        # 的条件写之前且不带任何 if：零字节即断（truncated）时恰恰最需要知道当时
        # 用的是哪个会话，若挂在条件下就会正好在最该溯源的场景里丢掉。
        # 上层据此可判定每条样例是否落在干净会话里 —— 落库前无从事后补算。
        raw["sent_thread_id"] = sent_thread_id
        if identity:
            raw["identity"] = identity
        if truncated:
            # 流被中途切断——已累积内容可能不完整。上层据此把「拿到部分答案」
            # 与「连接彻底失败/无评分」区分开，并允许散文兜底对部分文本抽分。
            raw["truncated"] = True
        if control_frames:
            # 带内控制帧（服务端异常 / 中断 / 排队）。与 truncated 同理无条件
            # 落库：这是判定「本次调用是否算失败」的唯一依据，HTTP 层看不出来。
            # agent_error 给上层一个不必解析明细就能判 error 的布尔位；
            # interrupted / enqueued 不算 agent 挂了，但同样没有有效答案，
            # 由上层按 incomplete 处理（沙箱在服务端也被 hold 着）。
            raw["control_frames"] = control_frames
            kinds = {c["kind"] for c in control_frames}
            if "error" in kinds:
                # 明细优先，但必须保持**真值**：服务端偶尔只发 {"status": "error"}
                # 而不带 error 字段，此时 detail 是 None。若直接落 None，上层
                # `if raw.get("agent_error")` 会把挂掉的样例判成正常 —— 这一位的
                # 全部意义就是不解析明细也能判 error，故明细为空时退回 True。
                raw["agent_error"] = next(
                    (c["detail"] for c in control_frames
                     if c["kind"] == "error" and c.get("detail")),
                    True,
                )
            if kinds & {"interrupted", "enqueued"}:
                raw["incomplete"] = True
        if protocol_frames:
            raw["protocol_frames"] = protocol_frames
            structured = next(
                (frame.get("detail") for frame in reversed(protocol_frames)
                 if frame["kind"] == "structured_output"),
                None,
            )
            if structured is not None:
                raw["structured_output"] = structured
        if tool_calls:
            raw["tool_calls"] = tool_calls
        if steps:
            raw["steps"] = steps
        if usage_seen:
            usage = {
                "input_tokens": usage_acc["input_tokens"],
                "output_tokens": usage_acc["output_tokens"],
                "total_tokens": usage_acc["input_tokens"] + usage_acc["output_tokens"],
            }
            details = {}
            if usage_acc["cache_read_tokens"]:
                details["cache_read"] = usage_acc["cache_read_tokens"]
            if usage_acc["cache_creation_tokens"]:
                details["cache_creation"] = usage_acc["cache_creation_tokens"]
            if details:
                usage["input_token_details"] = details
            raw["usage"] = usage
        return AgentResponse(
            content=content,
            latency_ms=latency_ms,
            raw_response=raw or None,
        )

    @staticmethod
    def _detect_control_frame(obj: dict[str, Any]) -> dict[str, Any] | None:
        """识别 OmniAgent 写在 SSE 流里的**非事件**控制帧，非控制帧返回 None。

        OmniAgent 的 ``astream_raw_events`` 用同一条 ``text/event-stream``
        混发两类东西：LangGraph 的 ``astream_events`` 事件（带 ``event``:
        ``on_chat_model_stream`` 等），以及三种自造的控制帧。控制帧要害在于
        **HTTP 状态码早已是 200**（StreamingResponse 一开就发头），所以服务端
        异常不会走 ``raise_for_status``，只能在流里发现：

        - ``{"status": "error", "error": "..."}`` — 服务端 500 级异常
          （``_run()`` 的 except 把它塞进队列）
        - ``{"event": "interrupted", "interrupt": {...}}`` — agent 等人工输入
          （tool_approval 或 user_question）；该会话沙箱被 hold 不回收
        - ``{"event": "enqueued", "message": "..."}`` — 该 thread 有未回答的
          ask 中断，本次请求被排队，不会产出答案

        返回 ``{"kind": ..., "detail": ...}``；``kind`` 取上述三者之一。

        为什么必须显式识别：``_handle_langgraph_event`` 按 ``obj["event"]``
        分派，``status``/``interrupted``/``enqueued`` 都落不进任何分支，被静默
        丢弃 → 上层只看到 ``content == ""``，与「agent 正常答了空话」不可区分，
        判成 skipped 甚至假 pass。评估必须把它们记成 error。
        """
        if obj.get("status") == "error":
            return {"kind": "error", "detail": obj.get("error")}
        event = obj.get("event")
        if event == "interrupted":
            return {"kind": "interrupted", "detail": obj.get("interrupt")}
        if event == "enqueued":
            return {"kind": "enqueued", "detail": obj.get("message")}
        return None

    @staticmethod
    def _detect_protocol_frame(obj: dict[str, Any]) -> dict[str, Any] | None:
        """识别 OmniAgent v1 的 HTTP SSE 协议帧，普通 LangGraph 事件返回 None。

        v1 除原始 ``astream_events`` 外还会发送两种框架级结果：斜杠命令以
        ``command_result.text`` 返回，结构化输出以 ``structured_output`` 返回。
        两者都不应交给 ``_handle_langgraph_event``，否则会被静默丢弃。
        """
        event = obj.get("event")
        if event == "command_result":
            return {"kind": "command_result", "detail": obj.get("text")}
        if event == "structured_output":
            return {
                "kind": "structured_output",
                "detail": obj.get("structured_output"),
            }
        return None

    @staticmethod
    def _handle_langgraph_event(
        obj: dict[str, Any],
        full_text: list[str],
        tool_calls: list[dict[str, Any]],
        active_tools: dict[str, dict[str, Any]],
        usage_acc: dict[str, int] | None = None,
        steps: list[dict[str, Any]] | None = None,
        thought_state: dict[str, Any] | None = None,
        t0: float | None = None,
    ) -> bool:
        """Pull text, tool calls, and (when ``usage_acc`` is provided) token
        usage out of LangChain's ``astream_events v2`` shape.

        When ``steps`` and ``thought_state`` are provided, also accumulate an
        ordered CoT timeline (thought spans interleaved with tool_call spans).
        ``t0`` is the ``perf_counter`` snapshot of when the HTTP POST started;
        passing it lets us record per-step ``first_token_ms`` (the first stream
        chunk that carried text after the chat_model_start event), which the
        runner aggregates into the per-case ``first_thinking_token_ms`` and
        ``first_answer_token_ms`` so the UI can show TTFT.

        Returns True iff this event contributed token counts — the caller
        flips a "have any usage" flag, so we don't write a fake zero usage
        block when the agent simply doesn't report tokens.
        """
        event = obj.get("event", "")
        data = obj.get("data") or {}

        if event == "on_chat_model_start":
            if thought_state is not None:
                thought_state["open"] = True
                thought_state["buf"] = []
                thought_state["started_at"] = time.time()
                thought_state["first_token_ms"] = None
            return False

        if event == "on_chat_model_stream":
            chunk = data.get("chunk")
            if isinstance(chunk, dict):
                kwargs = chunk.get("kwargs") or {}
                content = kwargs.get("content")
                got_text = False
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text = item.get("text", "")
                            if text:
                                full_text.append(text)
                                got_text = True
                                if thought_state is not None and thought_state.get("open"):
                                    thought_state["buf"].append(text)
                elif isinstance(content, str) and content:
                    full_text.append(content)
                    got_text = True
                    if thought_state is not None and thought_state.get("open"):
                        thought_state["buf"].append(content)
                if got_text and thought_state is not None and thought_state.get("open"):
                    if thought_state.get("first_token_ms") is None and t0 is not None:
                        thought_state["first_token_ms"] = int(
                            (time.perf_counter() - t0) * 1000
                        )
            return False

        if event == "on_tool_start":
            run_id = obj.get("run_id") or ""
            name = obj.get("name") or data.get("name") or ""
            input_arg = data.get("input")
            active_tools[run_id] = {
                "name": name, "args": input_arg, "started_at": time.time(),
            }
            return False

        if event == "on_tool_end":
            run_id = obj.get("run_id") or ""
            name = obj.get("name") or data.get("name") or ""
            entry = active_tools.pop(run_id, None) or {"name": name, "args": None}
            output = data.get("output")
            normalized_output = (
                output if isinstance(output, (str, dict, list)) else str(output)[:500]
            )
            tool_calls.append({
                "tool_name": entry.get("name") or name,
                "args": entry.get("args"),
                "output": normalized_output,
            })
            if steps is not None:
                started = entry.get("started_at")
                duration_ms = (
                    int((time.time() - started) * 1000) if started else None
                )
                steps.append({
                    "type": "tool_call",
                    "tool_name": entry.get("name") or name,
                    "args": entry.get("args"),
                    "output": normalized_output,
                    "started_at": started,
                    "duration_ms": duration_ms,
                })
            return False

        if event == "on_chat_model_end":
            # Close the open thought span first (regardless of usage).
            if thought_state is not None and thought_state.get("open") and steps is not None:
                buf_text = "".join(thought_state.get("buf") or []).strip()
                started = thought_state.get("started_at")
                duration_ms = (
                    int((time.time() - started) * 1000) if started else None
                )
                if buf_text:
                    steps.append({
                        "type": "thought",
                        "content": buf_text,
                        "started_at": started,
                        "duration_ms": duration_ms,
                        "first_token_ms": thought_state.get("first_token_ms"),
                    })
                thought_state["open"] = False
                thought_state["buf"] = []
                thought_state["started_at"] = None
                thought_state["first_token_ms"] = None

        if event == "on_chat_model_end" and usage_acc is not None:
            # Per LangChain ChatModel convention, the LLM emits usage_metadata
            # on its final chunk under output.usage_metadata. Common shapes:
            #   {input_tokens, output_tokens, total_tokens,
            #    input_token_details: {cache_read?, cache_creation?, audio?}}
            # We accumulate across all model_end events because tool-calling
            # agents emit one per LLM step.
            output = data.get("output") or {}
            kwargs = output.get("kwargs") if isinstance(output, dict) else None
            if isinstance(kwargs, dict):
                meta = kwargs.get("usage_metadata") or {}
                if isinstance(meta, dict):
                    inp = meta.get("input_tokens")
                    outp = meta.get("output_tokens")
                    if isinstance(inp, int):
                        usage_acc["input_tokens"] += inp
                    if isinstance(outp, int):
                        usage_acc["output_tokens"] += outp
                    details = meta.get("input_token_details") or {}
                    if isinstance(details, dict):
                        cr = details.get("cache_read")
                        cc = details.get("cache_creation")
                        if isinstance(cr, int):
                            usage_acc["cache_read_tokens"] += cr
                        if isinstance(cc, int):
                            usage_acc["cache_creation_tokens"] += cc
                    if inp or outp:
                        return True
            return False

        return False

    async def close(self):
        if self._owns_client:
            await self._client.aclose()
