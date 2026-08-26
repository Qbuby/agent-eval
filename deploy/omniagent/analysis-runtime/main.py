"""Bounded HTTP runtime for one Agent Eval analysis sandbox."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import urllib.parse
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

WORKSPACE_DIR = Path(os.environ.get("SANDBOX_WORKSPACE_DIR", "/workspace"))
TEMP_DIR = Path(os.environ.get("SANDBOX_TEMP_DIR", "/tmp"))
ALLOWED_DIRS = (WORKSPACE_DIR, TEMP_DIR)
MAX_TIMEOUT_SECONDS = 600
MAX_STREAM_BYTES = 256 * 1024
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
TERMINATE_GRACE_SECONDS = 0.5

logger = logging.getLogger("agent-eval-analysis-runtime")
app = FastAPI(title="Agent Eval Analysis Runtime", version="1.0.0")


class ExecuteRequest(BaseModel):
    command: str = Field(min_length=1, max_length=16_384)
    timeout: float = Field(default=60, ge=1, le=MAX_TIMEOUT_SECONDS)
    env: dict[str, str] | None = None


class ExecuteResponse(BaseModel):
    stdout: str = ""
    stderr: str = ""
    exit_code: int
    cwd: str = str(WORKSPACE_DIR)


class _OutputLimitExceeded(RuntimeError):
    pass


class _OutputBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.consumed = 0

    def consume(self, size: int) -> None:
        self.consumed += size
        if self.consumed > self.maximum:
            raise _OutputLimitExceeded


def _safe_path(raw_path: str) -> Path:
    decoded = urllib.parse.unquote(raw_path)
    candidate = Path(decoded)
    if not candidate.is_absolute():
        candidate = WORKSPACE_DIR / candidate
    lexical = Path(os.path.abspath(candidate))
    resolved = lexical.resolve(strict=False)
    for base in ALLOWED_DIRS:
        root = base.resolve(strict=True)
        if resolved == root or resolved.is_relative_to(root):
            try:
                relative = lexical.relative_to(root)
            except ValueError:
                continue
            current = root
            for part in relative.parts:
                current /= part
                if current.exists() and current.is_symlink():
                    raise HTTPException(
                        status_code=403,
                        detail="symbolic-link paths are not allowed",
                    )
            return lexical
    raise HTTPException(status_code=403, detail="path is outside allowed directories")


async def _stop_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=TERMINATE_GRACE_SECONDS)
        return
    except TimeoutError:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    await process.wait()


async def _read_limited(stream: asyncio.StreamReader, budget: _OutputBudget) -> bytes:
    chunks: list[bytes] = []
    while chunk := await stream.read(16 * 1024):
        budget.consume(len(chunk))
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace").rstrip("\n")


@app.get("/")
@app.get("/healthz")
@app.get("/readyz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/execute", response_model=ExecuteResponse)
async def execute(request: ExecuteRequest) -> ExecuteResponse:
    environment = dict(os.environ)
    if request.env:
        environment.update(request.env)
    process = await asyncio.create_subprocess_exec(
        "/bin/bash",
        "-lc",
        request.command,
        cwd=WORKSPACE_DIR,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    budget = _OutputBudget(MAX_STREAM_BYTES)
    stdout_task = asyncio.create_task(_read_limited(process.stdout, budget))
    stderr_task = asyncio.create_task(_read_limited(process.stderr, budget))
    wait_task = asyncio.create_task(process.wait())
    started = time.monotonic()
    try:
        done, _ = await asyncio.wait(
            {stdout_task, stderr_task, wait_task},
            timeout=request.timeout,
            return_when=asyncio.FIRST_EXCEPTION,
        )
        if not done:
            await _stop_process_group(process)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            return ExecuteResponse(
                stderr=f"Command timed out after {request.timeout:g} seconds",
                exit_code=124,
            )
        for task in (stdout_task, stderr_task):
            if task.done() and isinstance(task.exception(), _OutputLimitExceeded):
                await _stop_process_group(process)
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                return ExecuteResponse(
                    stderr="Command output exceeded the runtime limit",
                    exit_code=125,
                )
        await wait_task
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        return ExecuteResponse(
            stdout=_decode(stdout),
            stderr=_decode(stderr),
            exit_code=int(process.returncode if process.returncode is not None else -1),
        )
    except BaseException:
        await _stop_process_group(process)
        await asyncio.gather(stdout_task, stderr_task, wait_task, return_exceptions=True)
        raise
    finally:
        for task in (stdout_task, stderr_task, wait_task):
            if not task.done():
                task.cancel()
        logger.info(
            "execute complete command_bytes=%d duration_ms=%d",
            len(request.command.encode("utf-8")),
            int((time.monotonic() - started) * 1000),
        )


@app.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, str]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing target path")
    target = _safe_path(file.filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _safe_path(str(target))
    staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    size = 0
    try:
        with staging.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="upload exceeds runtime limit")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        target = _safe_path(str(target))
        os.replace(staging, target)
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return {"message": "uploaded", "path": str(target)}


@app.get("/download/{path:path}")
async def download(path: str) -> StreamingResponse:
    target = _safe_path(path)
    if target.is_symlink() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    if target.stat().st_size > MAX_DOWNLOAD_BYTES:
        raise HTTPException(status_code=413, detail="download exceeds runtime limit")

    async def content():
        sent = 0
        with target.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                sent += len(chunk)
                if sent > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("download exceeded runtime limit during streaming")
                yield chunk

    return StreamingResponse(content(), media_type="application/octet-stream")
