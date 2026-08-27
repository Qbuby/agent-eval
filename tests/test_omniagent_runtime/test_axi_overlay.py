from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


OVERLAY = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "omniagent"
    / "overlay"
    / "omniagent_overlay"
)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"test_overlay_{name}", OVERLAY / name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inject_sandbox(module, monkeypatch, session: "FakeSession") -> None:
    async def get_session(_scope):
        return session

    fake = SimpleNamespace(
        get_session_manager=lambda: SimpleNamespace(get_session=get_session),
        sandbox_scope=lambda configurable: configurable["thread_id"],
    )
    monkeypatch.setitem(sys.modules, "omniagent", SimpleNamespace(sandbox=fake))
    monkeypatch.setitem(sys.modules, "omniagent.sandbox", fake)


class FakeSession:
    def __init__(self, result: object) -> None:
        self.result = result
        self.writes: list[tuple[str, bytes]] = []
        self.calls: list[tuple[str, int, dict[str, str] | None]] = []

    async def write_file(self, path: str, content: bytes | str) -> None:
        value = content.encode("utf-8") if isinstance(content, str) else content
        self.writes.append((path, value))

    async def execute(self, command: str, timeout: int, env=None):
        self.calls.append((command, timeout, env))
        return self.result


@pytest.mark.asyncio
async def test_overlay_run_keeps_token_out_of_command_and_request(monkeypatch) -> None:
    module = _load("axi_tools.py")
    token = "unique-secret-capability-token"
    session = FakeSession(
        SimpleNamespace(
            exit_code=0,
            stdout='{"ok":true,"data":{"rows":1}}',
            stderr="",
            timed_out=False,
        )
    )

    _inject_sandbox(module, monkeypatch, session)
    monkeypatch.setenv("AGENT_EVAL_INTERNAL_URL", "http://agent-eval-internal:8000")
    runtime = SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "thread-1",
                "execution_auth": {"token": token},
            }
        }
    )

    body = await module._execute_axi(
        runtime,
        "run",
        {"tool_name": "data/query; touch /tmp/pwned", "arguments": {"x": "$(id)"}},
    )

    assert body == {"ok": True, "data": {"rows": 1}}
    assert len(session.calls) == 1
    command, timeout, env = session.calls[0]
    assert command.startswith("/opt/runtime/bin/python /tmp/agent-eval-axi-bridge.py run ")
    assert "data/query" not in command
    assert "touch" not in command
    assert token not in command
    assert timeout == 45
    assert env == {
        "AGENT_EVAL_EXECUTION_TOKEN": token,
        "AGENT_EVAL_INTERNAL_URL": "http://agent-eval-internal:8000",
    }
    request = json.loads(session.writes[-1][1])
    assert request["tool_name"] == "data/query; touch /tmp/pwned"
    assert token not in session.writes[-1][1].decode("utf-8")
    assert token not in json.dumps(body)


@pytest.mark.asyncio
async def test_overlay_search_and_describe_receive_no_execution_environment(monkeypatch) -> None:
    module = _load("axi_tools.py")
    session = FakeSession(
        SimpleNamespace(exit_code=0, stdout='{"ok":true,"data":[]}', stderr="", timed_out=False)
    )

    _inject_sandbox(module, monkeypatch, session)
    runtime = SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "thread-2",
                "execution_auth": {"token": "search-describe-token"},
            }
        }
    )

    assert (await module._execute_axi(runtime, "search", {"query": "runs"}))["ok"] is True
    assert (await module._execute_axi(runtime, "describe", {"tool_name": "data/query"}))["ok"] is True
    assert [call[2] for call in session.calls] == [None, None]


@pytest.mark.asyncio
async def test_overlay_search_fails_closed_without_token(monkeypatch) -> None:
    module = _load("axi_tools.py")
    touched = False

    def manager():
        nonlocal touched
        touched = True
        raise AssertionError("sandbox must not be accessed")

    fake = SimpleNamespace(
        get_session_manager=manager,
        sandbox_scope=lambda configurable: configurable["thread_id"],
    )
    monkeypatch.setitem(sys.modules, "omniagent", SimpleNamespace(sandbox=fake))
    monkeypatch.setitem(sys.modules, "omniagent.sandbox", fake)
    runtime = SimpleNamespace(config={"configurable": {"thread_id": "thread-search"}})

    body = await module._execute_axi(runtime, "search", {"query": "runs"})

    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert touched is False


@pytest.mark.asyncio
async def test_overlay_run_fails_closed_before_sandbox_without_token(monkeypatch) -> None:
    module = _load("axi_tools.py")
    touched = False

    def manager():
        nonlocal touched
        touched = True
        raise AssertionError("sandbox must not be accessed")

    fake = SimpleNamespace(
        get_session_manager=manager,
        sandbox_scope=lambda configurable: configurable["thread_id"],
    )
    monkeypatch.setitem(sys.modules, "omniagent", SimpleNamespace(sandbox=fake))
    monkeypatch.setitem(sys.modules, "omniagent.sandbox", fake)
    runtime = SimpleNamespace(config={"configurable": {"thread_id": "thread-3"}})
    body = await module._execute_axi(runtime, "run", {"tool_name": "data/query"})

    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert touched is False


def test_bridge_uses_fixed_argv_without_shell() -> None:
    bridge = _load("axi_bridge.py")
    argv = bridge._argv(
        "run", {"tool_name": "data/query", "arguments": {"value": "$(id)"}}
    )

    assert argv[:3] == ["axi", "run", "data/query"]
    assert argv[3] == "--json"
    assert json.loads(argv[4]) == {"value": "$(id)"}
    assert all("shell" not in item for item in argv)


def test_bridge_denies_unreviewed_tools_and_filters_search() -> None:
    bridge = _load("axi_bridge.py")
    with pytest.raises(bridge.BridgeError) as info:
        bridge._argv("run", {"tool_name": "other/dangerous", "arguments": {}})
    assert info.value.code == "CAPABILITY_DENIED"

    result = [
        {"name": "data/query"},
        {"name": "other/dangerous"},
        {"invalid": True},
    ]
    filtered = [
        item
        for item in result
        if isinstance(item, dict) and item.get("name") in bridge.ALLOWED_TOOLS
    ]
    assert filtered == [{"name": "data/query"}]


def test_bridge_normalizes_business_error_and_removes_request(tmp_path, monkeypatch, capsys) -> None:
    bridge = _load("axi_bridge.py")
    request = tmp_path / "request.json"
    request.write_text('{"tool_name":"data/query","arguments":{}}', encoding="utf-8")
    completed = SimpleNamespace(
        stdout=b'{"status":"error","error":"denied"}',
        stderr=b"",
        returncode=0,
    )
    monkeypatch.setattr(bridge.subprocess, "run", lambda *args, **kwargs: completed)
    monkeypatch.setattr(sys, "argv", ["bridge", "run", str(request)])

    assert bridge.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": {"code": "AXI_RUN_FAILED", "message": "denied"},
    }
    assert not request.exists()
