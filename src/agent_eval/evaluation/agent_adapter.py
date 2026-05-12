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
    """Calls an SSE streaming API (like the forklift agent in run_test.py)."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        payload_template: dict[str, Any] | None = None,
        timeout: float = 120,
    ):
        self.url = url
        self.timeout = timeout
        self.payload_template = payload_template or {}
        req_headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if headers:
            req_headers.update(headers)
        self._client = httpx.AsyncClient(headers=req_headers, timeout=timeout)

    def _build_payload(self, question: str) -> dict[str, Any]:
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
                    payload_data = obj.get("payload", {})
                    if payload_data.get("type") == "done":
                        break
                    response_text = payload_data.get("response", "")
                    if isinstance(response_text, str) and response_text:
                        full_text.append(response_text)
                except json.JSONDecodeError:
                    if data.strip():
                        full_text.append(data)

        latency_ms = (time.perf_counter() - start) * 1000
        content = "".join(full_text).strip()

        return AgentResponse(content=content, latency_ms=latency_ms)

    async def close(self):
        await self._client.aclose()
