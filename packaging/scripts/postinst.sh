#!/bin/sh
# Post-install: set up dirs, reload systemd, restart on upgrade.
#
# $1 conventions:
#   deb: "configure" (install or upgrade) — $2 = previous version when upgrading
#   rpm: 1 (first install) | 2+ (upgrade)
set -e

FIRST_INSTALL=0
case "$1" in
    1)
        FIRST_INSTALL=1
        ;;
    configure)
        [ -z "$2" ] && FIRST_INSTALL=1
        ;;
esac

# Idempotent setup — safe to run on every install/upgrade.
mkdir -p /var/lib/labdog /var/log/labdog
chown labdog:labdog /var/lib/labdog /var/log/labdog
chmod 750 /var/lib/labdog /var/log/labdog

if command -v systemd-tmpfiles > /dev/null 2>&1; then
    systemd-tmpfiles --create labdog.conf 2>/dev/null || true
fi

if command -v systemctl > /dev/null 2>&1; then
    systemctl daemon-reload 2>/dev/null || true
fi

if [ "$FIRST_INSTALL" -eq 1 ]; then
    cat <<'EOF'

LabDog installed successfully.

Next steps:
  1. Edit /etc/labdog/labdog.toml — set [security] secret_key and
     encryption_key (generate: openssl rand -base64 32), [database] url,
     and [security] labdog_server_ip.
  2. Enable and start the service:
       systemctl enable --now labdog.service
  3. Check status:
       systemctl status labdog.service
       journalctl -u labdog -f

EOF
else
    # Upgrade — restart if the service is currently running.
    if command -v systemctl > /dev/null 2>&1 \
       && systemctl is-active --quiet labdog.service 2>/dev/null; then
        systemctl restart labdog.service 2>/dev/null || true
    fi
fi
