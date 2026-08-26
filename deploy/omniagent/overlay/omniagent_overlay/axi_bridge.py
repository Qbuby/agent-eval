"""Sandbox-side fixed argv bridge for the Axi CLI.

The control plane uploads this file and a JSON request. No model-provided value is
interpolated into a shell command. The execution token is inherited only by ``run``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

MAX_REQUEST_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 96 * 1024
TIMEOUT_SECONDS = 30
ALLOWED_TOOLS = frozenset({"data/search", "data/describe", "data/query"})


class BridgeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _read_request(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_REQUEST_BYTES:
        raise ValueError("request exceeds the bridge limit")
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError("request must be an object")
    return body


def _argv(operation: str, body: dict[str, Any]) -> list[str]:
    if operation == "search":
        query = body.get("query")
        top_k = body.get("top_k", 5)
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise ValueError("top_k must be an integer")
        return ["axi", "search", query, "--top-k", str(max(1, min(top_k, 10)))]
    if operation == "describe":
        tool_name = body.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name is required")
        if tool_name not in ALLOWED_TOOLS:
            raise BridgeError("CAPABILITY_DENIED", "tool is not in the reviewed allowlist")
        return ["axi", "describe", tool_name]
    if operation == "run":
        tool_name = body.get("tool_name")
        arguments = body.get("arguments", {})
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name is required")
        if tool_name not in ALLOWED_TOOLS:
            raise BridgeError("CAPABILITY_DENIED", "tool is not in the reviewed allowlist")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError("arguments exceed the bridge limit")
        return ["axi", "run", tool_name, "--json", encoded]
    raise ValueError("unsupported operation")


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def main() -> int:
    request_path: Path | None = None
    try:
        if len(sys.argv) != 3:
            raise ValueError("bridge requires operation and request path")
        operation = sys.argv[1]
        request_path = Path(sys.argv[2]).resolve(strict=True)
        body = _read_request(request_path)
        completed = subprocess.run(
            _argv(operation, body),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        if len(completed.stdout) > MAX_OUTPUT_BYTES or len(completed.stderr) > MAX_OUTPUT_BYTES:
            raise RuntimeError("Axi output exceeds the bridge limit")
        try:
            result = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Axi returned invalid JSON") from exc
        if operation == "search":
            if not isinstance(result, list):
                raise RuntimeError("Axi returned an invalid search response")
            result = [
                item
                for item in result
                if isinstance(item, dict) and item.get("name") in ALLOWED_TOOLS
            ]
            if completed.returncode != 0:
                raise RuntimeError("Axi command failed")
            _emit({"ok": True, "data": result})
        elif operation == "run":
            if not isinstance(result, dict) or result.get("status") not in {"success", "error"}:
                raise RuntimeError("Axi returned an invalid run envelope")
            if result.get("status") == "success":
                _emit({"ok": True, "data": result.get("data")})
            else:
                _emit({"ok": False, "error": {"code": "AXI_RUN_FAILED", "message": str(result.get("error") or "Axi run failed")[:1000]}})
        else:
            if completed.returncode != 0:
                raise RuntimeError("Axi command failed")
            _emit({"ok": True, "data": result})
        return 0
    except subprocess.TimeoutExpired:
        _emit({"ok": False, "error": {"code": "AXI_TIMEOUT", "message": "Axi command timed out"}})
        return 0
    except BridgeError as exc:
        _emit({"ok": False, "error": {"code": exc.code, "message": str(exc)[:1000]}})
        return 0
    except Exception as exc:
        _emit({"ok": False, "error": {"code": "AXI_BRIDGE_ERROR", "message": str(exc)[:1000]}})
        return 0
    finally:
        if request_path is not None:
            request_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
