from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest


RUNTIME = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "omniagent"
    / "analysis-runtime"
)


def _load_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    temp = tmp_path / "tmp"
    workspace.mkdir()
    temp.mkdir()
    monkeypatch.setenv("SANDBOX_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("SANDBOX_TEMP_DIR", str(temp))
    spec = importlib.util.spec_from_file_location(
        f"agent_eval_analysis_runtime_{tmp_path.name}", RUNTIME / "main.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, workspace


@pytest.mark.asyncio
async def test_analysis_runtime_health_and_atomic_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, workspace = _load_runtime(monkeypatch, tmp_path)
    transport = httpx.ASGITransport(app=runtime.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://runtime") as client:
        assert (await client.get("/healthz")).json() == {"status": "ok"}

        target = str(workspace / "input.txt")
        first = await client.post("/upload", files={"file": (target, b"first")})
        second = await client.post("/upload", files={"file": (target, b"second")})
        downloaded = await client.get(f"/download/{target}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert downloaded.content == b"second"
    assert (workspace / "input.txt").read_bytes() == b"second"
    assert list(workspace.glob("*.part")) == []


@pytest.mark.asyncio
async def test_analysis_runtime_rejects_oversize_without_replacing_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, workspace = _load_runtime(monkeypatch, tmp_path)
    runtime.MAX_UPLOAD_BYTES = 4
    target = workspace / "input.txt"
    target.write_bytes(b"safe")
    transport = httpx.ASGITransport(app=runtime.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://runtime") as client:
        response = await client.post(
            "/upload", files={"file": (str(target), b"too-large")}
        )

    assert response.status_code == 413
    assert target.read_bytes() == b"safe"
    assert list(workspace.glob("*.part")) == []


@pytest.mark.asyncio
async def test_analysis_runtime_rejects_escape_and_oversize_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, workspace = _load_runtime(monkeypatch, tmp_path)
    runtime.MAX_DOWNLOAD_BYTES = 4
    (workspace / "large.bin").write_bytes(b"12345")
    transport = httpx.ASGITransport(app=runtime.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://runtime") as client:
        escaped = await client.get("/download/%2Fetc%2Fpasswd")
        oversized = await client.get(f"/download/{workspace / 'large.bin'}")

    assert escaped.status_code == 403
    assert oversized.status_code == 413


def test_analysis_runtime_combines_stdout_and_stderr_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _ = _load_runtime(monkeypatch, tmp_path)
    budget = runtime._OutputBudget(5)
    budget.consume(3)
    with pytest.raises(runtime._OutputLimitExceeded):
        budget.consume(3)


def test_analysis_runtime_image_is_non_root_and_axi_free() -> None:
    dockerfile = (RUNTIME / "Dockerfile").read_text(encoding="utf-8")
    requirements = (RUNTIME / "requirements.txt").read_text(encoding="utf-8")
    assert "python:3.12.13-slim-bookworm@sha256:" in dockerfile
    assert "4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "main:app" in dockerfile
    assert "axi" not in requirements.lower()
    assert "python-multipart==0.0.32" in requirements
    assert all("==" in line for line in requirements.splitlines() if line.strip())


def test_analysis_runtime_smoke_matches_sandbox_security_contract() -> None:
    smoke = (RUNTIME / "smoke.sh").read_text(encoding="utf-8")

    assert 'trap cleanup EXIT HUP INT TERM' in smoke
    assert 'docker rm -f "$NAME"' in smoke
    assert '--read-only' in smoke
    assert '--cap-drop ALL' in smoke
    assert '--security-opt no-new-privileges' in smoke
    assert '--pids-limit 64' in smoke
    assert '--memory 512m' in smoke
    assert '--publish 127.0.0.1::8888' in smoke
    assert '"SMOKE_ENV":"one-command-only"' in smoke
    assert '\\"${SMOKE_ENV-unset}\\"' in smoke
    assert '"exit_code"] == 124' in smoke
    assert '/workspace/survived' in smoke
    assert '"$STATUS" = 404' in smoke
    assert '"$ROOT_STATUS" = 403' in smoke
