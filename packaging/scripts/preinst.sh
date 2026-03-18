#!/bin/sh
set -e

case "$1" in
    install|upgrade)
        if ! getent passwd barricade > /dev/null 2>&1; then
            adduser \
                --system \
                --group \
                --no-create-home \
                --home /var/lib/barricade \
                --shell /usr/sbin/nologin \
                --gecos "Barricade service account" \
                barricade
        fi
        ;;
esac
