#!/bin/sh
set -e

case "$1" in
    purge)
        rm -rf /var/lib/barricade /var/log/barricade /etc/barricade
        if getent passwd barricade > /dev/null 2>&1; then
            deluser --system barricade 2>/dev/null || userdel barricade 2>/dev/null || true
        fi
        ;;
    remove|disappear)
        if command -v systemctl > /dev/null 2>&1; then
            systemctl daemon-reload || true
        fi
        ;;
esac
