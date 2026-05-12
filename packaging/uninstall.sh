#!/usr/bin/env bash
# LabDog uninstaller
# Usage: sudo ./uninstall.sh [--purge] [--help]

set -euo pipefail

PURGE=0
log() { echo "[labdog-uninstall] $*" >&2; }
die() { echo "Error: $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sudo ./uninstall.sh [--purge] [--help]

Removes LabDog from the system.

Options:
  --purge   Also remove configuration, data, logs, and the system user
  --help    Show this help message

Without --purge, configuration (/etc/labdog), data (/var/lib/labdog),
logs (/var/log/labdog), and the labdog user are preserved.
EOF
    exit 0
}

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        --help|-h) usage ;;
        *) die "Unknown option: $arg" ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    die "must run as root (try: sudo $0)"
fi

# --- Stop and disable services ---
if command -v systemctl >/dev/null 2>&1; then
    log "Stopping labdog service..."
    systemctl stop labdog.service 2>/dev/null || true
    systemctl disable labdog.service 2>/dev/null || true
fi

# --- Detect systemd unit path ---
if [ -d /usr/lib/systemd/system ]; then
    SYSTEMD_DIR=/usr/lib/systemd/system
elif [ -d /lib/systemd/system ]; then
    SYSTEMD_DIR=/lib/systemd/system
else
    SYSTEMD_DIR=/usr/lib/systemd/system
fi

# --- Remove unit files ---
log "Removing systemd unit files..."
rm -f "$SYSTEMD_DIR"/labdog.service

# --- Remove tmpfiles config ---
log "Removing tmpfiles.d configuration..."
rm -f /usr/lib/tmpfiles.d/labdog.conf

# --- Remove application ---
log "Removing /usr/lib/labdog..."
rm -rf /usr/lib/labdog

# --- Remove runtime directory ---
rm -rf /run/labdog

# --- Reload systemd ---
# Tolerate environments where systemd isn't running as PID 1 — the
# unit file is already gone, so a failed reload doesn't matter, and
# the --purge block below would otherwise be silently skipped.
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
fi

# --- Purge (config, data, logs, user) ---
if [ "$PURGE" -eq 1 ]; then
    log "Purging configuration, data, logs, and user..."
    rm -rf /etc/labdog /var/lib/labdog /var/log/labdog

    if id labdog >/dev/null 2>&1; then
        deluser --system labdog 2>/dev/null || userdel labdog 2>/dev/null || true
        log "Removed system user 'labdog'"
    fi
    if getent group labdog >/dev/null 2>&1; then
        delgroup labdog 2>/dev/null || groupdel labdog 2>/dev/null || true
    fi
fi

log "Uninstall complete."
echo ""
echo "Removed:"
echo "  /usr/lib/labdog          (application)"
echo "  $SYSTEMD_DIR/labdog.service (systemd unit)"
echo "  /usr/lib/tmpfiles.d/labdog.conf"
echo "  /run/labdog              (runtime)"

if [ "$PURGE" -eq 1 ]; then
    echo "  /etc/labdog              (configuration)"
    echo "  /var/lib/labdog          (data)"
    echo "  /var/log/labdog          (logs)"
    echo "  labdog user and group"
else
    echo ""
    echo "Preserved (use --purge to remove):"
    echo "  /etc/labdog              (configuration)"
    echo "  /var/lib/labdog          (data)"
    echo "  /var/log/labdog          (logs)"
    echo "  labdog system user"
fi
