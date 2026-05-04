#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

# ── Defaults ─────────────────────────────────────────────────────────────────
TARGET="all"
CLEAN=0

# Auto-detect version from pyproject.toml
VERSION=$(grep '^version' "$REPO_ROOT/backend/pyproject.toml" 2>/dev/null \
    | head -1 | sed 's/.*= *"\(.*\)"/\1/' || echo "0.1.0")

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "[build] $*"; }
step() { echo ""; echo "[build] ── $* ──"; }
die()  { echo "ERROR: $*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Build LabDog as a pre-built tarball and Linux packages.

Options:
  --version=X.Y.Z   Package version (default: $VERSION, auto-read from pyproject.toml)
  --target=TARGET   What to build: all | tarball | deb | rpm  (default: all)
  --clean           Remove staging/ and dist/ before building
  --help            Show this help

Examples:
  $(basename "$0")                          # Build all artifacts with auto-detected version
  $(basename "$0") --version=1.0.0          # Build all with explicit version
  $(basename "$0") --target=tarball         # Build tarball only
  $(basename "$0") --clean --version=1.0.0  # Clean then full build

Prerequisites:
  python3.12   apt: python3.12 / dnf: python3.12
  npm          apt: nodejs / dnf: nodejs
  tar          apt: tar / dnf: tar
  nfpm         https://nfpm.goreleaser.com/install/ (for deb/rpm only)

Output (in packaging/dist/):
  labdog-VERSION-linux-amd64.tar.gz
  labdog_VERSION_amd64.deb
  labdog-VERSION-1.x86_64.rpm
EOF
    exit 0
}

# ── Argument parsing ──────────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --version=*) VERSION="${arg#*=}" ;;
        --target=*)  TARGET="${arg#*=}" ;;
        --clean)     CLEAN=1 ;;
        --help|-h)   usage ;;
        *) die "Unknown option: $arg" ;;
    esac
done

# ── Prerequisite checks ───────────────────────────────────────────────────────
step "Checking prerequisites"

MISSING=0

check_prereq() {
    local cmd="$1"
    local install_hint="$2"
    if command -v "$cmd" >/dev/null 2>&1; then
        log "  ✓ $cmd ($(command -v "$cmd"))"
    else
        log "  ✗ $cmd — NOT FOUND"
        log "    Install: $install_hint"
        MISSING=$((MISSING + 1))
    fi
}

check_prereq python3.12  "apt: sudo apt install python3.12  |  dnf: sudo dnf install python3.12"
check_prereq npm         "apt: sudo apt install nodejs     |  dnf: sudo dnf install nodejs"
check_prereq tar         "apt: sudo apt install tar        |  dnf: sudo dnf install tar"

# nfpm only required for deb/rpm
if [[ "$TARGET" == "all" || "$TARGET" == "deb" || "$TARGET" == "rpm" ]]; then
    check_prereq nfpm "https://nfpm.goreleaser.com/install/"
fi

if [[ "$MISSING" -gt 0 ]]; then
    die "$MISSING prerequisite(s) missing. Install them and retry."
fi

# ── Clean if requested ────────────────────────────────────────────────────────
if [[ "$CLEAN" -eq 1 ]]; then
    step "Cleaning staging and dist"
    make -C "$SCRIPT_DIR" clean VERSION="$VERSION"
fi

# ── Build ─────────────────────────────────────────────────────────────────────
TOTAL_STEPS=2
CURRENT_STEP=0

TOTAL_STEPS=$((TOTAL_STEPS + $([ "$TARGET" = "all" ] && echo 3 || echo 1)))

CURRENT_STEP=$((CURRENT_STEP + 1))
step "[$CURRENT_STEP/$((TOTAL_STEPS))] Building application components"
make -C "$SCRIPT_DIR" build VERSION="$VERSION"

# ── Package ───────────────────────────────────────────────────────────────────
case "$TARGET" in
    tarball)
        CURRENT_STEP=$((CURRENT_STEP + 1))
        step "[$CURRENT_STEP/$TOTAL_STEPS] Building tarball"
        make -C "$SCRIPT_DIR" tarball VERSION="$VERSION"
        ;;
    deb)
        CURRENT_STEP=$((CURRENT_STEP + 1))
        step "[$CURRENT_STEP/$TOTAL_STEPS] Building .deb package"
        make -C "$SCRIPT_DIR" deb VERSION="$VERSION"
        ;;
    rpm)
        CURRENT_STEP=$((CURRENT_STEP + 1))
        step "[$CURRENT_STEP/$TOTAL_STEPS] Building .rpm package"
        make -C "$SCRIPT_DIR" rpm VERSION="$VERSION"
        ;;
    all)
        CURRENT_STEP=$((CURRENT_STEP + 1))
        step "[$CURRENT_STEP/$TOTAL_STEPS] Building tarball"
        make -C "$SCRIPT_DIR" tarball VERSION="$VERSION"
        CURRENT_STEP=$((CURRENT_STEP + 1))
        step "[$CURRENT_STEP/$TOTAL_STEPS] Building .deb package"
        make -C "$SCRIPT_DIR" deb VERSION="$VERSION"
        CURRENT_STEP=$((CURRENT_STEP + 1))
        step "[$CURRENT_STEP/$TOTAL_STEPS] Building .rpm package"
        make -C "$SCRIPT_DIR" rpm VERSION="$VERSION"
        ;;
    *)
        die "Unknown target: $TARGET. Valid: all | tarball | deb | rpm"
        ;;
esac

# ── Summary ───────────────────────────────────────────────────────────────────
DIST_DIR="$SCRIPT_DIR/dist"
step "Build complete — artifacts:"
echo ""
for f in "$DIST_DIR"/labdog-* "$DIST_DIR"/labdog_*; do
    [[ -f "$f" ]] || continue
    SIZE=$(du -sh "$f" | cut -f1)
    SHA=$(sha256sum "$f" | cut -d' ' -f1)
    echo "  $f"
    echo "    size:   $SIZE"
    echo "    sha256: $SHA"
    echo ""
done
