from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import shutil
import socket
import uuid
from pathlib import Path

from sqlalchemy import select

from agent_eval.config import settings
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import OmniAgentArtifactRow, OmniAgentJobRow
from agent_eval.omniagent_runtime.action_executor import (
    execute_action_job,
    reconcile_terminal_action_jobs,
)
from agent_eval.omniagent_runtime.artifacts import (
    ingest_artifact,
    store_from_settings,
)
from agent_eval.omniagent_runtime.jobs import (
    claim_next_job,
    finish_job,
    heartbeat_job,
    mark_job_running,
    recover_expired_leases,
)
from agent_eval.omniagent_runtime.quota import add_quota_entry
from agent_eval.omniagent_runtime.runner import (
    KubernetesAnalysisRunner,
    RunnerError,
    RunnerInfrastructureError,
    runner_from_settings,
)
from agent_eval.omniagent_runtime.security import enabled_execution_tenants

logger = logging.getLogger(__name__)


class _PathReader:
    def __init__(self, path: Path) -> None:
        self._handle = path.open("rb")

    async def read(self, size: int = -1) -> bytes:
        return self._handle.read(size)

    def close(self) -> None:
        self._handle.close()


class LeaseLost(RuntimeError):
    pass


class OmniAgentExecutionWorker:
    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if not settings.omniagent.worker_enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="omniagent-execution-worker")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                handled = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("OmniAgent execution worker iteration failed")
                handled = False
            if not handled:
                await asyncio.sleep(max(0.1, settings.omniagent.worker_poll_seconds))

    async def run_once(self) -> bool:
        async with async_session_factory() as db:
            await recover_expired_leases(db)
            await reconcile_terminal_action_jobs(db)
            claimed = await claim_next_job(
                db,
                worker_id=self.worker_id,
                lease_seconds=60,
                kinds=frozenset({"analysis.python", "action.execute"}),
                tenant_ids=enabled_execution_tenants(),
            )
            await db.commit()
        if claimed is None:
            return False
        job, _ = claimed
        if job.kind == "action.execute":
            await self._execute_action(job)
        else:
            await self._execute_analysis(job)
        return True

    async def _execute_action(self, job: OmniAgentJobRow) -> None:
        async with async_session_factory() as db:
            try:
                if not await mark_job_running(
                    db,
                    job_id=job.id,
                    worker_id=self.worker_id,
                    runtime_ref="fixed-action",
                ):
                    await db.rollback()
                    return
                changed = await execute_action_job(db, job=job, worker_id=self.worker_id)
                if not changed:
                    await db.rollback()
                    return
                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.exception("fixed action execution failed for job %s", job.id)
                await self._finish(
                    job,
                    status="failed",
                    error_code="ACTION_INFRASTRUCTURE_ERROR",
                    error_message=str(exc),
                    infrastructure_failure=True,
                )

    async def _heartbeat(self, job_id: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(20)
            async with async_session_factory() as db:
                alive = await heartbeat_job(
                    db, job_id=job_id, worker_id=self.worker_id, lease_seconds=60
                )
                await db.commit()
            if not alive:
                raise LeaseLost("analysis worker lost its job lease")

    async def _execute_analysis(self, job: OmniAgentJobRow) -> None:
        root = Path(settings.omniagent.artifact_root).resolve() / ".jobs" / str(job.id)
        heartbeat_task: asyncio.Task | None = None
        try:
            runner = runner_from_settings()
            execution_id = f"{job.id.hex}-a{job.attempt_count}"
            runtime_ref = (
                runner.runtime_ref(execution_id)
                if isinstance(runner, KubernetesAnalysisRunner)
                else str(root)
            )
            async with async_session_factory() as db:
                if not await mark_job_running(
                    db, job_id=job.id, worker_id=self.worker_id, runtime_ref=runtime_ref
                ):
                    await db.rollback()
                    return
                await db.commit()
            await self._materialize_inputs(job, root)
            run_task = asyncio.create_task(
                runner.run(
                    code=job.spec["code"],
                    workspace=root,
                    execution_id=execution_id,
                )
            )
            heartbeat_task = asyncio.create_task(self._heartbeat(job.id))
            done, _ = await asyncio.wait(
                {run_task, heartbeat_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat_task in done:
                try:
                    await heartbeat_task
                finally:
                    run_task.cancel()
                    await asyncio.gather(run_task, return_exceptions=True)
            result = await run_task
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            heartbeat_task = None
            if result.exit_code != 0:
                await self._finish(
                    job,
                    status="failed",
                    error_code="ANALYSIS_CODE_FAILED",
                    error_message=result.logs[-4000:],
                    usage={"duration_seconds": result.duration_seconds},
                )
                return
            if result.code_sha256 != job.spec.get("code_sha256"):
                raise ValueError("analysis code digest does not match the submitted job")
            await self._publish_outputs_and_finish(
                job,
                result={
                    "code_sha256": result.code_sha256,
                    "python_version": result.python_version,
                    "attempt": job.attempt_count,
                },
                usage={"duration_seconds": result.duration_seconds},
                output_files=result.output_files,
            )
        except LeaseLost:
            logger.warning("worker lost lease for analysis job %s", job.id)
        except RunnerInfrastructureError as exc:
            await self._finish(
                job,
                status="failed",
                error_code=type(exc).__name__.upper(),
                error_message=str(exc),
                infrastructure_failure=True,
            )
        except RunnerError as exc:
            await self._finish(
                job,
                status="failed",
                error_code=type(exc).__name__.upper(),
                error_message=str(exc),
            )
        except Exception as exc:
            await self._finish(
                job,
                status="failed",
                error_code="ANALYSIS_INFRASTRUCTURE_ERROR",
                error_message=str(exc),
                infrastructure_failure=True,
            )
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            shutil.rmtree(root, ignore_errors=True)

    async def _materialize_inputs(self, job: OmniAgentJobRow, root: Path) -> None:
        artifact_ids = [uuid.UUID(value) for value in job.spec.get("artifact_ids", [])]
        if not artifact_ids:
            return
        async with async_session_factory() as db:
            rows = list(
                (
                    await db.execute(
                        select(OmniAgentArtifactRow).where(
                            OmniAgentArtifactRow.id.in_(artifact_ids),
                            OmniAgentArtifactRow.tenant_id == job.tenant_id,
                            OmniAgentArtifactRow.owner_id == job.requested_by,
                            OmniAgentArtifactRow.state == "available",
                        )
                    )
                ).scalars()
            )
        if {row.id for row in rows} != set(artifact_ids):
            raise ValueError("analysis input artifacts changed or became unavailable")
        inputs = root / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        store = store_from_settings()
        for row in rows:
            source = store.path_for_read(row.object_key)
            target = inputs / f"{row.id}{row.extension}"
            try:
                if source.is_symlink():
                    raise ValueError("analysis input cannot be a symbolic link")
                shutil.copyfile(source, target, follow_symlinks=False)
            finally:
                store.cleanup_read(source)

    async def _publish_outputs_and_finish(
        self,
        job: OmniAgentJobRow,
        *,
        result: dict,
        usage: dict,
        output_files: tuple[Path, ...],
    ) -> bool:
        artifact_ids: list[str] = []
        object_keys: list[str] = []
        store = store_from_settings()
        async with async_session_factory() as db:
            try:
                for path in output_files:
                    reader = _PathReader(path)
                    try:
                        artifact = await ingest_artifact(
                            db,
                            tenant_id=job.tenant_id,
                            owner_id=job.requested_by,
                            filename=path.name,
                            declared_mime=(
                                mimetypes.guess_type(path.name)[0]
                                or "application/octet-stream"
                            ),
                            source=reader,
                            session_id=job.session_id,
                            store=store,
                        )
                    finally:
                        reader.close()
                    object_keys.append(artifact.object_key)
                    if artifact.state != "available":
                        raise ValueError(f"analysis output was quarantined: {path.name}")
                    artifact.job_id = job.id
                    artifact_ids.append(str(artifact.id))
                result["output_artifact_ids"] = artifact_ids
                changed = await finish_job(
                    db,
                    job=job,
                    worker_id=self.worker_id,
                    status="succeeded",
                    result=result,
                    usage=usage,
                )
                if not changed:
                    raise LeaseLost("analysis worker lost its lease before publication")
                self._add_settlement(db, job, status="succeeded", usage=usage)
                await db.commit()
                return True
            except Exception:
                await db.rollback()
                for object_key in object_keys:
                    store.delete(object_key)
                raise

    async def _finish(
        self,
        job: OmniAgentJobRow,
        *,
        status: str,
        result: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        usage: dict | None = None,
        infrastructure_failure: bool = False,
    ) -> None:
        async with async_session_factory() as db:
            changed = await finish_job(
                db,
                job=job,
                worker_id=self.worker_id,
                status=status,
                result=result,
                error_code=error_code,
                error_message=error_message,
                usage=usage,
                infrastructure_failure=infrastructure_failure,
            )
            if changed:
                current = (
                    await db.execute(
                        select(OmniAgentJobRow.status).where(OmniAgentJobRow.id == job.id)
                    )
                ).scalar_one()
                if current in {"succeeded", "failed", "cancelled", "expired"}:
                    self._add_settlement(db, job, status=current, usage=usage or {})
                    if job.kind == "action.execute":
                        await reconcile_terminal_action_jobs(db)
            await db.commit()

    @staticmethod
    def _add_settlement(db, job: OmniAgentJobRow, *, status: str, usage: dict) -> None:
        add_quota_entry(
            db,
            tenant_id=job.tenant_id,
            user_id=job.requested_by,
            metric="analysis_jobs",
            amount=0,
            entry_type="settlement",
            resource_type="job",
            resource_id=str(job.id),
            details={"status": status, "usage": usage},
        )
        duration = float(usage.get("duration_seconds") or 0)
        if duration > 0:
            add_quota_entry(
                db,
                tenant_id=job.tenant_id,
                user_id=job.requested_by,
                metric="sandbox_cpu_seconds",
                amount=duration,
                entry_type="usage",
                resource_type="job",
                resource_id=str(job.id),
            )
