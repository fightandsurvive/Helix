#!/usr/bin/env python3
"""
Migration runner.

Applies *.sql files in lexicographic order, tracking SHA256 of each
applied file in `public._migrations` to prevent silent edits to
already-applied migrations.

Modes:
    (default)               Apply any unapplied migrations
    --resync                Update hashes for all files without re-applying.
                            Use only after line-ending/encoding normalization
                            that changed file bytes but not SQL content.
    --resync-file NAME      Update hash for one specific migration only.
    --force-reapply NAME    Delete the record so a specific migration re-runs
                            on next invocation. Only safe if the migration is
                            idempotent (IF NOT EXISTS, OR REPLACE, DROP+CREATE).
"""
# ruff: noqa: E402, I001
from __future__ import annotations

import argparse
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
    Read a .sql file with defensive encoding handling.

    Steps:
    1. Read as utf-8-sig (strips leading BOM transparently)
    2. Replace non-breaking spaces (\\u00A0) with regular spaces
    3. Strip zero-width characters (\\u200b, \\u200c, \\u200d, \\ufeff)
    """
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace("\u00a0", " ")
    text = text.replace("\u200b", "")
    text = text.replace("\u200c", "")
    text = text.replace("\u200d", "")
    text = text.replace("\ufeff", "")
    return text


def _hash_sql(path: Path) -> str:
    """SHA256 of the normalized content the runner will execute."""
    return hashlib.sha256(_read_sql(path).encode("utf-8")).hexdigest()


def _ensure_tracking_table(conn) -> None:
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
    with conn.cursor() as cur:
        cur.execute("SELECT sha256 FROM public._migrations WHERE filename=%s", (filename,))
        row = cur.fetchone()
    return row[0] if row else None


def _resync(conn, files: list[Path], target: str | None) -> int:
    """Update hashes to match current disk content. Does NOT re-apply."""
    updated = 0
    with conn.cursor() as cur:
        for f in files:
            if target and f.name != target:
                continue
            new_hash = _hash_sql(f)
            cur.execute("SELECT sha256 FROM public._migrations WHERE filename=%s", (f.name,))
            row = cur.fetchone()
            if not row:
                if not target:
                    log.debug("resync_skip_unapplied", file=f.name)
                continue
            if row[0] != new_hash:
                cur.execute(
                    "UPDATE public._migrations SET sha256=%s WHERE filename=%s",
                    (new_hash, f.name),
                )
                log.info("resync_updated", file=f.name, old=row[0][:12], new=new_hash[:12])
                updated += 1
            else:
                log.debug("resync_no_change", file=f.name)
    conn.commit()
    return updated


def _force_reapply(conn, filename: str) -> bool:
    """Delete the record so the migration re-runs next time."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM public._migrations WHERE filename=%s", (filename,))
        deleted = cur.rowcount
    conn.commit()
    if deleted > 0:
        log.info("force_reapply", file=filename,
                 message="Record deleted; will re-apply on next run. "
                         "Only safe if migration uses IF NOT EXISTS or DROP+CREATE.")
        return True
    log.warning("force_reapply_no_record", file=filename)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Helix migration runner")
    ap.add_argument("--resync", action="store_true",
                    help="Update hashes for all files without re-applying.")
    ap.add_argument("--resync-file", metavar="NAME",
                    help="Update hash for one specific migration.")
    ap.add_argument("--force-reapply", metavar="NAME",
                    help="Delete the record so this migration re-runs next time.")
    args = ap.parse_args()

    migration_dir = Path(__file__).parent
    files = sorted(migration_dir.glob("*.sql"))
    if not files:
        log.error("no_migration_files", dir=str(migration_dir))
        return 1

    applied_count = 0
    try:
        with connection() as conn:
            _ensure_tracking_table(conn)

            # ── Resync modes ────────────────────────────────────────
            if args.resync or args.resync_file:
                target = args.resync_file
                n = _resync(conn, files, target)
                log.info("resync_done", updated=n,
                         message="Hashes updated without re-applying. If any "
                                 "of these had meaningful SQL changes (not "
                                 "just line endings), the schema may be stale "
                                 "— verify with psql \\d on affected tables.")
                return 0

            if args.force_reapply:
                if not _force_reapply(conn, args.force_reapply):
                    return 1
                log.info("force_reapply_done",
                         message="Run migrations again to re-apply.")
                return 0

            # ── Normal apply mode ───────────────────────────────────
            log.info("migrations_start", count=len(files))
            for f in files:
                content = _read_sql(f)
                sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
                recorded_sha = _applied_sha(conn, f.name)

                if recorded_sha is not None:
                    if recorded_sha != sha:
                        log.error(
                            "migration_modified",
                            file=f.name,
                            hint=f"If this is only line-ending/encoding "
                                 f"normalization, run: python migrations/run_migrations.py "
                                 f"--resync-file {f.name}. If the SQL content "
                                 f"changed meaningfully, review the schema and "
                                 f"use --force-reapply {f.name} only if the "
                                 f"migration is idempotent.",
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
                                "INSERT INTO public._migrations (filename, sha256) "
                                "VALUES (%s, %s)",
                                (f.name, sha),
                            )
                    applied_count += 1
                except Exception as e:
                    log.exception("migration_failed", file=f.name, error=str(e))
                    return 3

            # Verify schemas exist
            expected_schemas = (
                "gm", "selfplay", "eval", "engine_state",
                "ops", "app", "analytics",
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name = ANY(%s) ORDER BY schema_name",
                    (list(expected_schemas),),
                )
                found = [r[0] for r in cur.fetchall()]
            missing = [s for s in expected_schemas if s not in found]
            if missing:
                log.error("missing_schemas", schemas=missing)
                return 4
            log.info("schemas_verified", schemas=found)
            log.info("migrations_done", applied=applied_count, total=len(files))
    finally:
        close_pool()

    return 0


if __name__ == "__main__":
    sys.exit(main())
