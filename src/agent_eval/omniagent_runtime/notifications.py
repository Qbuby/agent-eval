from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_eval.config import settings
from agent_eval.db import async_session_factory
from agent_eval.db_models.tables import (
    OmniAgentNotificationRow,
    OmniAgentOutboxRow,
    UserRow,
)

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 10


def notification_dict(row: OmniAgentNotificationRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "link": row.link,
        "read_at": row.read_at,
        "created_at": row.created_at,
    }


async def create_notification(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    event_id: int | None,
    kind: str,
    title: str,
    body: str,
    link: str | None = None,
    deliver_feishu: bool = True,
) -> OmniAgentNotificationRow | None:
    """Create one in-app notification and derive any Feishu destination server-side."""
    if user_id is None:
        return None
    if event_id is not None:
        existing = (
            await db.execute(
                select(OmniAgentNotificationRow).where(
                    OmniAgentNotificationRow.tenant_id == tenant_id,
                    OmniAgentNotificationRow.user_id == user_id,
                    OmniAgentNotificationRow.event_id == event_id,
                    OmniAgentNotificationRow.kind == kind,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    row = OmniAgentNotificationRow(
        tenant_id=tenant_id,
        user_id=user_id,
        event_id=event_id,
        kind=kind[:48],
        title=title[:256],
        body=body,
        link=link[:1024] if link else None,
    )
    db.add(row)
    await db.flush()

    if deliver_feishu:
        open_id = (
            await db.execute(
                select(UserRow.feishu_open_id).where(
                    UserRow.id == user_id,
                    UserRow.tenant_id == tenant_id,
                    UserRow.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if open_id:
            db.add(
                OmniAgentOutboxRow(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    notification_id=row.id,
                    channel="feishu",
                    destination=open_id,
                    payload={"title": row.title, "body": row.body, "link": row.link},
                    status="pending",
                    attempts=0,
                    next_attempt_at=datetime.now(timezone.utc),
                )
            )
    return row


class OmniAgentOutboxDispatcher:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if not settings.omniagent.product_plane_enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="omniagent-outbox-dispatcher")

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
                logger.exception("OmniAgent outbox dispatch iteration failed")
                handled = False
            if not handled:
                await asyncio.sleep(1)

    async def run_once(self) -> bool:
        now = datetime.now(timezone.utc)
        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(OmniAgentOutboxRow)
                    .where(
                        OmniAgentOutboxRow.status.in_(["pending", "retry", "sending"]),
                        OmniAgentOutboxRow.next_attempt_at <= now,
                        OmniAgentOutboxRow.attempts < MAX_DELIVERY_ATTEMPTS,
                    )
                    .order_by(OmniAgentOutboxRow.next_attempt_at, OmniAgentOutboxRow.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            row.status = "sending"
            row.attempts += 1
            row.next_attempt_at = now + timedelta(minutes=5)
            outbox_id = row.id
            channel = row.channel
            destination = row.destination
            payload = dict(row.payload or {})
            await db.commit()

        error: str | None = None
        try:
            if channel != "feishu":
                raise ValueError(f"unsupported notification channel: {channel}")
            if not settings.feishu.configured:
                raise RuntimeError("Feishu delivery is not configured")
            from agent_eval.feishu.service import get_service

            text = payload.get("body") or payload.get("title") or "OmniAgent notification"
            if payload.get("link"):
                text = f"{text}\n{payload['link']}"
            await get_service().send_card(destination, text)
        except Exception as exc:  # noqa: BLE001 - durable retry records the failure
            error = f"{type(exc).__name__}: {exc}"[:2000]

        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(OmniAgentOutboxRow)
                    .where(OmniAgentOutboxRow.id == outbox_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                return True
            if error is None:
                row.status = "delivered"
                row.delivered_at = datetime.now(timezone.utc)
                row.last_error = None
            else:
                row.last_error = error
                if row.attempts >= MAX_DELIVERY_ATTEMPTS:
                    row.status = "failed"
                else:
                    row.status = "retry"
                    delay = min(3600, 2 ** row.attempts)
                    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            await db.commit()
        return True
