#!/bin/sh
set -e

case "$1" in
    remove|upgrade|deconfigure)
        if command -v systemctl > /dev/null 2>&1; then
            systemctl stop labdog.service 2>/dev/null || true
            systemctl disable labdog.service 2>/dev/null || true
        fi
        ;;
esac
