"""Admin-only operational endpoints.

Houses small debug aids that don't fit in the domain routers. First inhabitant:
``GET /api/admin/request-log`` — a snapshot of the in-memory ring buffer of
recent HTTP requests, gated by ``require_admin``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from agent_eval.api import request_log
from agent_eval.auth.dependencies import require_admin
from agent_eval.db_models.tables import UserRow

router = APIRouter(prefix="/api/admin", tags=["admin"])


class RequestLogEntryResponse(BaseModel):
    timestamp: str
    method: str
    path: str
    status: int
    latency_ms: float
    request_id: str
    query: str = ""
    client: str = "-"
    error: str | None = None
    body_preview: str | None = None
    body_truncated: bool = False


class RequestLogResponse(BaseModel):
    capacity: int
    returned: int
    entries: list[RequestLogEntryResponse]


@router.get("/request-log", response_model=RequestLogResponse)
async def get_request_log(
    limit: int = Query(100, ge=1, le=500),
    status_min: int | None = Query(None, ge=100, le=599),
    path_prefix: str | None = Query(None),
    _admin: UserRow = Depends(require_admin),
):
    """Return the most recent HTTP requests captured by the middleware.

    Newest first. ``status_min`` (e.g. ``400``) limits to errors. ``path_prefix``
    is a literal ``startswith`` match on the request path.
    """
    entries = request_log.snapshot(
        limit=limit, status_min=status_min, path_prefix=path_prefix
    )
    return RequestLogResponse(
        capacity=request_log.buffer.capacity,
        returned=len(entries),
        entries=[RequestLogEntryResponse(**e.to_dict()) for e in entries],
    )
