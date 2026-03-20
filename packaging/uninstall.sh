#!/usr/bin/env bash
# Barricade uninstaller
# Usage: sudo ./uninstall.sh [--purge] [--help]

set -euo pipefail

PURGE=0
log() { echo "[barricade-uninstall] $*" >&2; }
die() { echo "Error: $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sudo ./uninstall.sh [--purge] [--help]

Removes Barricade from the system.

Options:
  --purge   Also remove configuration, data, logs, and the system user
  --help    Show this help message

Without --purge, configuration (/etc/barricade), data (/var/lib/barricade),
logs (/var/log/barricade), and the barricade user are preserved.
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
    log "Stopping barricade service..."
    systemctl stop barricade.service 2>/dev/null || true
    systemctl disable barricade.service 2>/dev/null || true
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
rm -f "$SYSTEMD_DIR"/barricade.service

# --- Remove tmpfiles config ---
log "Removing tmpfiles.d configuration..."
rm -f /usr/lib/tmpfiles.d/barricade.conf

# --- Remove application ---
log "Removing /usr/lib/barricade..."
rm -rf /usr/lib/barricade

# --- Remove runtime directory ---
rm -rf /run/barricade

# --- Reload systemd ---
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
fi

# --- Purge (config, data, logs, user) ---
if [ "$PURGE" -eq 1 ]; then
    log "Purging configuration, data, logs, and user..."
    rm -rf /etc/barricade /var/lib/barricade /var/log/barricade

    if id barricade >/dev/null 2>&1; then
        deluser --system barricade 2>/dev/null || userdel barricade 2>/dev/null || true
        log "Removed system user 'barricade'"
    fi
    if getent group barricade >/dev/null 2>&1; then
        delgroup barricade 2>/dev/null || groupdel barricade 2>/dev/null || true
    fi
fi

log "Uninstall complete."
echo ""
echo "Removed:"
echo "  /usr/lib/barricade          (application)"
echo "  $SYSTEMD_DIR/barricade.service (systemd unit)"
echo "  /usr/lib/tmpfiles.d/barricade.conf"
echo "  /run/barricade              (runtime)"

if [ "$PURGE" -eq 1 ]; then
    echo "  /etc/barricade              (configuration)"
    echo "  /var/lib/barricade          (data)"
    echo "  /var/log/barricade          (logs)"
    echo "  barricade user and group"
else
    echo ""
    echo "Preserved (use --purge to remove):"
    echo "  /etc/barricade              (configuration)"
    echo "  /var/lib/barricade          (data)"
    echo "  /var/log/barricade          (logs)"
    echo "  barricade system user"
fi
