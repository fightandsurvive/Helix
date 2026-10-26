#!/usr/bin/env python3
"""
Migration runner.

Behavior:
- Creates `public._migrations` (filename, sha256, applied_at) on first run.
- Iterates *.sql in lexicographic order — numeric prefixes enforce order.
- Skips already-applied files, but refuses to skip if the file content
  changed (sha256 mismatch) — this prevents silent schema drift where a
  developer edits an applied migration to fix a bug and other developers'
  databases don't reflect the change.
- Each migration runs in its own transaction. If migration 07 fails,
  migrations 01-06 remain applied. Re-running resumes from 07.

Enterprise note: NEVER edit an applied migration. Add a new one.
Usage:
    python migrations/run_migrations.py
    # OR
    python -m migrations.run_migrations

Both invocation styles work. The sys.path manipulation below exists because
`python migrations/run_migrations.py` puts `migrations/` on sys.path (not
the repo root), so `from common.db import ...` would fail without it.
Applies *.sql files in lexicographic order, tracking SHA256 of each applied
file in `public._migrations` to prevent silent edits to already-applied
migrations.

Robustness notes:
- BOMs (UTF-8 byte-order marks) are stripped on read. Windows editors
  (Notepad, VS Code "UTF-8 with BOM" mode, PowerShell Out-File) frequently
  save files with BOMs, and PostgreSQL's SQL parser rejects a leading BOM.
- Connection pool is explicitly closed on exit to avoid the 5-second
  hang per worker that psycopg_pool produces on ungraceful shutdown.

Usage:
    python migrations/run_migrations.py
    # OR
    python -m migrations.run_migrations
"""
# ruff: noqa: E402, I001
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common.db import close_pool, connection
from config.logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger("migrations")


def _read_sql(path: Path) -> str:
    """
    Read a .sql file, stripping any UTF-8 BOM.

    Uses utf-8-sig which transparently strips the BOM if present. If the
    file has no BOM, this behaves identically to plain utf-8.
    """
    return path.read_text(encoding="utf-8-sig")


def _hash_file(path: Path) -> str:
    """
    SHA256 of file contents, with BOM stripped for consistency.

    Important: hash the *content* the runner will execute, not the raw
    bytes on disk. Otherwise applying the same migration on a BOM'd file
    vs a BOM-less file would produce different hashes and the runner
    would think the file had been modified.
    """
    return hashlib.sha256(_read_sql(path).encode("utf-8")).hexdigest()


def _ensure_tracking_table(conn) -> None:
    """Idempotent — creates the migration tracking table if missing."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public._migrations (
                filename   TEXT PRIMARY KEY,
                sha256     TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
    conn.commit()


def _applied_sha(conn, filename: str) -> str | None:
    """Returns the recorded sha256 for a filename, or None if not applied."""
    with conn.cursor() as cur:
        cur.execute("SELECT sha256 FROM public._migrations WHERE filename=%s", (filename,))
        row = cur.fetchone()
    return row[0] if row else None


def main() -> int:
    migration_dir = Path(__file__).parent
    files = sorted(migration_dir.glob("*.sql"))
    if not files:
        log.error("no_migration_files", dir=str(migration_dir))
        return 1

    log.info("migrations_start", count=len(files))
    applied_count = 0

    try:
        with connection() as conn:
            _ensure_tracking_table(conn)

            for f in files:
                content = _read_sql(f)
                sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
                recorded_sha = _applied_sha(conn, f.name)

                if recorded_sha is not None:
                    if recorded_sha != sha:
                        log.error(
                            "migration_modified",
                            file=f.name,
                            message=(
                                "File changed after being applied. Revert or add a "
                                "new migration instead of editing an applied one."
                            ),
                        )
                        return 2
                    log.info("migration_skip", file=f.name)
                    continue

                log.info("migration_apply", file=f.name, bytes=len(content))

                try:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(content)
                            cur.execute(
                                "INSERT INTO public._migrations (filename, sha256) VALUES (%s, %s)",
                                (f.name, sha),
                            )
                    applied_count += 1
                except Exception as e:
                    log.exception("migration_failed", file=f.name, error=str(e))
                    return 3

            log.info("migrations_done", applied=applied_count, total=len(files))
    finally:
        # Explicit close prevents the 5-second hang per worker that
        # psycopg_pool shows on ungraceful termination.
        close_pool()

    return 0


if __name__ == "__main__":
    sys.exit(main())
