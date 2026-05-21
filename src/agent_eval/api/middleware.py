"""Request-scoped logging middleware.

Responsibilities:
1. Assign a request_id (from header X-Request-ID, or new uuid4 hex[:16])
   and store it in:
     - request.state.request_id  (for handlers / exception handler)
     - request_id_var            (contextvar — picked up by RequestIdFilter)
2. Emit one INFO log on request start (method, path, query) and one on finish
   (status, latency_ms). 5xx escalates to ERROR.
3. Echo the request_id back as response header X-Request-ID so frontends and
   curl users can quote it when reporting issues.
4. Optionally log the request body on 4xx/5xx when LOG_REQUEST_BODY=true.
   Default off — request bodies often contain LLM API keys, user passwords,
   and LangSmith tokens.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from agent_eval.logging_config import request_id_var

logger = logging.getLogger(__name__)

_BODY_LOG_LIMIT = 4096  # bytes; redact-large to prevent log explosion


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, log_request_body: bool = False) -> None:
        super().__init__(app)
        self._log_request_body = log_request_body

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        token = request_id_var.set(rid)

        start = time.perf_counter()
        method = request.method
        path = request.url.path
        query = request.url.query
        client = request.client.host if request.client else "-"

        logger.info(
            "request started method=%s path=%s query=%s client=%s",
            method, path, query or "-", client,
        )

        # Snapshot the body up-front only when we'll need it. Reading body
        # consumes the stream, so we must rebuild request._receive afterward.
        cached_body: bytes | None = None
        if self._log_request_body:
            try:
                cached_body = await request.body()

                async def _replay():  # type: ignore[no-untyped-def]
                    return {"type": "http.request", "body": cached_body, "more_body": False}

                request._receive = _replay  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover — body read should not break the request
                cached_body = None

        try:
            response: Response = await call_next(request)
        except Exception:
            # The global exception handler logs the traceback. We only need to
            # finalize the access log line here.
            latency_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "request failed method=%s path=%s latency_ms=%.1f",
                method, path, latency_ms,
            )
            request_id_var.reset(token)
            raise

        latency_ms = (time.perf_counter() - start) * 1000
        status = response.status_code
        log_fn = logger.error if status >= 500 else (logger.warning if status >= 400 else logger.info)
        log_fn(
            "request finished method=%s path=%s status=%d latency_ms=%.1f",
            method, path, status, latency_ms,
        )

        if cached_body and status >= 400:
            preview = cached_body[:_BODY_LOG_LIMIT]
            try:
                preview_str = preview.decode("utf-8", errors="replace")
            except Exception:
                preview_str = repr(preview)
            truncated = " (truncated)" if len(cached_body) > _BODY_LOG_LIMIT else ""
            logger.warning(
                "request body on %d: method=%s path=%s body=%s%s",
                status, method, path, preview_str, truncated,
            )

        response.headers["X-Request-ID"] = rid
        request_id_var.reset(token)
        return response
