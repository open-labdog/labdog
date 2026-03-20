#!/usr/bin/env bash
# Barricade installer — run from inside extracted tarball
# Usage: sudo ./install.sh [--help]

set -euo pipefail

log() { echo "[barricade-install] $*" >&2; }
die() { echo "Error: $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sudo ./install.sh [--help]

Installs Barricade to standard FHS paths:
  /usr/lib/barricade         — application code
  /etc/barricade             — configuration
  /var/lib/barricade         — persistent data
  /var/log/barricade         — log files
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
for required_dir in usr/lib/barricade systemd tmpfiles.d; do
    if [ ! -d "$SCRIPT_DIR/$required_dir" ]; then
        die "Expected directory '$required_dir' not found in $SCRIPT_DIR — are you running from the extracted tarball?"
    fi
done
if [ ! -f "$SCRIPT_DIR/etc/barricade.toml" ]; then
    die "Expected file 'etc/barricade.toml' not found in $SCRIPT_DIR — are you running from the extracted tarball?"
fi

# --- Create system user ---
log "Creating barricade system user..."
if id barricade >/dev/null 2>&1; then
    log "User 'barricade' already exists, skipping"
else
    if command -v adduser >/dev/null 2>&1 && adduser --help 2>&1 | grep -q -- '--system'; then
        adduser --system --group --home /var/lib/barricade --no-create-home --shell /usr/sbin/nologin barricade
    elif command -v useradd >/dev/null 2>&1; then
        useradd --system --user-group --home-dir /var/lib/barricade --no-create-home --shell /usr/sbin/nologin barricade
    else
        die "Neither adduser nor useradd found — cannot create system user"
    fi
    log "Created system user 'barricade'"
fi

# --- Install application files ---
log "Installing application to /usr/lib/barricade..."
cp -r "$SCRIPT_DIR/usr/lib/barricade" /usr/lib/

# --- Install configuration (preserve existing) ---
if [ -f /etc/barricade/barricade.toml ]; then
    log "Existing /etc/barricade/barricade.toml found — preserving (not overwritten)"
else
    log "Installing default configuration to /etc/barricade..."
    mkdir -p /etc/barricade
    cp "$SCRIPT_DIR/etc/barricade.toml" /etc/barricade/
    chmod 640 /etc/barricade/barricade.toml
    chown root:barricade /etc/barricade/barricade.toml
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
cp "$SCRIPT_DIR"/systemd/barricade.service "$SYSTEMD_DIR/"

# --- Install tmpfiles.d ---
log "Installing tmpfiles.d configuration..."
mkdir -p /usr/lib/tmpfiles.d
cp "$SCRIPT_DIR/tmpfiles.d/barricade.conf" /usr/lib/tmpfiles.d/

# --- Create data directories ---
log "Creating data directories..."
mkdir -p /var/lib/barricade /var/log/barricade
chown -R barricade:barricade /var/lib/barricade /var/log/barricade

# --- Create runtime directory ---
log "Creating runtime directory..."
if command -v systemd-tmpfiles >/dev/null 2>&1; then
    systemd-tmpfiles --create barricade.conf 2>/dev/null || true
else
    mkdir -p /run/barricade
    chown barricade:barricade /run/barricade
fi

# --- Reload systemd ---
if command -v systemctl >/dev/null 2>&1; then
    log "Reloading systemd daemon..."
    systemctl daemon-reload
fi

# --- Success ---
log "Installation complete."

cat <<'EOF'

Barricade installed to /usr/lib/barricade

Next steps:
  1. Edit /etc/barricade/barricade.toml with your settings:
       - [security] secret_key and encryption_key: generate with: openssl rand -base64 32
       - [database] url: your PostgreSQL connection string
       - [security] barricade_server_ip: this server's IP address

  2. Enable and start the service:
       systemctl enable --now barricade.service

  3. Check status:
       systemctl status barricade.service
       journalctl -u barricade -f

EOF
