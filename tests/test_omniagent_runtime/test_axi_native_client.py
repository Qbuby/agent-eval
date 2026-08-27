from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

PACKAGE_SRC = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "agent-eval-axi-tools"
    / "src"
)
sys.path.insert(0, str(PACKAGE_SRC))

from agent_eval_axi_tools.client import (  # noqa: E402 - load the standalone package source
    DESCRIBE_PATH,
    QUERY_PATH,
    SEARCH_PATH,
    AgentEvalDataClient,
    AgentEvalToolError,
)


def test_axi_client_uses_only_fixed_paths(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EVAL_INTERNAL_URL", "http://agent-eval-internal:8000")
    monkeypatch.setenv("AGENT_EVAL_EXECUTION_TOKEN", "secret-capability-token")
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json={"path": request.url.path})

    client = AgentEvalDataClient(transport=httpx.MockTransport(handler))
    assert client.search({"query": "runs"})["path"] == SEARCH_PATH
    assert client.describe({"entities": ["evaluation_runs"]})["path"] == DESCRIBE_PATH
    assert client.query({"from": "evaluation_runs", "select": [{"field": "id"}]})["path"] == QUERY_PATH
    assert {item[1] for item in seen} == {SEARCH_PATH, DESCRIBE_PATH, QUERY_PATH}
    assert all(item[0] == "POST" for item in seen)
    assert all(item[2] == "Bearer secret-capability-token" for item in seen)


def test_axi_client_fails_without_command_token(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EVAL_INTERNAL_URL", "http://agent-eval-internal:8000")
    monkeypatch.delenv("AGENT_EVAL_EXECUTION_TOKEN", raising=False)
    with pytest.raises(AgentEvalToolError, match="authorization"):
        AgentEvalDataClient()


def test_axi_client_bounds_errors_without_echoing_token(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EVAL_INTERNAL_URL", "http://agent-eval-internal:8000")
    monkeypatch.setenv("AGENT_EVAL_EXECUTION_TOKEN", "do-not-echo")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": {"code": "FIELD_DENIED", "message": "no"}})

    client = AgentEvalDataClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AgentEvalToolError) as info:
        client.query({})
    assert info.value.code == "FIELD_DENIED"
    assert "do-not-echo" not in str(info.value)
