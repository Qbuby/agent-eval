"""Centralized logging configuration.

Single entry point ``setup_logging()`` configures root + third-party + agent_eval
loggers via ``logging.config.dictConfig``. Avoids the runtime ``basicConfig(force=True)``
anti-pattern previously scattered across ``api/app.py`` and ``cli.py``.

Public surface:
    setup_logging(level: str, fmt: str) -> None
    get_request_id() -> str | None
    request_id_var: ContextVar[str]              (for middleware to set)

Design notes:
    - request_id is carried via contextvars so any logger call inside an async
      request gets it automatically (no need to thread it through every call).
    - JSON formatter is hand-rolled (~30 lines) to avoid pulling in structlog.
    - Third-party loggers (httpx/httpcore/langsmith/langgraph/sqlalchemy.engine)
      are pinned to WARNING regardless of root level — INFO from these floods.
"""

from __future__ import annotations

import json
import logging
import logging.config
from contextvars import ContextVar
from datetime import datetime, timezone

# Public: middleware writes here, get_request_id() / RequestIdFilter read here.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str | None:
    """Return the current request_id, or None if not in a request context."""
    rid = request_id_var.get()
    return rid or None


class RequestIdFilter(logging.Filter):
    """Inject ``record.request_id`` so format strings can reference it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """Single-line JSON per record. Keys: ts, level, logger, msg, request_id, [exc]."""

    # logging.LogRecord built-in attributes — anything else under record.__dict__
    # is "extra" and we forward it under an "extra" key.
    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "request_id",
        "taskName",  # added in Python 3.12
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        extras = {
            k: v for k, v in record.__dict__.items() if k not in self._RESERVED
        }
        if extras:
            payload["extra"] = extras
        return json.dumps(payload, ensure_ascii=False, default=str)


_PLAIN_FMT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"

# Third-party loggers we always pin to WARNING — they emit per-request INFO that
# floods our own diagnostics. Bump individually if you ever need to debug them.
_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "httpcore.http11",
    "httpcore.connection",
    "langsmith",
    "langsmith.client",
    "langgraph",
    "sqlalchemy.engine",
    "asyncio",
)


def setup_logging(level: str = "INFO", fmt: str = "plain") -> None:
    """Configure logging once. Idempotent — safe to call multiple times.

    level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' (case-insensitive)
    fmt:   'plain' | 'json'
    """
    level_str = (level or "INFO").upper()
    fmt_choice = (fmt or "plain").lower()

    formatter_cfg: dict
    if fmt_choice == "json":
        formatter_cfg = {"()": "agent_eval.logging_config.JsonFormatter"}
    else:
        formatter_cfg = {"format": _PLAIN_FMT}

    config: dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {"()": "agent_eval.logging_config.RequestIdFilter"},
        },
        "formatters": {"default": formatter_cfg},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": level_str,
                "formatter": "default",
                "filters": ["request_id"],
            },
        },
        "root": {
            "level": level_str,
            "handlers": ["console"],
        },
        "loggers": {
            "agent_eval": {"level": level_str, "propagate": True},
            # uvicorn ships its own loggers — pull them onto our handler so the
            # JSON/plain format applies uniformly. Keep their level at root.
            "uvicorn": {"level": level_str, "propagate": True, "handlers": []},
            "uvicorn.error": {"level": level_str, "propagate": True, "handlers": []},
            "uvicorn.access": {"level": level_str, "propagate": True, "handlers": []},
            **{name: {"level": "WARNING", "propagate": True} for name in _NOISY_LOGGERS},
        },
    }

    logging.config.dictConfig(config)
