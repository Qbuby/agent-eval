from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from requests.adapters import HTTPAdapter

from agent_eval.config import settings
from agent_eval.omniagent_runtime.runner import (
    KubernetesAnalysisRunner,
    RunnerInfrastructureError,
    RunnerOutputViolation,
    RunnerTimeout,
)


class FakeSandboxClient:
    def __init__(self, *, manifest: dict | None = None) -> None:
        self.manifest = manifest or {
            "python_version": "3.12.11",
            "files": [{"path": "nested/result.txt", "size": 4}],
        }
        self.writes: list[tuple[str, str, bytes]] = []
        self.commands: list[tuple[str, str, int]] = []
        self.reads: list[tuple[str, str]] = []
        self.destroyed: list[str] = []

    def write(self, session_id: str, path: str, content: bytes | str) -> None:
        value = content.encode() if isinstance(content, str) else content
        self.writes.append((session_id, path, value))

    def run(self, session_id: str, command: str, timeout: int = 60, env=None):
        del env
        self.commands.append((session_id, command, timeout))
        if "manifest.py" in command:
            return SimpleNamespace(
                stdout=json.dumps(self.manifest), stderr="", exit_code=0
            )
        return SimpleNamespace(stdout="complete", stderr="", exit_code=0)

    def read(self, session_id: str, path: str) -> bytes:
        self.reads.append((session_id, path))
        return b"done"

    def destroy(self, session_id: str) -> None:
        self.destroyed.append(session_id)


@pytest.fixture(autouse=True)
def enable_kubernetes_runner(monkeypatch):
    monkeypatch.setattr(settings.omniagent, "runner", "kubernetes")
    monkeypatch.setattr(settings.omniagent, "kubernetes_runner_confirmed", True)


@pytest.mark.asyncio
async def test_kubernetes_runner_uses_fixed_commands_and_destroys_claim(tmp_path: Path) -> None:
    client = FakeSandboxClient()
    workspace = tmp_path / "job"
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "source.csv").write_bytes(b"a,b\n")

    result = await KubernetesAnalysisRunner(client).run(
        code="print('user supplied')",
        workspace=workspace,
        execution_id="job-id-attempt-1",
    )

    assert result.exit_code == 0
    assert result.logs == "complete"
    assert result.python_version == "3.12.11"
    assert [path.relative_to(workspace / "outputs").as_posix() for path in result.output_files] == [
        "nested/result.txt"
    ]
    assert result.output_files[0].read_bytes() == b"done"
    assert len(client.commands) == 2
    assert all("user supplied" not in command for _, command, _ in client.commands)
    assert any(path == "/workspace/inputs/source.csv" for _, path, _ in client.writes)
    assert len(client.destroyed) == 1
    assert client.destroyed[0].startswith("ae-analysis-")


@pytest.mark.asyncio
async def test_kubernetes_runner_timeout_is_not_replayed_and_destroys_claim(
    tmp_path: Path,
) -> None:
    client = FakeSandboxClient()

    def timeout_result(session_id: str, command: str, timeout: int = 60, env=None):
        del env
        client.commands.append((session_id, command, timeout))
        return SimpleNamespace(stdout="", stderr="timed out", exit_code=124)

    client.run = timeout_result  # type: ignore[method-assign]
    with pytest.raises(RunnerTimeout):
        await KubernetesAnalysisRunner(client).run(
            code="while True: pass",
            workspace=tmp_path / "timeout",
            execution_id="timeout-attempt",
            timeout_seconds=5,
        )

    assert len(client.commands) == 1
    assert len(client.destroyed) == 1


@pytest.mark.asyncio
async def test_kubernetes_runner_transport_failure_is_infrastructure_error(
    tmp_path: Path,
) -> None:
    client = FakeSandboxClient()

    def failed_run(session_id: str, command: str, timeout: int = 60, env=None):
        del session_id, command, timeout, env
        raise OSError("control plane unavailable")

    client.run = failed_run  # type: ignore[method-assign]
    with pytest.raises(RunnerInfrastructureError, match="transport"):
        await KubernetesAnalysisRunner(client).run(
            code="print('x')",
            workspace=tmp_path / "infra",
            execution_id="infra-attempt",
        )
    assert len(client.destroyed) == 1


@pytest.mark.asyncio
async def test_kubernetes_runner_rejects_untrusted_manifest_path(tmp_path: Path) -> None:
    client = FakeSandboxClient(
        manifest={
            "python_version": "3.12.11",
            "files": [{"path": "../escaped.txt", "size": 4}],
        }
    )
    with pytest.raises(RunnerOutputViolation, match="path"):
        await KubernetesAnalysisRunner(client).run(
            code="print('x')",
            workspace=tmp_path / "escape",
            execution_id="escape-attempt",
        )
    assert client.reads == []
    assert not (tmp_path / "escaped.txt").exists()
    assert len(client.destroyed) == 1


@pytest.mark.asyncio
async def test_kubernetes_runner_waits_for_destroy_when_cancelled(tmp_path: Path) -> None:
    client = FakeSandboxClient()
    started = threading.Event()
    released = threading.Event()

    def blocking_run(session_id: str, command: str, timeout: int = 60, env=None):
        del session_id, command, timeout, env
        started.set()
        released.wait(timeout=5)
        return SimpleNamespace(stdout="", stderr="", exit_code=0)

    def releasing_destroy(session_id: str) -> None:
        client.destroyed.append(session_id)
        released.set()

    client.run = blocking_run  # type: ignore[method-assign]
    client.destroy = releasing_destroy  # type: ignore[method-assign]
    task = asyncio.create_task(
        KubernetesAnalysisRunner(client).run(
            code="print('x')",
            workspace=tmp_path / "cancel",
            execution_id="cancel-attempt",
        )
    )
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert released.is_set()
    assert len(client.destroyed) == 1


@pytest.mark.asyncio
async def test_production_runner_disables_sdk_execute_retries(tmp_path: Path) -> None:
    client = FakeSandboxClient()
    session = __import__("requests").Session()
    session.mount("http://", HTTPAdapter(max_retries=5))
    connector = SimpleNamespace(session=session)
    client._connectors = {}  # type: ignore[attr-defined]

    original_write = client.write

    def creating_write(session_id: str, path: str, content: bytes | str) -> None:
        client._connectors.setdefault(session_id, connector)  # type: ignore[attr-defined]
        original_write(session_id, path, content)

    client.write = creating_write  # type: ignore[method-assign]
    result = await KubernetesAnalysisRunner(
        client, require_transport_hardening=True
    ).run(
        code="print('x')",
        workspace=tmp_path / "hardened",
        execution_id="hardened-attempt",
    )

    assert result.exit_code == 0
    assert session.get_adapter("http://").max_retries.total == 0
    assert session.get_adapter("https://").max_retries.total == 0
