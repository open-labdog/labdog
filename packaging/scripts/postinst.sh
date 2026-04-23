#!/bin/sh
set -e

case "$1" in
    configure)
        mkdir -p /var/lib/labdog /var/log/labdog
        chown -R labdog:labdog /var/lib/labdog /var/log/labdog
        chmod 750 /var/lib/labdog /var/log/labdog

        # tmpfiles.d creates volatile /run/labdog
        if command -v systemd-tmpfiles > /dev/null 2>&1; then
            systemd-tmpfiles --create labdog.conf 2>/dev/null || true
        fi

        if command -v systemctl > /dev/null 2>&1 && systemctl is-system-running > /dev/null 2>&1; then
            systemctl daemon-reload
        fi

        PREV_VERSION="$2"
        if [ -z "$PREV_VERSION" ]; then
            echo ""
            echo "LabDog installed successfully."
            echo ""
            echo "Next steps:"
            echo "  1. Edit /etc/labdog/labdog.toml with your configuration"
            echo "     (update [security] secret_key, encryption_key, [database] url, labdog_server_ip)"
            echo "  2. Enable and start the service:"
            echo "     systemctl enable --now labdog.service"
            echo "  3. Check status:"
            echo "     systemctl status labdog.service"
            echo ""
        else
            if systemctl is-active --quiet labdog.service 2>/dev/null; then
                systemctl restart labdog.service 2>/dev/null || true
            fi
        fi
        ;;
esac
