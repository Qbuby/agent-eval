from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import os
import posixpath
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Protocol

from agent_eval.config import settings
from agent_eval.omniagent_runtime.policy import DEFAULT_POLICY

DEFAULT_TIMEOUT_SECONDS = 10 * 60
DEFAULT_LOG_BYTES = 256 * 1024
MAX_OUTPUT_FILES = 200

logger = logging.getLogger(__name__)


class RunnerError(RuntimeError):
    pass


class RunnerDisabled(RunnerError):
    pass


class RunnerTimeout(RunnerError):
    pass


class RunnerLogLimit(RunnerError):
    pass


class RunnerOutputViolation(RunnerError):
    pass


class RunnerInfrastructureError(RunnerError):
    """The sandbox transport or control plane failed before a trusted result existed."""


class SandboxClientProtocol(Protocol):
    def run(
        self,
        session_id: str,
        command: str,
        timeout: int = 60,
        env: dict[str, str] | None = None,
    ) -> Any: ...

    def write(self, session_id: str, path: str, content: bytes | str) -> None: ...

    def read(self, session_id: str, path: str) -> bytes: ...

    def destroy(self, session_id: str) -> None: ...


@dataclass(frozen=True)
class AnalysisRunResult:
    exit_code: int
    logs: str
    output_files: tuple[Path, ...]
    duration_seconds: float
    code_sha256: str
    python_version: str


_BOOTSTRAP = r'''
import os
import pathlib
import runpy
import socket
import sys

code_path = pathlib.Path(sys.argv[1]).resolve(strict=True)
input_root = pathlib.Path(sys.argv[2]).resolve(strict=True)
output_root = pathlib.Path(sys.argv[3]).resolve(strict=True)

runtime_roots = []
for item in [sys.base_prefix, sys.exec_prefix, *sys.path]:
    if not item:
        continue
    try:
        runtime_roots.append(pathlib.Path(item).resolve(strict=True))
    except (FileNotFoundError, OSError):
        pass

def within(path, roots):
    try:
        resolved = pathlib.Path(path).resolve(strict=False)
    except (OSError, TypeError, ValueError):
        return False
    return any(resolved == root or resolved.is_relative_to(root) for root in roots)

read_roots = [input_root, output_root, code_path.parent, *runtime_roots]
write_roots = [output_root]

def audit(event, args):
    if event.startswith("socket."):
        raise PermissionError("network access is disabled")
    if event.startswith("subprocess.") or event in {
        "os.system", "os.posix_spawn", "os.spawn", "pty.spawn"
    }:
        raise PermissionError("child processes are disabled")
    if event in {"ctypes.dlopen", "os.exec", "os.chdir", "os.fchdir"}:
        raise PermissionError(f"operation is disabled: {event}")
    if event == "open" and args:
        path = args[0]
        if isinstance(path, int):
            return
        mode = args[1] if len(args) > 1 and isinstance(args[1], str) else "r"
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        writes = any(char in mode for char in "wax+") or bool(
            flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        )
        roots = write_roots if writes else read_roots
        if not within(path, roots):
            raise PermissionError("filesystem access is outside the allowed roots")
    if event in {"os.listdir", "os.scandir", "os.remove", "os.rmdir", "os.rename"} and args:
        path = args[0]
        roots = write_roots if event != "os.listdir" and event != "os.scandir" else read_roots
        if not within(path, roots):
            raise PermissionError("filesystem access is outside the allowed roots")

sys.addaudithook(audit)
os.environ["OMNI_INPUT_DIR"] = str(input_root)
os.environ["OMNI_OUTPUT_DIR"] = str(output_root)
runpy.run_path(str(code_path), run_name="__main__")
'''


_OUTPUT_MANIFEST = r'''
import json
import pathlib
import sys

output_root = pathlib.Path(sys.argv[1]).resolve(strict=True)
policy = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
max_files = int(policy["max_files"])
max_bytes = int(policy["max_bytes"])
files = []
total = 0

for path in sorted(output_root.rglob("*")):
    if path.is_symlink():
        raise RuntimeError("analysis outputs cannot contain symbolic links")
    if path.is_dir():
        continue
    if not path.is_file():
        raise RuntimeError("analysis output is not a regular file")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(output_root):
        raise RuntimeError("analysis output escapes the output directory")
    relative = path.relative_to(output_root).as_posix()
    size = path.stat().st_size
    files.append({"path": relative, "size": size})
    total += size
    if len(files) > max_files:
        raise RuntimeError("analysis produced too many output files")
    if total > max_bytes:
        raise RuntimeError("analysis outputs exceed the configured byte limit")

print(json.dumps({"python_version": sys.version.split()[0], "files": files}, separators=(",", ":")))
'''


_KUBERNETES_EXECUTE_COMMAND = (
    "mkdir -p /workspace/inputs /workspace/outputs && "
    "/opt/runtime/bin/python -I /workspace/runtime/bootstrap.py "
    "/workspace/runtime/analysis.py /workspace/inputs /workspace/outputs"
)
_KUBERNETES_MANIFEST_COMMAND = (
    "/opt/runtime/bin/python -I /workspace/runtime/manifest.py "
    "/workspace/outputs /workspace/runtime/output-policy.json"
)


def _safe_workspace(workspace: Path) -> Path:
    if workspace.exists() and workspace.is_symlink():
        raise RunnerOutputViolation("job workspace cannot be a symbolic link")
    workspace.mkdir(parents=True, exist_ok=True)
    root = workspace.resolve(strict=True)
    for name in ("inputs", "outputs", "runtime", "tmp"):
        path = root / name
        if path.exists() and path.is_symlink():
            raise RunnerOutputViolation(f"job {name} directory cannot be a symbolic link")
        path.mkdir(exist_ok=True)
    return root


def _sanitized_env(workspace: Path) -> dict[str, str]:
    env: dict[str, str] = {
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "HOME": str(workspace / "tmp"),
        "USERPROFILE": str(workspace / "tmp"),
        "TMP": str(workspace / "tmp"),
        "TEMP": str(workspace / "tmp"),
        "LANG": "C.UTF-8",
    }
    for name in ("SYSTEMROOT", "WINDIR"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


async def _read_limited(stream: asyncio.StreamReader, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await stream.read(16 * 1024):
        size += len(chunk)
        if size > limit:
            raise RunnerLogLimit("analysis logs exceed the configured limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()


def collect_output_files(
    output_root: Path,
    *,
    max_bytes: int = DEFAULT_POLICY.artifact_job_output_bytes,
    max_files: int = MAX_OUTPUT_FILES,
) -> tuple[Path, ...]:
    root = output_root.resolve(strict=True)
    files: list[Path] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RunnerOutputViolation("analysis outputs cannot contain symbolic links")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RunnerOutputViolation("analysis output is not a regular file")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise RunnerOutputViolation("analysis output escapes the output directory")
        files.append(path)
        if len(files) > max_files:
            raise RunnerOutputViolation("analysis produced too many output files")
        total += path.stat().st_size
        if total > max_bytes:
            raise RunnerOutputViolation("analysis outputs exceed the configured byte limit")
    return tuple(files)


class LocalDevelopmentPythonRunner:
    """Development-only runner. Production must use the Kubernetes adapter."""

    async def run(
        self,
        *,
        code: str,
        workspace: Path,
        execution_id: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        log_bytes: int = DEFAULT_LOG_BYTES,
        output_bytes: int = DEFAULT_POLICY.artifact_job_output_bytes,
    ) -> AnalysisRunResult:
        del execution_id
        if settings.omniagent.runner != "local_dev":
            raise RunnerDisabled("local Python execution is disabled")
        if not isinstance(code, str) or not code.strip():
            raise RunnerError("analysis code is required")
        if len(code.encode("utf-8")) > 1024 * 1024:
            raise RunnerError("analysis code exceeds 1 MiB")

        root = _safe_workspace(workspace)
        code_path = root / "runtime" / "analysis.py"
        bootstrap_path = root / "runtime" / "bootstrap.py"
        code_path.write_text(code, encoding="utf-8", newline="\n")
        bootstrap_path.write_text(_BOOTSTRAP, encoding="utf-8", newline="\n")

        creationflags = 0
        kwargs: dict[str, object] = {}
        if os.name == "nt":
            creationflags = getattr(os, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        else:
            kwargs["start_new_session"] = True

        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(bootstrap_path),
            str(code_path),
            str(root / "inputs"),
            str(root / "outputs"),
            cwd=root,
            env=_sanitized_env(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=creationflags,
            **kwargs,
        )
        assert process.stdout is not None
        reader = asyncio.create_task(_read_limited(process.stdout, max(1024, log_bytes)))
        waiter = asyncio.create_task(process.wait())
        try:
            done, _ = await asyncio.wait(
                {reader, waiter},
                timeout=max(1, min(int(timeout_seconds), DEFAULT_TIMEOUT_SECONDS)),
                return_when=asyncio.FIRST_EXCEPTION,
            )
            if not done:
                raise RunnerTimeout("analysis exceeded the configured timeout")
            if reader in done and reader.exception() is not None:
                raise reader.exception()  # type: ignore[misc]
            await waiter
            logs = await reader
        except BaseException:
            await _terminate_process(process)
            reader.cancel()
            waiter.cancel()
            await asyncio.gather(reader, waiter, return_exceptions=True)
            raise

        outputs = collect_output_files(root / "outputs", max_bytes=output_bytes)
        return AnalysisRunResult(
            exit_code=int(process.returncode or 0),
            logs=logs.decode("utf-8", errors="replace"),
            output_files=outputs,
            duration_seconds=time.monotonic() - started,
            code_sha256=hashlib.sha256(code.encode("utf-8")).hexdigest(),
            python_version=sys.version.split()[0],
        )


def _validate_code(code: str) -> bytes:
    if not isinstance(code, str) or not code.strip():
        raise RunnerError("analysis code is required")
    encoded = code.encode("utf-8")
    if len(encoded) > 1024 * 1024:
        raise RunnerError("analysis code exceeds 1 MiB")
    return encoded


def _sandbox_session_id(execution_id: str) -> str:
    if not isinstance(execution_id, str) or not execution_id.strip():
        raise RunnerError("Kubernetes analysis execution id is required")
    digest = hashlib.sha256(execution_id.encode("utf-8")).hexdigest()[:40]
    return f"ae-analysis-{digest}"


def _safe_remote_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RunnerOutputViolation("invalid analysis output path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RunnerOutputViolation("invalid analysis output path")
    return path


def _prepare_local_output_target(output_root: Path, relative: PurePosixPath) -> Path:
    root = output_root.resolve(strict=True)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise RunnerOutputViolation("analysis output path is not a directory")
        else:
            current.mkdir()
        if not current.resolve(strict=True).is_relative_to(root):
            raise RunnerOutputViolation("analysis output escapes the output directory")

    target = current / relative.name
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise RunnerOutputViolation("analysis output target is not a regular file")
        target.unlink()
    if not target.resolve(strict=False).is_relative_to(root):
        raise RunnerOutputViolation("analysis output escapes the output directory")
    return target


def runner_configuration_error() -> str | None:
    cfg = settings.omniagent
    if cfg.runner == "local_dev":
        return None
    if cfg.runner != "kubernetes":
        return "analysis runner is disabled"
    if not cfg.kubernetes_runner_confirmed:
        return "Kubernetes analysis runner review gate is not confirmed"
    if not cfg.kubernetes_namespace.strip() or not cfg.kubernetes_template.strip():
        return "Kubernetes analysis runner namespace and template are required"
    minimum_ttl = (
        max(1, cfg.kubernetes_ready_timeout_seconds) + DEFAULT_TIMEOUT_SECONDS + 60
    )
    if cfg.kubernetes_claim_ttl_seconds < minimum_ttl:
        return "Kubernetes analysis claim TTL does not cover provisioning and execution"
    try:
        module = importlib.import_module("k8s_agent_sandbox")
    except ImportError:
        return "Kubernetes analysis runner SDK is unavailable"
    if not hasattr(module, "OmniAgentSandboxClient"):
        return "Kubernetes analysis runner SDK is incompatible"
    return None


class KubernetesAnalysisRunner:
    """Run one analysis attempt in one short-lived Kubernetes SandboxClaim."""

    def __init__(
        self,
        client: SandboxClientProtocol,
        *,
        require_transport_hardening: bool = False,
    ) -> None:
        self._client = client
        self._require_transport_hardening = require_transport_hardening
        self._transport_hardened: set[str] = set()

    @classmethod
    def from_settings(cls) -> "KubernetesAnalysisRunner":
        error = runner_configuration_error()
        if error is not None:
            raise RunnerDisabled(error)
        try:
            from k8s_agent_sandbox import OmniAgentSandboxClient
        except ImportError as exc:
            raise RunnerDisabled("Kubernetes analysis runner SDK is unavailable") from exc
        cfg = settings.omniagent
        try:
            client = OmniAgentSandboxClient(
                template_name=cfg.kubernetes_template,
                namespace=cfg.kubernetes_namespace,
                sandbox_ready_timeout=cfg.kubernetes_ready_timeout_seconds,
                shutdown_after_seconds=cfg.kubernetes_claim_ttl_seconds,
            )
        except Exception as exc:
            raise RunnerDisabled("Kubernetes analysis runner client initialization failed") from exc
        return cls(client, require_transport_hardening=True)

    @staticmethod
    def runtime_ref(execution_id: str) -> str:
        return f"sandboxclaim/{_sandbox_session_id(execution_id)}"

    async def _write(self, session_id: str, path: str, content: bytes | str) -> None:
        try:
            await asyncio.to_thread(self._client.write, session_id, path, content)
        except Exception as exc:
            raise RunnerInfrastructureError("sandbox upload failed") from exc

    async def _run(self, session_id: str, command: str, timeout: int) -> Any:
        await self._harden_transport(session_id)
        try:
            return await asyncio.to_thread(
                self._client.run,
                session_id,
                command,
                timeout=timeout,
            )
        except Exception as exc:
            raise RunnerInfrastructureError("sandbox command transport failed") from exc

    async def _harden_transport(self, session_id: str) -> None:
        """Disable SDK HTTP retries before the first non-idempotent execute request."""
        if not self._require_transport_hardening or session_id in self._transport_hardened:
            return

        def configure() -> None:
            connectors = getattr(self._client, "_connectors", None)
            connector = connectors.get(session_id) if isinstance(connectors, dict) else None
            session = getattr(connector, "session", None)
            if session is None or not callable(getattr(session, "mount", None)):
                raise RuntimeError("pinned sandbox SDK connector contract is unavailable")
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            adapter = HTTPAdapter(max_retries=Retry(total=0, redirect=0))
            session.mount("http://", adapter)
            session.mount("https://", adapter)

        try:
            await asyncio.to_thread(configure)
        except Exception as exc:
            raise RunnerInfrastructureError(
                "sandbox command transport could not be hardened against replay"
            ) from exc
        self._transport_hardened.add(session_id)

    async def _read(self, session_id: str, path: str) -> bytes:
        try:
            return await asyncio.to_thread(self._client.read, session_id, path)
        except Exception as exc:
            raise RunnerInfrastructureError("sandbox output download failed") from exc

    async def _destroy(self, session_id: str) -> None:
        destroy_task = asyncio.create_task(
            asyncio.to_thread(self._client.destroy, session_id)
        )
        try:
            await asyncio.shield(destroy_task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(destroy_task)
            except Exception as exc:
                logger.warning(
                    "failed to destroy analysis SandboxClaim %s: %s", session_id, exc
                )
            raise
        except Exception as exc:
            logger.warning("failed to destroy analysis SandboxClaim %s: %s", session_id, exc)

    async def run(
        self,
        *,
        code: str,
        workspace: Path,
        execution_id: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        log_bytes: int = DEFAULT_LOG_BYTES,
        output_bytes: int = DEFAULT_POLICY.artifact_job_output_bytes,
    ) -> AnalysisRunResult:
        if settings.omniagent.runner != "kubernetes":
            raise RunnerDisabled("Kubernetes analysis execution is disabled")
        if not settings.omniagent.kubernetes_runner_confirmed:
            raise RunnerDisabled("Kubernetes analysis runner review gate is not confirmed")
        encoded_code = _validate_code(code)
        session_id = _sandbox_session_id(execution_id or "")
        root = _safe_workspace(workspace)
        timeout = max(1, min(int(timeout_seconds), DEFAULT_TIMEOUT_SECONDS))
        output_limit = max(0, min(int(output_bytes), DEFAULT_POLICY.artifact_job_output_bytes))
        log_limit = max(1024, int(log_bytes))
        started = time.monotonic()

        try:
            await self._write(session_id, "/workspace/runtime/analysis.py", encoded_code)
            await self._write(session_id, "/workspace/runtime/bootstrap.py", _BOOTSTRAP)
            await self._write(session_id, "/workspace/runtime/manifest.py", _OUTPUT_MANIFEST)
            await self._write(
                session_id,
                "/workspace/runtime/output-policy.json",
                json.dumps(
                    {"max_files": MAX_OUTPUT_FILES, "max_bytes": output_limit},
                    separators=(",", ":"),
                ),
            )
            input_root = root / "inputs"
            for path in sorted(input_root.rglob("*")):
                if path.is_symlink():
                    raise RunnerOutputViolation("analysis inputs cannot contain symbolic links")
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise RunnerOutputViolation("analysis input is not a regular file")
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(input_root.resolve(strict=True)):
                    raise RunnerOutputViolation("analysis input escapes the input directory")
                relative = path.relative_to(input_root).as_posix()
                remote = posixpath.join("/workspace/inputs", relative)
                await self._write(session_id, remote, path.read_bytes())

            execution = await self._run(session_id, _KUBERNETES_EXECUTE_COMMAND, timeout)
            stdout = str(getattr(execution, "stdout", "") or "")
            stderr = str(getattr(execution, "stderr", "") or "")
            logs = stdout + (("\n" if stdout and stderr else "") + stderr if stderr else "")
            if len(logs.encode("utf-8")) > log_limit:
                raise RunnerLogLimit("analysis logs exceed the configured limit")
            exit_code = int(getattr(execution, "exit_code", -1))
            if exit_code == 124:
                raise RunnerTimeout("analysis exceeded the configured timeout")
            if exit_code != 0:
                return AnalysisRunResult(
                    exit_code=exit_code,
                    logs=logs,
                    output_files=(),
                    duration_seconds=time.monotonic() - started,
                    code_sha256=hashlib.sha256(encoded_code).hexdigest(),
                    python_version="unknown",
                )

            manifest_result = await self._run(
                session_id, _KUBERNETES_MANIFEST_COMMAND, min(60, timeout)
            )
            if int(getattr(manifest_result, "exit_code", -1)) != 0:
                detail = str(getattr(manifest_result, "stderr", "") or "")[-1000:]
                raise RunnerOutputViolation(detail or "analysis output validation failed")
            manifest_raw = str(getattr(manifest_result, "stdout", "") or "")
            try:
                manifest = json.loads(manifest_raw)
            except (TypeError, json.JSONDecodeError) as exc:
                raise RunnerOutputViolation("invalid analysis output manifest") from exc
            entries = manifest.get("files") if isinstance(manifest, dict) else None
            if not isinstance(entries, list) or len(entries) > MAX_OUTPUT_FILES:
                raise RunnerOutputViolation("invalid analysis output manifest")

            seen: set[str] = set()
            total = 0
            for entry in entries:
                if not isinstance(entry, dict):
                    raise RunnerOutputViolation("invalid analysis output manifest")
                relative = _safe_remote_relative_path(entry.get("path"))
                relative_text = relative.as_posix()
                if relative_text in seen:
                    raise RunnerOutputViolation("duplicate analysis output path")
                seen.add(relative_text)
                size = entry.get("size")
                if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                    raise RunnerOutputViolation("invalid analysis output size")
                total += size
                if total > output_limit:
                    raise RunnerOutputViolation(
                        "analysis outputs exceed the configured byte limit"
                    )
                content = await self._read(
                    session_id, posixpath.join("/workspace/outputs", relative_text)
                )
                if len(content) != size:
                    raise RunnerOutputViolation("analysis output changed during download")
                target = _prepare_local_output_target(root / "outputs", relative)
                with target.open("xb") as handle:
                    handle.write(content)

            outputs = collect_output_files(root / "outputs", max_bytes=output_limit)
            python_version = manifest.get("python_version")
            if not isinstance(python_version, str) or len(python_version) > 64:
                raise RunnerOutputViolation("invalid analysis runtime version")
            return AnalysisRunResult(
                exit_code=0,
                logs=logs,
                output_files=outputs,
                duration_seconds=time.monotonic() - started,
                code_sha256=hashlib.sha256(encoded_code).hexdigest(),
                python_version=python_version,
            )
        finally:
            await self._destroy(session_id)


def runner_from_settings() -> LocalDevelopmentPythonRunner | KubernetesAnalysisRunner:
    if settings.omniagent.runner == "local_dev":
        return LocalDevelopmentPythonRunner()
    if settings.omniagent.runner == "kubernetes":
        return KubernetesAnalysisRunner.from_settings()
    raise RunnerDisabled("analysis runner is disabled")
