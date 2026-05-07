#!/bin/sh
# Pre-install: create the labdog system user/group if missing.
# Idempotent — runs on both deb (install/upgrade) and rpm (1=install, 2=upgrade).
set -e

if getent passwd labdog > /dev/null 2>&1; then
    exit 0
fi

if command -v adduser > /dev/null 2>&1 && adduser --help 2>&1 | grep -q -- '--system'; then
    # Debian-family
    adduser \
        --system \
        --group \
        --no-create-home \
        --home /var/lib/labdog \
        --shell /usr/sbin/nologin \
        --gecos "LabDog service account" \
        labdog
elif command -v useradd > /dev/null 2>&1; then
    # RPM-family — group auto-created by --user-group
    useradd \
        --system \
        --user-group \
        --no-create-home \
        --home-dir /var/lib/labdog \
        --shell /usr/sbin/nologin \
        --comment "LabDog service account" \
        labdog
else
    echo "preinst: neither adduser nor useradd available — cannot create labdog user" >&2
    exit 1
fi
