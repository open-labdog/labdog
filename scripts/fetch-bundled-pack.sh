#!/usr/bin/env sh
# Fetch the labdog-playbooks bundled action pack into a destination
# directory. Single source of truth for the clone logic used by the
# Dockerfile builder stage, packaging/Makefile, the CI workflow, and
# dev/dev.sh.
#
# Usage:  fetch-bundled-pack.sh <dest-dir>
#
# Environment:
#   LABDOG_PLAYBOOKS_REPO  upstream URL (default: github
#                          open-labdog/labdog-playbooks). Override e.g.
#                          to gitlab.lan.tyresson.se/dennis/labdog-playbooks
#                          while github mirror is stale (see BUG-46).
#   LABDOG_PLAYBOOKS_REF   git ref -- tag, branch, or commit SHA. No
#                          default; must be set. Read from the repo-root
#                          LABDOG_PLAYBOOKS_REF file in calling contexts.
#
# Exit codes:
#   0  success
#   1  bad args / missing env
#   2  clone failed
#   3  checkout (ref doesn't resolve on upstream) failed
#
# POSIX sh so it runs under alpine/git's busybox shell as well as
# bash on Ubuntu / macOS.

set -eu

DEST="${1:-}"
if [ -z "$DEST" ]; then
  echo "fetch-bundled-pack: ERROR: missing destination directory" >&2
  echo "usage: fetch-bundled-pack.sh <dest-dir>" >&2
  exit 1
fi

REPO="${LABDOG_PLAYBOOKS_REPO:-https://github.com/open-labdog/labdog-playbooks.git}"
REF="${LABDOG_PLAYBOOKS_REF:-}"
if [ -z "$REF" ]; then
  echo "fetch-bundled-pack: ERROR: LABDOG_PLAYBOOKS_REF is empty." >&2
  echo "  Commit a SHA to ./LABDOG_PLAYBOOKS_REF or pass via env." >&2
  exit 1
fi

# Validate REF: allow SHAs, tag names, branch names; reject leading dashes,
# shell metacharacters, and whitespace to prevent flag-injection into git.
case "$REF" in
  *[!A-Za-z0-9._/-]*)
    echo "fetch-bundled-pack: ERROR: LABDOG_PLAYBOOKS_REF contains invalid characters: '${REF}'" >&2
    echo "  Allowed: A-Za-z0-9 . _ / -  (no leading dash, no whitespace, no shell metacharacters)" >&2
    exit 1
    ;;
  -*)
    echo "fetch-bundled-pack: ERROR: LABDOG_PLAYBOOKS_REF must not start with '-': '${REF}'" >&2
    exit 1
    ;;
esac

# Validate REPO: require an explicit URL scheme from the allow-list
# (matches backend/app/schemas/git_repos.py:43-44).  Rejects any value
# starting with '-' (--upload-pack= injection, CVE-2017-1000117 family).
case "$REPO" in
  https://*|git@*|ssh://*)
    ;;
  *)
    echo "fetch-bundled-pack: ERROR: LABDOG_PLAYBOOKS_REPO does not match allowed schemes (https://, git@, ssh://): '${REPO}'" >&2
    exit 1
    ;;
esac

mkdir -p "$DEST"
# Clone into a sibling of $DEST so we can move .git out before any
# consumer notices; cleaner than racing with `rm -rf $DEST/.git`.
TMP="${DEST}.fetch.$$"
trap 'rm -rf "$TMP"' EXIT
rm -rf "$TMP"

echo "fetch-bundled-pack: cloning ${REPO}@${REF} into ${DEST}..." >&2

# Try the shallow tag/branch path first. SHA refs are rejected by
# `--branch`, so on failure we fall back to a full clone + explicit
# checkout. We deliberately don't silence stderr from the full clone
# -- if the upstream is unreachable the operator should see why.
if git clone --depth 1 --branch "$REF" -- "$REPO" "$TMP" 2>/dev/null; then
  :
else
  echo "fetch-bundled-pack: --branch '${REF}' didn't resolve (likely a SHA), falling back to full clone..." >&2
  if ! git clone -- "$REPO" "$TMP"; then
    echo "fetch-bundled-pack: ERROR: failed to clone ${REPO}" >&2
    exit 2
  fi
  if ! git -C "$TMP" checkout -- "$REF"; then
    echo "fetch-bundled-pack: ERROR: ref '${REF}' not found in ${REPO}." >&2
    echo "  If you just bumped LABDOG_PLAYBOOKS_REF, make sure the SHA has been pushed to the upstream." >&2
    echo "  See BUGS.md BUG-46 for the gitlab->github mirror transition." >&2
    exit 3
  fi
fi

rm -rf "$TMP/.git" "$TMP/.gitignore"

# Atomic-ish swap: clear the dest then move the fetched tree in.
rm -rf "$DEST"
mv "$TMP" "$DEST"
trap - EXIT

echo "fetch-bundled-pack: populated ${DEST}" >&2
