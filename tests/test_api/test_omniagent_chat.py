from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

os.environ.setdefault("AUTH_SECRET_KEY", "test-secret-key")

from agent_eval.api.app import create_app
from agent_eval.config import settings
from agent_eval.db_models.tenant_context import INTERNAL_TENANT_ID
from agent_eval.omniagent_runtime.security import decode_execution_token
from agent_eval.services.omniagent_chat import (
    OmniAgentChatService,
    content_to_text,
    owner_clause,
    owner_scope,
)
from agent_eval.db_models.tables import OmniAgentChatSessionRow


def _service() -> OmniAgentChatService:
    return OmniAgentChatService(
        session_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
        thread_id="ae-chat-test",
        question="你好",
        user_identity="agent-eval-user-test",
        url="http://omniagent.invalid/api/agent/langgraph",
    )


def test_payload_is_strict_v1_shape_and_identity_uses_header():
    service = _service()

    assert service.build_payload() == {
        "question": "你好",
        "configurable": {
            "thread_id": "ae-chat-test",
            "language": "请用中文回复",
        },
    }
    assert "stream" not in service.build_payload()
    assert "user_id" not in service.build_payload()
    assert service.build_headers()["X-User-Id"] == "agent-eval-user-test"


def test_execution_payload_is_bound_to_current_assistant_message(monkeypatch):
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session_id = uuid.uuid4()
    message_id = uuid.uuid4()
    monkeypatch.setattr(settings.omniagent, "execution_enabled", True)
    monkeypatch.setattr(settings.omniagent, "execution_tenant_allowlist", str(tenant_id))
    monkeypatch.setattr(
        settings.omniagent,
        "execution_secret_key",
        "test-execution-secret-key-with-at-least-32-bytes",
    )
    service = OmniAgentChatService(
        session_id=session_id,
        assistant_message_id=message_id,
        thread_id="ae-chat-execution",
        question="查询数据",
        user_identity="agent-eval-user-test",
        execution_user_id=user_id,
        execution_tenant_id=tenant_id,
        execution_role="superadmin",
    )

    token = service.build_payload()["configurable"]["execution_auth"]["token"]
    principal = decode_execution_token(token)

    assert principal.user_id == user_id
    assert principal.tenant_id == tenant_id
    assert principal.session_id == session_id
    assert principal.message_id == message_id
    assert principal.is_superadmin is False
    assert {"data:search", "data:describe", "data:query", "action:prepare"} <= principal.scopes


def test_execution_auth_is_not_issued_for_dev_identity(monkeypatch):
    monkeypatch.setattr(settings.omniagent, "execution_enabled", True)
    service = _service()

    assert "execution_auth" not in service.build_payload()["configurable"]


def test_execution_auth_is_not_issued_outside_tenant_allowlist(monkeypatch):
    tenant_id = uuid.uuid4()
    monkeypatch.setattr(settings.omniagent, "execution_enabled", True)
    monkeypatch.setattr(settings.omniagent, "execution_tenant_allowlist", str(uuid.uuid4()))
    service = OmniAgentChatService(
        session_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
        thread_id="ae-chat-not-allowlisted",
        question="查询数据",
        user_identity="agent-eval-user-test",
        execution_user_id=uuid.uuid4(),
        execution_tenant_id=tenant_id,
        execution_role="user",
    )

    assert "execution_auth" not in service.build_payload()["configurable"]


def test_protocol_and_control_frames_are_normalized():
    service = _service()
    message_id = str(service.assistant_message_id)

    assert service.normalize({"event": "command_result", "text": "完成"}) == (
        [{"type": "content_delta", "message_id": message_id, "delta": "完成"}],
        False,
    )
    assert service.normalize(
        {"event": "structured_output", "structured_output": {"score": 1}}
    ) == (
        [
            {
                "type": "structured_output",
                "message_id": message_id,
                "data": {"score": 1},
            }
        ],
        False,
    )
    events, stop = service.normalize({"status": "error", "error": "boom"})
    assert stop is True
    assert events == [{"type": "error", "message_id": message_id, "message": "boom"}]


def test_langgraph_text_and_tool_frames_are_normalized():
    service = _service()
    message_id = str(service.assistant_message_id)

    events, stop = service.normalize(
        {
            "event": "on_chat_model_stream",
            "data": {"chunk": {"kwargs": {"content": [{"type": "text", "text": "回答"}]}}},
        }
    )
    assert stop is False
    assert events == [{"type": "content_delta", "message_id": message_id, "delta": "回答"}]

    events, _ = service.normalize(
        {"event": "on_tool_start", "run_id": "tool-1", "name": "search", "data": {"input": {"q": "x"}}}
    )
    assert events[0]["type"] == "tool_start"
    assert events[0]["tool_call_id"] == "tool-1"
    events, _ = service.normalize(
        {"event": "on_tool_end", "run_id": "tool-1", "name": "search", "data": {"output": {"ok": True}}}
    )
    assert events[0]["type"] == "tool_end"
    assert events[0]["output"] == {"ok": True}


def test_owner_scope_is_explicit_even_for_superadmin_and_dev_mode():
    assert owner_scope(None) == (INTERNAL_TENANT_ID, None)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    user = SimpleNamespace(tenant_id=tenant_id, id=user_id, is_superadmin=True)
    assert owner_scope(user) == (tenant_id, user_id)

    clauses = owner_clause(OmniAgentChatSessionRow, user)
    rendered = " ".join(str(clause) for clause in clauses)
    assert "tenant_id" in rendered
    assert "created_by" in rendered


def test_content_projection_accepts_future_content_blocks():
    assert content_to_text("纯文本") == "纯文本"
    assert content_to_text(
        [{"type": "text", "text": "前"}, {"type": "image", "url": "x"}, {"type": "text", "text": "后"}]
    ) == "前后"


def test_internal_omniagent_routes_are_registered():
    def collect_paths(routes):
        paths = set()
        for route in routes:
            if hasattr(route, "path"):
                paths.add(route.path)
            original = getattr(route, "original_router", None)
            if original is not None:
                paths.update(collect_paths(original.routes))
        return paths

    paths = collect_paths(create_app().routes)
    expected = {
        "/api/omniagent/sessions",
        "/api/omniagent/sessions/{session_id}",
        "/api/omniagent/sessions/{session_id}/messages",
        "/api/omniagent/sessions/{session_id}/messages/stream",
        "/api/omniagent/sessions/{session_id}/messages/{message_id}/retry",
        "/api/omniagent/events",
        "/api/omniagent/jobs",
        "/api/omniagent/actions",
        "/api/omniagent/memories",
        "/api/omniagent/artifacts",
        "/api/omniagent/notifications",
        "/api/omniagent/schedules",
        "/internal/omniagent/v1/actions/prepare",
        "/internal/omniagent/v1/jobs",
        "/internal/omniagent/v1/memories/search",
        "/internal/omniagent/v1/analysis/submit",
        "/internal/omniagent-data/v1/search",
        "/internal/omniagent-data/v1/describe",
        "/internal/omniagent-data/v1/query",
    }
    assert expected <= set(paths)
