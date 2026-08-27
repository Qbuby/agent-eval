from __future__ import annotations

from typing import Any

from axi import tool

from agent_eval_axi_tools.client import AgentEvalDataClient, AgentEvalToolError, error_envelope


def _call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        client = AgentEvalDataClient()
        result = getattr(client, method)(payload)
        return {"ok": True, "data": result}
    except AgentEvalToolError as exc:
        return error_envelope(exc)


@tool(name="search", description="Search reviewed logical Agent Eval data entities.")
def search(
    query: str,
    source: str | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    return _call(
        "search",
        {"query": query, "source": source, "tags": tags or [], "limit": max(1, min(limit, 20))},
    )


@tool(name="describe", description="Describe safe fields and relationships for logical entities.")
def describe(
    entities: list[str], include_relationships: bool = True
) -> dict[str, Any]:
    return _call(
        "describe",
        {"entities": entities[:5], "include_relationships": include_relationships},
    )


@tool(name="query", description="Execute a governed read-only query AST against Agent Eval.")
def query(request: dict[str, Any]) -> dict[str, Any]:
    return _call("query", request)
