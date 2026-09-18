#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}/.."

TAG="latest"
IMAGE="labdog"

echo "=== LabDog Local Build ==="
echo ""

# Remove previous image
if docker image inspect "${IMAGE}:${TAG}" &>/dev/null; then
  echo "Removing old ${IMAGE}:${TAG}"
  docker rmi "${IMAGE}:${TAG}" 2>/dev/null || true
fi

# Build AIO image
echo ""
echo "--- Building labdog ---"
# Pass the pinned bundled-pack ref explicitly. Without this the Dockerfile's
# in-stage default (`main`) applies, so every local build — including the one
# deploy.sh ships — silently bundles whatever labdog-playbooks main happened to
# be at that minute instead of the SHA committed in LABDOG_PLAYBOOKS_REF.
LABDOG_PLAYBOOKS_REF="$(tr -d '[:space:]' < "${ROOT_DIR}/LABDOG_PLAYBOOKS_REF")"

docker build \
  --tag "${IMAGE}:${TAG}" \
  --build-arg LABDOG_PLAYBOOKS_REF="${LABDOG_PLAYBOOKS_REF}" \
  --file "${ROOT_DIR}/Dockerfile" \
  "${ROOT_DIR}"

# Prune build cache and dangling images (only when --clean is passed)
if [[ "${1:-}" == "--clean" ]]; then
  echo ""
  echo "--- Cleaning up ---"
  docker builder prune -f 2>/dev/null || true
  docker image prune -f 2>/dev/null || true
fi

echo ""
echo "=== Done ==="
docker images --filter "reference=${IMAGE}:${TAG}" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
