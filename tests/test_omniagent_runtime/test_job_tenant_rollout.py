from __future__ import annotations

import uuid

import pytest
from sqlalchemy.dialects import postgresql

from agent_eval.config import settings
from agent_eval.omniagent_runtime.jobs import claim_next_job
from agent_eval.omniagent_runtime.security import enabled_execution_tenants


class _NoJobResult:
    def scalar_one_or_none(self):
        return None


class _CapturingSession:
    def __init__(self) -> None:
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _NoJobResult()


@pytest.mark.asyncio
async def test_empty_tenant_rollout_skips_database_claim() -> None:
    db = _CapturingSession()

    claimed = await claim_next_job(
        db,  # type: ignore[arg-type]
        worker_id="worker",
        tenant_ids=frozenset(),
    )

    assert claimed is None
    assert db.statements == []


@pytest.mark.asyncio
async def test_claim_statement_filters_to_enabled_tenants() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    db = _CapturingSession()

    claimed = await claim_next_job(
        db,  # type: ignore[arg-type]
        worker_id="worker",
        kinds=frozenset({"analysis.python"}),
        tenant_ids=frozenset({first, second}),
    )

    assert claimed is None
    assert len(db.statements) == 1
    compiled = db.statements[0].compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    rendered = str(compiled)
    assert "omniagent_jobs.tenant_id IN" in rendered
    assert set(compiled.params.values()) >= {first, second, "analysis.python"}


def test_global_switch_empties_worker_rollout(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid.uuid4()
    monkeypatch.setattr(settings.omniagent, "execution_tenant_allowlist", str(tenant_id))
    monkeypatch.setattr(settings.omniagent, "execution_enabled", False)
    assert enabled_execution_tenants() == frozenset()

    monkeypatch.setattr(settings.omniagent, "execution_enabled", True)
    assert enabled_execution_tenants() == frozenset({tenant_id})
