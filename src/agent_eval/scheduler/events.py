from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agent_eval.data.trace_extractor import RunSummary

logger = logging.getLogger(__name__)


@dataclass
class NewRunsEvent:
    project_name: str
    runs: list[RunSummary]


EventHandler = Callable[[NewRunsEvent], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        self._handlers = [h for h in self._handlers if h is not handler]

    async def publish(self, event: NewRunsEvent) -> None:
        errors: list[Exception] = []
        for handler in self._handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(
                    "Event handler %s failed for project %s: %s",
                    getattr(handler, "__name__", repr(handler)),
                    event.project_name,
                    e,
                    exc_info=True,
                )
                errors.append(e)

        if errors:
            raise EventHandlerError(
                f"{len(errors)} handler(s) failed for project {event.project_name}",
                errors=errors,
            )


class EventHandlerError(Exception):
    def __init__(self, message: str, errors: list[Exception]):
        super().__init__(message)
        self.errors = errors
