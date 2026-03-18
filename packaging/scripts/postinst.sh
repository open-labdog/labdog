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
            echo "  1. Edit /etc/barricade/barricade.env with your configuration"
            echo "     (update SECRET_KEY, ENCRYPTION_KEY, DATABASE_URL, BARRICADE_SERVER_IP)"
            echo "  2. Enable and start all services:"
            echo "     systemctl enable --now barricade.target"
            echo "  3. Check status:"
            echo "     systemctl status barricade.target"
            echo ""
        else
            if grep -q '^DATABASE_URL=' /etc/barricade/barricade.env 2>/dev/null; then
                echo "Running database migrations..."
                systemctl start barricade-migrate.service 2>/dev/null || true
            fi
            for svc in barricade-api barricade-worker barricade-beat barricade-frontend; do
                if systemctl is-active --quiet "$svc.service" 2>/dev/null; then
                    systemctl restart "$svc.service" 2>/dev/null || true
                fi
            done
        fi
        ;;
esac
