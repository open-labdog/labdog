#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}/.."
ENV_FILE="${SCRIPT_DIR}/.env"

"${SCRIPT_DIR}/build.sh"

echo ""
echo "--- Restarting containers ---"
docker compose -f "${SCRIPT_DIR}/docker-compose.yml" --env-file "${ENV_FILE}" down
docker compose -f "${SCRIPT_DIR}/docker-compose.yml" --env-file "${ENV_FILE}" up -d

echo ""
echo "=== Deploy complete ==="
docker compose -f "${SCRIPT_DIR}/docker-compose.yml" ps
