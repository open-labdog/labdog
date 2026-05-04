#!/usr/bin/env bash
# LabDog installer — run from inside extracted tarball
# Usage: sudo ./install.sh [--help]

set -euo pipefail

log() { echo "[labdog-install] $*" >&2; }
die() { echo "Error: $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sudo ./install.sh [--help]

Installs LabDog to standard FHS paths:
  /usr/lib/labdog         — application code
  /etc/labdog             — configuration
  /var/lib/labdog         — persistent data
  /var/log/labdog         — log files
  /usr/lib/systemd/system    — systemd unit files

This script must be run as root from inside the extracted tarball directory.
EOF
    exit 0
}

# --- Parse args ---
for arg in "$@"; do
    case "$arg" in
        --help|-h) usage ;;
        *) die "Unknown option: $arg" ;;
    esac
done

# --- Root check ---
if [ "$(id -u)" -ne 0 ]; then
    die "must run as root (try: sudo $0)"
fi

# --- Locate script directory (tarball root) ---
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

# --- Verify tarball structure ---
for required_dir in usr/lib/labdog systemd tmpfiles.d; do
    if [ ! -d "$SCRIPT_DIR/$required_dir" ]; then
        die "Expected directory '$required_dir' not found in $SCRIPT_DIR — are you running from the extracted tarball?"
    fi
done
if [ ! -f "$SCRIPT_DIR/etc/labdog.toml" ]; then
    die "Expected file 'etc/labdog.toml' not found in $SCRIPT_DIR — are you running from the extracted tarball?"
fi

# --- Create system user ---
log "Creating labdog system user..."
if id labdog >/dev/null 2>&1; then
    log "User 'labdog' already exists, skipping"
else
    if command -v adduser >/dev/null 2>&1 && adduser --help 2>&1 | grep -q -- '--system'; then
        adduser --system --group --home /var/lib/labdog --no-create-home --shell /usr/sbin/nologin labdog
    elif command -v useradd >/dev/null 2>&1; then
        useradd --system --user-group --home-dir /var/lib/labdog --no-create-home --shell /usr/sbin/nologin labdog
    else
        die "Neither adduser nor useradd found — cannot create system user"
    fi
    log "Created system user 'labdog'"
fi

# --- Install application files ---
log "Installing application to /usr/lib/labdog..."
cp -r "$SCRIPT_DIR/usr/lib/labdog" /usr/lib/

# --- Install configuration (preserve existing) ---
if [ -f /etc/labdog/labdog.toml ]; then
    log "Existing /etc/labdog/labdog.toml found — preserving (not overwritten)"
else
    log "Installing default configuration to /etc/labdog..."
    mkdir -p /etc/labdog
    cp "$SCRIPT_DIR/etc/labdog.toml" /etc/labdog/
    chmod 640 /etc/labdog/labdog.toml
    chown root:labdog /etc/labdog/labdog.toml
fi

# --- Detect systemd unit path ---
if [ -d /usr/lib/systemd/system ]; then
    SYSTEMD_DIR=/usr/lib/systemd/system
elif [ -d /lib/systemd/system ]; then
    SYSTEMD_DIR=/lib/systemd/system
else
    SYSTEMD_DIR=/usr/lib/systemd/system
    mkdir -p "$SYSTEMD_DIR"
fi

log "Installing systemd unit to $SYSTEMD_DIR..."
cp "$SCRIPT_DIR"/systemd/labdog.service "$SYSTEMD_DIR/"

# --- Install tmpfiles.d ---
log "Installing tmpfiles.d configuration..."
mkdir -p /usr/lib/tmpfiles.d
cp "$SCRIPT_DIR/tmpfiles.d/labdog.conf" /usr/lib/tmpfiles.d/

# --- Create data directories ---
log "Creating data directories..."
mkdir -p /var/lib/labdog /var/log/labdog
chown -R labdog:labdog /var/lib/labdog /var/log/labdog

# --- Create runtime directory ---
log "Creating runtime directory..."
if command -v systemd-tmpfiles >/dev/null 2>&1; then
    systemd-tmpfiles --create labdog.conf 2>/dev/null || true
else
    mkdir -p /run/labdog
    chown labdog:labdog /run/labdog
fi

# --- Reload systemd ---
if command -v systemctl >/dev/null 2>&1; then
    log "Reloading systemd daemon..."
    systemctl daemon-reload
fi

# --- Success ---
log "Installation complete."

cat <<'EOF'

LabDog installed to /usr/lib/labdog

Next steps:
  1. Edit /etc/labdog/labdog.toml with your settings:
       - [security] secret_key and encryption_key: generate with: openssl rand -base64 32
       - [database] url: your PostgreSQL connection string
       - [security] labdog_server_ip: this server's IP address

  2. Enable and start the service:
       systemctl enable --now labdog.service

  3. Check status:
       systemctl status labdog.service
       journalctl -u labdog -f

EOF
