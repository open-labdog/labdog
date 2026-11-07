#!/bin/sh
# Generate the SSH key and stack secrets once, then build the fake-host image.
set -eu
cd "$(dirname "$0")"
mkdir -p .state
[ -f .state/id_ed25519 ] || ssh-keygen -q -t ed25519 -N '' -C labdog-loadtest -f .state/id_ed25519
if [ ! -f .env ]; then
    umask 077
    cat > .env <<ENV
POSTGRES_PASSWORD=$(openssl rand -hex 16)
LABDOG_SECURITY__SECRET_KEY=$(openssl rand -hex 64)
LABDOG_SECURITY__ENCRYPTION_KEY=$(openssl rand -base64 32)
ENV
fi
if [ ! -d .state/collections/ansible_collections/ansible/posix ]; then
    mkdir -p .state/collections && chmod 777 .state/collections
    docker run --rm --entrypoint ansible-galaxy -v "$PWD/.state/collections:/c" \
        "${LABDOG_IMAGE:-openlabdog/labdog:dev-5cb85d7}" collection install ansible.posix -p /c
fi
docker compose build fakehost
