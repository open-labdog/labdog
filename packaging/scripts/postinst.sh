#!/bin/sh
set -e

case "$1" in
    configure)
        mkdir -p /var/lib/barricade /var/log/barricade
        chown -R barricade:barricade /var/lib/barricade /var/log/barricade
        chmod 750 /var/lib/barricade /var/log/barricade

        # tmpfiles.d creates volatile /run/barricade
        if command -v systemd-tmpfiles > /dev/null 2>&1; then
            systemd-tmpfiles --create barricade.conf 2>/dev/null || true
        fi

        if command -v systemctl > /dev/null 2>&1 && systemctl is-system-running > /dev/null 2>&1; then
            systemctl daemon-reload
        fi

        PREV_VERSION="$2"
        if [ -z "$PREV_VERSION" ]; then
            echo ""
            echo "Barricade installed successfully."
            echo ""
            echo "Next steps:"
            echo "  1. Edit /etc/barricade/barricade.toml with your configuration"
            echo "     (update [security] secret_key, encryption_key, [database] url, barricade_server_ip)"
            echo "  2. Enable and start the service:"
            echo "     systemctl enable --now barricade.service"
            echo "  3. Check status:"
            echo "     systemctl status barricade.service"
            echo ""
        else
            if systemctl is-active --quiet barricade.service 2>/dev/null; then
                systemctl restart barricade.service 2>/dev/null || true
            fi
        fi
        ;;
esac
