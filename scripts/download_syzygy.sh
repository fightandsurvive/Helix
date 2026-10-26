#!/usr/bin/env bash
# Download Syzygy tablebases.
#
# Usage:
#   ./download_syzygy.sh [DEST] [345|6|all]
#
# 3-4-5:  ~1 GB, covers every ≤5-piece endgame. Install this first.
# 6:      149 GB, covers ≤6-piece. Torrent recommended (rsync/http slow).
# 7:      16.7 TB — not downloaded here. Dedicated storage project.
set -euo pipefail

DEST="${1:-$HOME/syzygy}"
MODE="${2:-345}"

mkdir -p "$DEST/3-4-5" "$DEST/6"
BASE="https://tablebase.lichess.ovh/tables/standard"

if [[ "$MODE" == "345" || "$MODE" == "all" ]]; then
    echo "[syzygy] Fetching 3-4-5 file list"
    if [ ! -f "$DEST/3-4-5/files.txt" ]; then
        curl -s "$BASE/3-4-5/" \
            | grep -oP 'href="\K[^"]+\.(rtbw|rtbz)' \
            | sort -u > "$DEST/3-4-5/files.txt"
    fi
    while read -r f; do
        [ -z "$f" ] && continue
        if [ ! -f "$DEST/3-4-5/$f" ]; then
            echo "  ↓ $f"
            curl -sL --retry 3 -o "$DEST/3-4-5/$f" "$BASE/3-4-5/$f"
        fi
    done < "$DEST/3-4-5/files.txt"
fi

if [[ "$MODE" == "6" || "$MODE" == "all" ]]; then
    echo "[syzygy] 6-piece set is ~149 GiB."
    echo "  Torrent (recommended): https://github.com/syzygy1/tb/releases"
    echo "  Or rsync:  rsync -av --info=progress2 rsync://tablebase.sesse.net/syzygy/6/ $DEST/6/"
fi

echo ""
echo "✓ Done. Set SYZYGY_PATH=$DEST in .env"
