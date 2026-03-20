#!/bin/sh
set -e

case "$1" in
    remove|upgrade|deconfigure)
        if command -v systemctl > /dev/null 2>&1; then
            systemctl stop barricade.service 2>/dev/null || true
            systemctl disable barricade.service 2>/dev/null || true
        fi
        ;;
esac
