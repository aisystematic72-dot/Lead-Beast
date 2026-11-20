"""structlog JSON logging with secret redaction and a run_id per pipeline run."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

SECRET_KEYS = {"password", "secret", "token", "api_key", "authorization", "secret_key", "dsn"}


def _redact(_: Any, __: str, event: dict[str, Any]) -> dict[str, Any]:
    for k in list(event):
        if any(s in k.lower() for s in SECRET_KEYS):
            event[k] = "***"
    return event


def configure(json: bool = True, level: int = logging.INFO) -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )


def get_logger(name: str = "leadbeast") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_run(run_id: str, **kw: Any) -> None:
    structlog.contextvars.bind_contextvars(run_id=run_id, **kw)
