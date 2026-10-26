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
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from common.db import connection
from config.logging_setup import configure_logging, get_logger

configure_logging()
log = get_logger("migrations")


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


def _hash_file(path: Path) -> str:
    """SHA256 of file contents, used to detect post-hoc edits."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    migration_dir = Path(__file__).parent
    files = sorted(migration_dir.glob("*.sql"))
    if not files:
        log.error("no_migration_files", dir=str(migration_dir))
        return 1

    log.info("migrations_start", count=len(files))

    with connection() as conn:
        _ensure_tracking_table(conn)
        applied_count = 0

        for f in files:
            sha = _hash_file(f)
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

            log.info("migration_apply", file=f.name, bytes=f.stat().st_size)
            content = f.read_text(encoding="utf-8")

            try:
                # Each migration in its own transaction — partial failure
                # leaves prior migrations applied cleanly.
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
