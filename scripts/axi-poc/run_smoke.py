#!/usr/bin/env python3
"""Exercise the reviewed Axi 0.0.11 contract against a loopback-only fake API."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


EXPECTED_TOOLS = {"data/search", "data/describe", "data/query"}
CANARY_TOKEN = "axi-poc-canary-token"


class _Handler(BaseHTTPRequestHandler):
    server_version = "AxiPoc/1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except BrokenPipeError:
            pass

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"")
        self.server.seen.append(  # type: ignore[attr-defined]
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": body,
            }
        )
        if body.get("query") == "timeout-proof":
            time.sleep(9)
            self._reply(200, {"entities": []})
            return
        if body.get("query") == "denied-proof":
            self._reply(
                403,
                {"detail": {"code": "FIELD_DENIED", "message": "field is denied"}},
            )
            return
        self._reply(200, {"entities": [{"name": "evaluation_runs"}]})


def _command(axi: Path, arguments: list[str], env: dict[str, str]) -> tuple[Any, str]:
    completed = subprocess.run(
        [str(axi), *arguments],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    if CANARY_TOKEN in completed.stdout or CANARY_TOKEN in completed.stderr:
        raise AssertionError("execution token leaked into Axi output")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Axi stdout is not one JSON value: {completed.stdout!r}; stderr={completed.stderr!r}"
        ) from exc
    return value, completed.stderr


def run(axi: Path) -> dict[str, Any]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.seen = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "AXI_RICH": "0",
            "AGENT_EVAL_INTERNAL_URL": f"http://127.0.0.1:{server.server_port}",
            "AGENT_EVAL_EXECUTION_TOKEN": CANARY_TOKEN,
        }

        discovered, search_stderr = _command(
            axi, ["search", "Agent Eval data", "--top-k", "10"], env
        )
        names = {item["name"] for item in discovered}
        assert names == EXPECTED_TOOLS, names

        described, _ = _command(axi, ["describe", "data/query"], env)
        assert described["server"] == "data"
        assert described["name"] == "query"
        assert described["input_schema"]["required"] == ["request"]

        success, _ = _command(
            axi,
            ["run", "data/search", "--json", '{"query":"runs","limit":1}'],
            env,
        )
        assert success == {
            "status": "success",
            "data": {
                "ok": True,
                "data": {"entities": [{"name": "evaluation_runs"}]},
            },
        }

        denied, _ = _command(
            axi,
            ["run", "data/search", "--json", '{"query":"denied-proof"}'],
            env,
        )
        assert denied["status"] == "success"
        assert denied["data"]["error"]["code"] == "FIELD_DENIED"

        timed_out, _ = _command(
            axi,
            ["run", "data/search", "--json", '{"query":"timeout-proof"}'],
            env,
        )
        assert timed_out["status"] == "success"
        assert timed_out["data"]["error"]["code"] == "QUERY_TIMEOUT"

        malformed, _ = _command(
            axi, ["run", "data/search", "--json", "{bad-json"], env
        )
        assert malformed["status"] == "error"
        assert "Invalid JSON argument" in malformed["error"]

        seen = server.seen  # type: ignore[attr-defined]
        assert len(seen) == 3
        assert all(item["authorization"] == f"Bearer {CANARY_TOKEN}" for item in seen)
        assert all(CANARY_TOKEN not in json.dumps(item["body"]) for item in seen)
        return {
            "axi": str(axi),
            "tools": sorted(names),
            "success": "ok",
            "business_error": "FIELD_DENIED",
            "timeout": "QUERY_TIMEOUT",
            "malformed": "status=error",
            "stderr_isolated": bool(search_stderr),
            "token_leak": False,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axi", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.axi), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
