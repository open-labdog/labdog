#!/bin/sh
# Post-remove: reload systemd; on deb purge, drop config/data and the user.
#
# $1 conventions:
#   deb: "remove" | "purge" | "upgrade" | "abort-install" | ...
#   rpm: 0 (full uninstall) | 1 (upgrade)
#
# rpm has no "purge" concept — admins remove /etc/labdog and /var/lib/labdog
# manually after `rpm -e` if they want a full wipe.
set -e

case "$1" in
    purge)
        rm -rf /var/lib/labdog /var/log/labdog /etc/labdog
        if getent passwd labdog > /dev/null 2>&1; then
            deluser --system labdog 2>/dev/null \
                || userdel labdog 2>/dev/null \
                || true
        fi
        if getent group labdog > /dev/null 2>&1; then
            delgroup labdog 2>/dev/null \
                || groupdel labdog 2>/dev/null \
                || true
        fi
        ;;
esac

case "$1" in
    remove|0)
        if command -v systemctl > /dev/null 2>&1; then
            systemctl daemon-reload 2>/dev/null || true
        fi
        ;;
esac
