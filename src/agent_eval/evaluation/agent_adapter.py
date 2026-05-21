from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class AgentResponse:
    content: str
    latency_ms: float
    token_count: int | None = None
    raw_response: Any = None


class OpenAICompatibleAdapter:
    """Calls an OpenAI-compatible /v1/chat/completions endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "default",
        timeout: float = 120,
        extra_headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update(extra_headers)
        self._client = httpx.AsyncClient(headers=headers, timeout=timeout)

    async def invoke(self, messages: list[dict[str, Any]]) -> AgentResponse:
        url = f"{self.base_url}/chat/completions"
        payload = {"model": self.model, "messages": messages, "stream": False}

        start = time.perf_counter()
        resp = await self._client.post(url, json=payload)
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
           "configurable": {"thread_id": <id>, "language": <text>},
           "stream": True}

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
    ):
        self.url = url
        self.timeout = timeout
        self.mode = mode
        self.thread_id = thread_id
        self.language = language
        self.payload_template = payload_template or {}
        req_headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if headers:
            req_headers.update(headers)
        self._client = httpx.AsyncClient(headers=req_headers, timeout=timeout)

    def _build_payload(self, question: str) -> dict[str, Any]:
        if self.mode == "langgraph_v2":
            return {
                "question": question,
                "configurable": {
                    "thread_id": self.thread_id or f"eval_{uuid.uuid4().hex[:12]}",
                    "language": self.language,
                },
                "stream": True,
            }

        # generic mode (legacy)
        payload = {}
        for key, value in self.payload_template.items():
            if isinstance(value, str) and "{input}" in value:
                payload[key] = value.replace("{input}", question)
            elif isinstance(value, str) and "{uuid}" in value:
                payload[key] = value.replace("{uuid}", uuid.uuid4().hex[:12])
            else:
                payload[key] = value

        if "question" not in payload and "messages" not in payload:
            payload["question"] = question

        if "conversation_id" not in payload:
            payload["conversation_id"] = f"eval_{uuid.uuid4().hex[:12]}"

        return payload

    async def invoke(self, messages: list[dict[str, Any]]) -> AgentResponse:
        question = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                question = msg.get("content", "")
                break

        payload = self._build_payload(question)
        full_text: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        active_tools: dict[str, dict[str, Any]] = {}
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

        start = time.perf_counter()
        async with self._client.stream("POST", self.url, json=payload) as resp:
            resp.raise_for_status()
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
                    if self._handle_langgraph_event(
                        obj, full_text, tool_calls, active_tools, usage_acc,
                    ):
                        usage_seen = True
                else:
                    payload_data = obj.get("payload", {})
                    if payload_data.get("type") == "done":
                        break
                    response_text = payload_data.get("response", "")
                    if isinstance(response_text, str) and response_text:
                        full_text.append(response_text)

        latency_ms = (time.perf_counter() - start) * 1000
        content = "".join(full_text).strip()
        # Build raw_response carrying both tool_calls and an OpenAI-shaped
        # usage block so _extract_usage in the runner picks it up.
        raw: dict[str, Any] = {}
        if tool_calls:
            raw["tool_calls"] = tool_calls
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
    def _handle_langgraph_event(
        obj: dict[str, Any],
        full_text: list[str],
        tool_calls: list[dict[str, Any]],
        active_tools: dict[str, dict[str, Any]],
        usage_acc: dict[str, int] | None = None,
    ) -> bool:
        """Pull text, tool calls, and (when ``usage_acc`` is provided) token
        usage out of LangChain's ``astream_events v2`` shape.

        Returns True iff this event contributed token counts — the caller
        flips a "have any usage" flag, so we don't write a fake zero usage
        block when the agent simply doesn't report tokens.
        """
        event = obj.get("event", "")
        data = obj.get("data") or {}

        if event == "on_chat_model_stream":
            chunk = data.get("chunk")
            if isinstance(chunk, dict):
                kwargs = chunk.get("kwargs") or {}
                content = kwargs.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text = item.get("text", "")
                            if text:
                                full_text.append(text)
                elif isinstance(content, str) and content:
                    full_text.append(content)
            return False

        if event == "on_tool_start":
            run_id = obj.get("run_id") or ""
            name = obj.get("name") or data.get("name") or ""
            input_arg = data.get("input")
            active_tools[run_id] = {"name": name, "args": input_arg}
            return False

        if event == "on_tool_end":
            run_id = obj.get("run_id") or ""
            name = obj.get("name") or data.get("name") or ""
            entry = active_tools.pop(run_id, None) or {"name": name, "args": None}
            output = data.get("output")
            tool_calls.append({
                "tool_name": entry.get("name") or name,
                "args": entry.get("args"),
                "output": output if isinstance(output, (str, dict, list)) else str(output)[:500],
            })
            return False

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
        await self._client.aclose()
