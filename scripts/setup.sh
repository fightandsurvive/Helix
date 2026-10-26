#!/usr/bin/env bash
# Helix setup — idempotent, safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "[1/7] System dependencies"
sudo apt update
sudo apt install -y \
    build-essential curl wget unzip git libssl-dev \
    postgresql postgresql-16-pg-cron postgresql-contrib \
    libopenblas-dev cmake ninja-build pkg-config zstd \
    nodejs npm cargo rustc libwebkit2gtk-4.1-dev libgtk-3-dev

echo "[2/7] Python 3.15"
if ! command -v python3.15 &>/dev/null; then
    if ! apt-cache show python3.15 &>/dev/null 2>&1; then
        sudo add-apt-repository -y ppa:deadsnakes/ppa
        sudo apt update
    fi
    sudo apt install -y python3.15 python3.15-venv python3.15-dev
fi
PY=python3.15

echo "[3/7] PostgreSQL"
sudo systemctl enable --now postgresql
sudo -u postgres psql <<'SQL' || true
CREATE USER trin WITH PASSWORD 'trin_dev';
CREATE DATABASE helix_db OWNER trin;
SQL
sudo -u postgres psql -d helix_db <<'SQL' || true
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;
CREATE EXTENSION IF NOT EXISTS pg_cron;
GRANT ALL ON SCHEMA public TO trin;
SQL
sudo -u postgres psql -c "ALTER SYSTEM SET io_method = 'io_uring';" || true
sudo systemctl restart postgresql

echo "[4/7] Python venv"
if [ ! -d .venv ]; then $PY -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

echo "[5/7] Ollama + DeepSeek-R1 32B"
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.ai/install.sh | sh
fi
ollama pull deepseek-r1:32b || echo "  (Ollama pull failed — LLM commentary will be unavailable)"

echo "[6/7] Stockfish 17.1"
mkdir -p "$HOME/bin"
if [ ! -f "$HOME/bin/stockfish-17.1" ]; then
    cd "$HOME/bin"
    wget -q https://github.com/official-stockfish/Stockfish/releases/download/sf_17.1/stockfish-ubuntu-x86-64-avx2.tar
    tar xf stockfish-ubuntu-x86-64-avx2.tar
    mv stockfish/stockfish-ubuntu-x86-64-avx2 stockfish-17.1
    chmod +x stockfish-17.1
    cd "$REPO_ROOT"
fi

echo "[7/7] .env"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  Created .env — edit DATABASE_URL, SYZYGY_PATH, STOCKFISH_PATH"
fi

echo ""
echo "✓ Setup complete."
echo ""
echo "Next steps:"
echo "  1. Download syzygy: bash scripts/download_syzygy.sh ~/syzygy 345"
echo "  2. Run migrations:  python migrations/run_migrations.py"
echo "  3. Load ECO data:   python -m ingest.eco_loader"
echo "  4. Load PGN data:   python -m ingest.bulk_loader ~/pgn/master"
