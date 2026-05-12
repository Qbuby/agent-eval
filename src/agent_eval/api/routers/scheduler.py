from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent_eval.scheduler.service import SchedulerService

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])

_scheduler: SchedulerService | None = None


def set_scheduler(service: SchedulerService) -> None:
    global _scheduler
    _scheduler = service


def _get_scheduler() -> SchedulerService:
    if _scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not initialized")
    return _scheduler


class AddWatchRequest(BaseModel):
    project_name: str


@router.get("/status")
async def get_status():
    svc = _get_scheduler()
    return await svc.get_status()


@router.post("/watch")
async def add_watch(req: AddWatchRequest):
    svc = _get_scheduler()
    await svc.add_watch(req.project_name)
    return {"message": f"Watch added for {req.project_name}"}


@router.delete("/watch/{project_name}")
async def remove_watch(project_name: str):
    svc = _get_scheduler()
    await svc.remove_watch(project_name)
    return {"message": f"Watch removed for {project_name}"}


@router.post("/watch/{project_name}/pause")
async def pause_watch(project_name: str):
    svc = _get_scheduler()
    await svc.pause_watch(project_name)
    return {"message": f"Watch paused for {project_name}"}


@router.post("/watch/{project_name}/resume")
async def resume_watch(project_name: str):
    svc = _get_scheduler()
    await svc.resume_watch(project_name)
    return {"message": f"Watch resumed for {project_name}"}


@router.post("/trigger/{project_name}")
async def trigger_poll(project_name: str):
    svc = _get_scheduler()
    runs = await svc.trigger_poll(project_name)
    return {"project_name": project_name, "new_runs": runs, "count": len(runs)}
