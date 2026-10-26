"""
Retry helper for external I/O (DB, HTTP, subprocess).

Why custom instead of raw tenacity: we want uniform logging and
exponential backoff with jitter as the default. Site code then reads
tersely:

    @retry(stop_after=5)
    def query_db(): ...

Jitter matters when 32 workers all hit the same DB recovery moment —
without jitter they retry in lockstep and re-saturate the DB. tenacity's
wait_exponential_jitter solves this.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from tenacity import (
    retry as _retry,
)
from tenacity import (
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from config.logging_setup import get_logger

log = get_logger(__name__)
F = TypeVar("F", bound=Callable)


def retry(
    stop_after: int = 3,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """
    Decorator: retries the wrapped function on the given exception types
    with exponential backoff and jitter.

    Args:
        stop_after: maximum number of total attempts (including the first)
        exceptions: which exceptions trigger retry — non-matching exceptions
                    propagate immediately, which is usually what you want
                    (e.g., a KeyError probably isn't transient)

    Backoff schedule: 1s, 2s, 4s, 8s, ... capped at 30s per wait, ±30%
    jitter. After `stop_after` attempts, the last exception is re-raised.
    """

    def decorator(fn: F) -> F:
        wrapped = _retry(
            stop=stop_after_attempt(stop_after),
            wait=wait_exponential_jitter(initial=1, max=30),
            retry=retry_if_exception_type(exceptions),
            reraise=True,
        )(fn)

        @wraps(fn)
        def inner(*args, **kwargs):
            try:
                return wrapped(*args, **kwargs)
            except Exception as e:
                log.error(
                    "retries_exhausted",
                    fn=fn.__name__,
                    attempts=stop_after,
                    error=str(e),
                )
                raise

        return inner  # type: ignore[return-value]

    return decorator
