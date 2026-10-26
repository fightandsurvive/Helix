"""
Structured logging via structlog.

Every module calls `get_logger(__name__)`. Logs are JSON in production
(parseable by every log aggregator), human-readable in dev.

Why structured: when a training run fails at step 47,000 across 32
workers, you filter by correlation ID, not grep. Structured logs make
that trivial — `generation=42 AND stage=selfplay AND level=error` is one
query.

Why JSON-in-prod / text-in-dev: aggregators (Loki, CloudWatch, Datadog)
parse JSON natively; humans read text natively. Same code path, one
renderer switch.
"""
from __future__ import annotations

import logging
import sys

import structlog

from config.settings import settings


def configure_logging() -> None:
    """
    Called once at process startup (entry scripts, UCI engine, service
    daemons). Idempotent — calling twice doesn't break anything, but the
    second call reconfigures structlog with the same settings.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
    ]

    if settings.log_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging through structlog so library warnings appear
    # in the same stream with the same format.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level),
    )


def get_logger(name: str):
    """Convenience accessor. Every module uses `get_logger(__name__)`."""
    return structlog.get_logger(name)
