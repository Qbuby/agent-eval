"""Three reviewed OmniAgent meta-tools that execute Axi inside a sandbox."""

import json
import os
import uuid
from pathlib import Path
from typing import Any

from langchain.tools import ToolRuntime, tool
from langchain_core.tools.structured import StructuredTool
from pydantic import BaseModel, Field

_BRIDGE = Path(__file__).with_name("axi_bridge.py").read_bytes()
_REMOTE_BRIDGE = "/tmp/agent-eval-axi-bridge.py"
_MAX_RESULT_BYTES = 96 * 1024


class AxiSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)


class AxiDescribeInput(BaseModel):
    tool_name: str = Field(min_length=1, max_length=200)


class AxiRunInput(BaseModel):
    tool_name: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


async def _execute_axi(
    runtime: ToolRuntime,
    operation: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    configurable = runtime.config.get("configurable") or {}
    execution_auth = configurable.get("execution_auth")
    token = execution_auth.get("token") if isinstance(execution_auth, dict) else None
    if not isinstance(token, str) or not token:
        return _error("UNAUTHENTICATED", "execution authorization is unavailable")
    internal_url = os.environ.get("AGENT_EVAL_INTERNAL_URL", "").rstrip("/")
    if operation == "run" and not internal_url.startswith(("http://", "https://")):
        return _error("CONFIG_ERROR", "Agent Eval internal service is unavailable")

    nonce = uuid.uuid4().hex
    request_path = f"/tmp/agent-eval-axi-request-{nonce}.json"
    command = (
        f"/opt/runtime/bin/python {_REMOTE_BRIDGE} {operation} {request_path}"
    )
    try:
        from omniagent.sandbox import get_session_manager, sandbox_scope

        session = await get_session_manager().get_session(sandbox_scope(configurable))
        await session.write_file(_REMOTE_BRIDGE, _BRIDGE)
        await session.write_file(
            request_path,
            json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )
        env = None
        if operation == "run":
            env = {
                "AGENT_EVAL_EXECUTION_TOKEN": token,
                "AGENT_EVAL_INTERNAL_URL": internal_url,
            }
        result = await session.execute(command, timeout=45, env=env)
        if result.timed_out:
            return _error("AXI_TIMEOUT", "Axi command timed out")
        if result.exit_code != 0:
            return _error("AXI_INFRASTRUCTURE_ERROR", "Axi command failed")
        raw = result.stdout.encode("utf-8")
        if len(raw) > _MAX_RESULT_BYTES:
            return _error("OUTPUT_LIMIT", "Axi result exceeds the limit")
        body = json.loads(result.stdout)
        if not isinstance(body, dict) or not isinstance(body.get("ok"), bool):
            return _error("INVALID_RESPONSE", "Axi returned an invalid envelope")
        return body
    except Exception:
        return _error("AXI_INFRASTRUCTURE_ERROR", "Axi sandbox is unavailable")


@tool(args_schema=AxiSearchInput)
async def axi_search(runtime: ToolRuntime, query: str, top_k: int = 5) -> dict[str, Any]:
    """Discover reviewed Axi capabilities by semantic search."""
    return await _execute_axi(runtime, "search", {"query": query, "top_k": top_k})


@tool(args_schema=AxiDescribeInput)
async def axi_describe(runtime: ToolRuntime, tool_name: str) -> dict[str, Any]:
    """Inspect the schema of one reviewed Axi capability before using it."""
    return await _execute_axi(runtime, "describe", {"tool_name": tool_name})


@tool(args_schema=AxiRunInput)
async def axi_run(
    runtime: ToolRuntime,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Run one reviewed Axi capability with turn-bound authorization."""
    return await _execute_axi(
        runtime,
        "run",
        {"tool_name": tool_name, "arguments": arguments},
    )


async def provide_tools(names: list[str] | None = None) -> list[StructuredTool]:
    tools = [axi_search, axi_describe, axi_run]
    if names is not None:
        tools = [item for item in tools if item.name in names]
    return tools
