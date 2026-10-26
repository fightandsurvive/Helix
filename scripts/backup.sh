#!/usr/bin/env bash
# Nightly backup: checkpoints, database, manifests.
set -euo pipefail

DEST="${BACKUP_DEST:-/mnt/backup/helix}"
DATE=$(date +%Y-%m-%d)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$DEST"

# Checkpoints — rsync with --delete so removed files stay removed
rsync -a --delete "$REPO_ROOT/checkpoints/" "$DEST/checkpoints/"

# Database dump — schema only (data regenerable via ingest pipeline)
pg_dump helix_db | zstd -3 > "$DEST/db-$DATE.sql.zst"

# Manifest
ls -la "$DEST" > "$DEST/manifest-$DATE.txt"

echo "✓ Backup complete: $DEST"
