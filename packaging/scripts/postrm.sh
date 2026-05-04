#!/bin/sh
set -e

case "$1" in
    purge)
        rm -rf /var/lib/labdog /var/log/labdog /etc/labdog
        if getent passwd labdog > /dev/null 2>&1; then
            deluser --system labdog 2>/dev/null || userdel labdog 2>/dev/null || true
        fi
        ;;
    remove|disappear)
        if command -v systemctl > /dev/null 2>&1; then
            systemctl daemon-reload || true
        fi
        ;;
esac
