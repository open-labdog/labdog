#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

./build.sh

echo ""
echo "--- Restarting containers ---"
docker compose down
docker compose up -d

echo ""
echo "=== Deploy complete ==="
docker compose ps
