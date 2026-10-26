#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../app"

if [ ! -d node_modules ]; then
    echo "Installing frontend dependencies..."
    npm install
fi

npm run tauri dev
