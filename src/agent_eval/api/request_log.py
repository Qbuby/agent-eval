"""Process-local ring buffer of recent HTTP requests.

Backs ``GET /api/admin/request-log`` so an operator can see which calls just
hit the API without grepping through container stdout. The buffer is in
memory only — restart wipes it. That is intentional: this is a debug aid,
not an audit log.

Design:
    - ``collections.deque(maxlen=...)`` is bounded and thread-safe for append,
      but a snapshot under iteration needs an explicit lock to avoid mutation
      during read.
    - ``capture(...)`` is called once per finished request from the
      ``RequestContextMiddleware``. Body preview is only attached on 4xx/5xx
      and only when ``LOG_REQUEST_BODY`` is on, mirroring the existing
      sensitivity stance in middleware.py.
    - ``snapshot(...)`` returns the most-recent-first slice with optional
      ``status_min`` / ``path_prefix`` filters and ``limit`` cap.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable

# How many request entries to keep. ~512 covers a few minutes of normal
# traffic and stays well under 1 MB even with body previews attached.
_DEFAULT_CAPACITY = 512

# Bytes from cached_body to keep in the ring entry. Smaller than the
# middleware's logging cap because the buffer holds many entries; we only
# need enough to recognize the failing payload.
_BODY_PREVIEW_LIMIT = 1024


@dataclass(slots=True)
class RequestLogEntry:
    """One captured HTTP request. Ordered roughly by relevance for the UI."""

    timestamp: str            # ISO-8601 UTC, e.g. "2026-05-25T10:30:00.123+00:00"
    method: str
    path: str
    status: int
    latency_ms: float
    request_id: str
    query: str = ""
    client: str = "-"
    # Set when the request raised before producing a response. status will
    # be 500 in that case to keep the row sortable alongside real 5xx.
    error: str | None = None
    # Decoded UTF-8 (with replacement) preview of the request body. Only
    # populated for 4xx/5xx when LOG_REQUEST_BODY=true. ``body_truncated``
    # tells the UI whether the original payload was longer than the preview.
    body_preview: str | None = None
    body_truncated: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class _RequestLogBuffer:
    """Thread-safe ring buffer. Single module-level instance below."""

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        self._buf: deque[RequestLogEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._capacity = capacity

    @property
    def capacity(self) -> int:
        return self._capacity

    def capture(self, entry: RequestLogEntry) -> None:
        # ``deque.append`` is atomic under the GIL, but we still take the
        # lock so that a concurrent ``snapshot`` sees a consistent view.
        with self._lock:
            self._buf.append(entry)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def snapshot(
        self,
        *,
        limit: int = 100,
        status_min: int | None = None,
        path_prefix: str | None = None,
    ) -> list[RequestLogEntry]:
        """Return at most ``limit`` entries, newest first, optionally filtered.

        ``status_min`` keeps entries whose status >= the threshold (e.g.
        ``400`` for "show me errors"). ``path_prefix`` is a simple
        ``startswith`` match.
        """
        with self._lock:
            items: Iterable[RequestLogEntry] = reversed(self._buf)
            out: list[RequestLogEntry] = []
            for e in items:
                if status_min is not None and e.status < status_min:
                    continue
                if path_prefix is not None and not e.path.startswith(path_prefix):
                    continue
                out.append(e)
                if len(out) >= limit:
                    break
            return out


# Module-level singleton — the middleware writes here, the admin router reads.
buffer = _RequestLogBuffer()


def capture(
    *,
    method: str,
    path: str,
    status: int,
    latency_ms: float,
    request_id: str,
    query: str = "",
    client: str = "-",
    error: str | None = None,
    body: bytes | None = None,
) -> None:
    """Convenience wrapper used by the middleware. Builds the entry and
    pushes it into the singleton buffer in one call."""
    body_preview: str | None = None
    body_truncated = False
    if body and status >= 400:
        preview_bytes = body[:_BODY_PREVIEW_LIMIT]
        try:
            body_preview = preview_bytes.decode("utf-8", errors="replace")
        except Exception:
            body_preview = repr(preview_bytes)
        body_truncated = len(body) > _BODY_PREVIEW_LIMIT

    entry = RequestLogEntry(
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        method=method,
        path=path,
        status=status,
        latency_ms=round(latency_ms, 1),
        request_id=request_id,
        query=query,
        client=client,
        error=error,
        body_preview=body_preview,
        body_truncated=body_truncated,
    )
    buffer.capture(entry)


def snapshot(
    *,
    limit: int = 100,
    status_min: int | None = None,
    path_prefix: str | None = None,
) -> list[RequestLogEntry]:
    return buffer.snapshot(limit=limit, status_min=status_min, path_prefix=path_prefix)
