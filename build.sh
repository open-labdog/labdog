#!/usr/bin/env bash
set -euo pipefail

TAG="barricade-local"
IMAGES=("barricade-backend" "barricade-frontend")

echo "=== Barricade Local Build ==="
echo ""

# Remove previous images
for img in "${IMAGES[@]}"; do
  if docker image inspect "${img}:${TAG}" &>/dev/null; then
    echo "Removing old ${img}:${TAG}"
    docker rmi "${img}:${TAG}" 2>/dev/null || true
  fi
done

# Build backend
echo ""
echo "--- Building backend ---"
docker build \
  --tag "barricade-backend:${TAG}" \
  --file backend/Dockerfile \
  backend/

# Build frontend
echo ""
echo "--- Building frontend ---"
docker build \
  --tag "barricade-frontend:${TAG}" \
  --file frontend/Dockerfile \
  frontend/

# Prune build cache and dangling images
echo ""
echo "--- Cleaning up ---"
docker builder prune -f 2>/dev/null || true
docker image prune -f 2>/dev/null || true

echo ""
echo "=== Done ==="
docker images --filter "reference=barricade-*:${TAG}" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
