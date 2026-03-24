#!/usr/bin/env bash
set -euo pipefail

TAG="latest"
IMAGE="barricade"

echo "=== Barricade Local Build ==="
echo ""

# Remove previous image
if docker image inspect "${IMAGE}:${TAG}" &>/dev/null; then
  echo "Removing old ${IMAGE}:${TAG}"
  docker rmi "${IMAGE}:${TAG}" 2>/dev/null || true
fi

# Build AIO image
echo ""
echo "--- Building barricade ---"
docker build \
  --tag "${IMAGE}:${TAG}" \
  --file Dockerfile \
  .

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
