from __future__ import annotations

import inspect
import os
import uuid

os.environ.setdefault("AUTH_SECRET_KEY", "test-secret-key")

from sqlalchemy.dialects import postgresql

from agent_eval.api.routers import portal


def test_feedback_sample_lookup_selects_only_the_primary_key() -> None:
    """反馈存在性校验不得读取可能内联数 MB 图片的 question_content。"""
    statement = portal._feedback_sample_exists_statement(uuid.uuid4())
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    selected = sql.partition(" from ")[0]
    assert "portal_samples.id" in selected
    assert "question_content" not in sql
    assert "portal_samples.question" not in selected
    assert "portal_samples.answer" not in selected
    assert "portal_samples.extra" not in selected


def test_feedback_submit_does_not_refresh_after_commit() -> None:
    source = inspect.getsource(portal.submit_feedback)

    assert "session.refresh" not in source
