"""
PostgreSQL access layer — psycopg3 with connection pooling.

Why a module: every caller writes to the same warehouse. Centralizing
connection pooling, transaction management, and query logging prevents
connection leaks — a classic self-play failure mode at 32 workers.

Why psycopg3 over psycopg2:
- Native async (used by the orchestrator's concurrent stages)
- Connection pool is a first-class library (psycopg_pool)
- Prepared statement support is server-side and automatic
- FEN MD5 lookups return UUID directly without parsing

Two public contexts:
    `transaction()` — write, commits on clean exit, rolls back on error
    `read_cursor()` — read, discards any accidental writes

Both are context managers so `with transaction() as cur:` works naturally.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection, Cursor
from psycopg_pool import ConnectionPool

from config.logging_setup import get_logger
from config.settings import settings

log = get_logger(__name__)

# Module-level pool — lazily initialized on first access. This matters
# because `import common.db` happens at process start when we may not
# yet want to open connections (e.g., during a dry-run that exits early).
_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """
    Lazy-initialized connection pool. Safe to call from anywhere.

    psycopg_pool.ConnectionPool opens `min_size` connections eagerly on
    construction. The pool is thread-safe; each thread that enters
    `connection()` gets its own connection, returned on exit.
    """
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=str(settings.database_url),
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            timeout=30,
            # Health check on checkout — catches stale connections after
            # DB restart without needing an explicit reconnect loop.
            check=ConnectionPool.check_connection,
        )
        log.info(
            "db_pool_created",
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
        )
    return _pool


@contextmanager
def transaction() -> Iterator[Cursor]:
    """
    Yields a cursor inside a transaction. Commits on clean exit, rolls
    back on exception.

    Usage:
        with transaction() as cur:
            cur.execute("INSERT ...", (a, b, c))

    The `with get_pool().connection() as conn:` context in psycopg3 commits
    on clean exit and rolls back on exception automatically — we don't
    call commit() explicitly.
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            yield cur


@contextmanager
def read_cursor() -> Iterator[Cursor]:
    """
    Yields a read-only cursor. Rolls back at exit to discard any
    accidental writes (defense in depth — reads shouldn't write, but if
    a caller passes an INSERT to read_cursor, the write won't stick).

    Usage:
        with read_cursor() as cur:
            cur.execute("SELECT ...")
            rows = cur.fetchall()
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            try:
                yield cur
            finally:
                conn.rollback()


@contextmanager
def connection() -> Iterator[Connection]:
    """
    Raw connection access for cases that need `conn.transaction()` blocks
    or multi-cursor workflows. Most code should use transaction() or
    read_cursor() instead.
    """
    with get_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    """
    Close all pool connections. Called on graceful shutdown so Postgres
    doesn't have to reap half-open TCP connections on client exit.
    """
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        log.info("db_pool_closed")
