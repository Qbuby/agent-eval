from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "http://127.0.0.1:18082"
EVIDENCE_DIR = ROOT / ".codex_tmp" / "omniagent-two-tenant-evidence"
FIXTURE = json.loads(
    (ROOT / ".codex_tmp" / "omniagent-two-tenant-fixture.json").read_text(
        encoding="utf-8"
    )
)


def request_json(
    path: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else None


def login(user: dict[str, str]) -> str:
    status, payload = request_json(
        "/api/auth/login",
        method="POST",
        body={"username": user["username"], "password": user["password"]},
    )
    if status != 200 or not isinstance(payload, dict):
        raise AssertionError(f"login failed for {user['username']}: {status} {payload}")
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise AssertionError("login response did not contain an access token")
    return token


def verify_tenant(key: str, other_key: str) -> dict[str, Any]:
    own = FIXTURE[key]
    other = FIXTURE[other_key]
    token = login(own)

    list_paths = (
        ("/api/omniagent/sessions", "session_id"),
        ("/api/omniagent/events", "marker"),
        ("/api/omniagent/jobs", "job_id"),
        ("/api/omniagent/actions", "action_id"),
        ("/api/omniagent/artifacts", "artifact_id"),
        ("/api/omniagent/memories", "memory_id"),
        ("/api/omniagent/notifications", "notification_id"),
        ("/api/omniagent/schedules", "schedule_id"),
    )
    for path, evidence_key in list_paths:
        status, payload = request_json(path, token=token)
        serialized = json.dumps(payload, ensure_ascii=False)
        if status != 200:
            raise AssertionError(f"{path} returned {status}: {serialized}")
        if own[evidence_key] not in serialized:
            raise AssertionError(
                f"own {evidence_key} absent from {path}: {serialized}"
            )
        if other[evidence_key] in serialized:
            raise AssertionError(
                f"cross-tenant {evidence_key} leaked from {path}: {serialized}"
            )

    direct_paths = (
        f"/api/omniagent/sessions/{other['session_id']}",
        f"/api/omniagent/jobs/{other['job_id']}",
        f"/api/omniagent/actions/{other['action_id']}",
        f"/api/omniagent/artifacts/{other['artifact_id']}/download",
    )
    for path in direct_paths:
        status, payload = request_json(path, token=token)
        if status != 404:
            raise AssertionError(f"cross-tenant direct access {path} returned {status}: {payload}")

    mutation_paths = (
        (f"/api/omniagent/sessions/{other['session_id']}", "DELETE"),
        (f"/api/omniagent/memories/{other['memory_id']}", "DELETE"),
        (f"/api/omniagent/jobs/{other['job_id']}/cancel", "POST"),
        (f"/api/omniagent/notifications/{other['notification_id']}/read", "POST"),
        (f"/api/omniagent/schedules/{other['schedule_id']}/pause", "POST"),
    )
    for path, method in mutation_paths:
        status, payload = request_json(path, token=token, method=method)
        if status != 404:
            raise AssertionError(
                f"cross-tenant mutation {method} {path} returned {status}: {payload}"
            )

    status, payload = request_json(
        f"/api/omniagent/memories?q={other['marker']}", token=token
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    if status != 200 or other["marker"] in serialized:
        raise AssertionError(f"cross-tenant memory query leaked data: {status} {serialized}")

    status, payload = request_json(
        f"/api/omniagent/events?after={max(0, other['event_cursor'] - 1)}",
        token=token,
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    if status != 200 or other["marker"] in serialized:
        raise AssertionError(f"cross-tenant event cursor leaked data: {status} {serialized}")

    status, payload = request_json(
        f"/api/omniagent/events?session_id={other['session_id']}", token=token
    )
    if status != 200 or not isinstance(payload, dict) or payload.get("items") != []:
        raise AssertionError(f"cross-tenant event session filter leaked data: {status} {payload}")

    return {
        "tenant": key,
        "marker": own["marker"],
        "list_isolation": True,
        "direct_access_404": True,
        "mutation_404": True,
        "query_isolation": True,
        "cursor_isolation": True,
    }


def main() -> int:
    result = {
        "ok": True,
        "alpha": verify_tenant("a", "b"),
        "beta": verify_tenant("b", "a"),
    }
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    (EVIDENCE_DIR / "api-result.json").write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
