#!/bin/sh
# Pre-install: create the labdog system user/group if missing.
# Idempotent — runs on both deb (install/upgrade) and rpm (1=install, 2=upgrade).
set -e

if getent passwd labdog > /dev/null 2>&1; then
    exit 0
fi

# Use useradd directly. It's universally available on both
# Debian/Ubuntu (passwd package) and RHEL/Rocky/Fedora (shadow-utils),
# and accepts the same long flags on both. The earlier adduser-based
# detection broke on Rocky 9 where `adduser` is a useradd wrapper that
# doesn't accept Debian-style --gecos / --no-create-home flags.
if ! command -v useradd > /dev/null 2>&1; then
    echo "preinst: useradd not available — cannot create labdog user" >&2
    exit 1
fi

useradd \
    --system \
    --user-group \
    --no-create-home \
    --home-dir /var/lib/labdog \
    --shell /usr/sbin/nologin \
    --comment "LabDog service account" \
    labdog
