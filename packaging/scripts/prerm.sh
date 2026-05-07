#!/bin/sh
# Pre-remove: stop and disable the service when fully uninstalling.
#
# $1 conventions:
#   deb: "remove" | "upgrade" | "deconfigure"
#   rpm: 0 (full uninstall) | 1 (upgrade — leave running)
set -e

STOP=0
case "$1" in
    remove|deconfigure|0)
        STOP=1
        ;;
esac

if [ "$STOP" -eq 1 ] && command -v systemctl > /dev/null 2>&1; then
    systemctl stop labdog.service 2>/dev/null || true
    systemctl disable labdog.service 2>/dev/null || true
fi
