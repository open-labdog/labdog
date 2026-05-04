#!/bin/sh
set -e

case "$1" in
    install|upgrade)
        if ! getent passwd labdog > /dev/null 2>&1; then
            adduser \
                --system \
                --group \
                --no-create-home \
                --home /var/lib/labdog \
                --shell /usr/sbin/nologin \
                --gecos "LabDog service account" \
                labdog
        fi
        ;;
esac
