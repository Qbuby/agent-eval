from __future__ import annotations

import os
from typing import Any

import httpx

SEARCH_PATH = "/internal/omniagent-data/v1/search"
DESCRIBE_PATH = "/internal/omniagent-data/v1/describe"
QUERY_PATH = "/internal/omniagent-data/v1/query"
MAX_RESPONSE_BYTES = 96 * 1024


class AgentEvalToolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AgentEvalDataClient:
    """Client with three fixed routes and environment-owned authorization."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        base_url = os.environ.get("AGENT_EVAL_INTERNAL_URL", "").rstrip("/")
        token = os.environ.get("AGENT_EVAL_EXECUTION_TOKEN", "")
        if not base_url.startswith(("http://", "https://")):
            raise AgentEvalToolError("CONFIG_ERROR", "Agent Eval internal service is not configured")
        if not token:
            raise AgentEvalToolError("UNAUTHENTICATED", "execution authorization is unavailable")
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(8.0, connect=3.0),
            transport=transport,
            follow_redirects=False,
        )

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post(SEARCH_PATH, payload)

    def describe(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post(DESCRIBE_PATH, payload)

    def query(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post(QUERY_PATH, payload)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise AgentEvalToolError("QUERY_TIMEOUT", "Agent Eval request timed out") from exc
        except httpx.HTTPError as exc:
            raise AgentEvalToolError("SERVICE_UNAVAILABLE", "Agent Eval is unavailable") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise AgentEvalToolError("OUTPUT_LIMIT", "Agent Eval response exceeds the limit")
        try:
            body = response.json()
        except ValueError as exc:
            raise AgentEvalToolError("INVALID_RESPONSE", "Agent Eval returned invalid JSON") from exc
        if response.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else None
            if isinstance(detail, dict):
                code = str(detail.get("code") or "REQUEST_FAILED")
                message = str(detail.get("message") or "Agent Eval request failed")
            else:
                code, message = "REQUEST_FAILED", "Agent Eval request failed"
            raise AgentEvalToolError(code, message[:1000])
        if not isinstance(body, dict):
            raise AgentEvalToolError("INVALID_RESPONSE", "Agent Eval response must be an object")
        return body


def error_envelope(exc: AgentEvalToolError) -> dict[str, Any]:
    return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
