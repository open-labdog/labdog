#!/usr/bin/env bash
# Verify backend/app/ansible/ is byte-identical to a tagged release of
# open-labdog/labdog-playbooks. Run in CI on every push so drift in
# either direction trips the build.
#
# The bundled directory is the in-image copy of the action pack — it
# becomes the safety-net override when the seeded labdog-playbooks
# GitRepository is unreachable at boot. Any drift between the two
# breaks the "what's in the image is exactly what was published" promise.
#
# Configuration:
#   PLAYBOOKS_REF — git ref to mirror against (default: main).
#                   Pin to a release tag once labdog-playbooks publishes
#                   its first tag.
#   PLAYBOOKS_REPO — override the upstream repo URL (default the
#                    canonical open-labdog/labdog-playbooks repo).
#                    Useful for local dev against a sibling checkout.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLED_DIR="${REPO_ROOT}/backend/app/ansible"
PLAYBOOKS_REPO="${PLAYBOOKS_REPO:-https://github.com/open-labdog/labdog-playbooks}"
PLAYBOOKS_REF="${PLAYBOOKS_REF:-main}"

if [ ! -d "${BUNDLED_DIR}" ]; then
  echo "error: bundled pack directory not found at ${BUNDLED_DIR}" >&2
  exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT

echo "Cloning ${PLAYBOOKS_REPO}@${PLAYBOOKS_REF} into a scratch dir…" >&2
if ! git clone --quiet --depth 1 --branch "${PLAYBOOKS_REF}" "${PLAYBOOKS_REPO}" "${tmp}/upstream" 2>/dev/null; then
  echo "warning: failed to clone ${PLAYBOOKS_REPO}@${PLAYBOOKS_REF}." >&2
  echo "         If the repo isn't published yet, this check is a no-op." >&2
  echo "         Set PLAYBOOKS_REPO to a sibling checkout to validate locally." >&2
  exit 0
fi

# Drop git metadata — these are repo plumbing, not pack content.
rm -rf "${tmp}/upstream/.git" "${tmp}/upstream/.gitignore"

if diff -rq "${BUNDLED_DIR}" "${tmp}/upstream" > "${tmp}/diff.out" 2>&1; then
  echo "ok: backend/app/ansible/ matches ${PLAYBOOKS_REPO}@${PLAYBOOKS_REF}"
  exit 0
fi

echo "error: backend/app/ansible/ has drifted from ${PLAYBOOKS_REPO}@${PLAYBOOKS_REF}" >&2
echo "" >&2
cat "${tmp}/diff.out" >&2
echo "" >&2
echo "Fix: re-sync from upstream with" >&2
echo "  rsync -a --exclude='.git' --exclude='.gitignore' /path/to/labdog-playbooks/ backend/app/ansible/" >&2
echo "Or update the bundled tree to match by editing the upstream repo first." >&2
exit 1
