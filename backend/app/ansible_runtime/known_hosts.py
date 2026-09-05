"""SEC-26: give the Ansible path the same host-key pinning as asyncssh.

LabDog already does real TOFU-with-pinning on its asyncssh connections:
the first successful connect records the server's public key on
``Host.ssh_host_key_entry`` and every later connect verifies against it
(``app/ssh_utils.py``). The Ansible path did none of that. It set
``StrictHostKeyChecking=accept-new`` with no ``UserKnownHostsFile``, so
each playbook run started from an empty known-hosts file and accepted
whatever key was presented — meaning a man-in-the-middle the web
terminal refuses was silently accepted by the pipeline that pushes
root-level configuration.

This module materialises the stored entry into a known-hosts file next
to the private key the run already writes, so it shares that file's
lifecycle: same tmpfs directory, removed by the same cleanup. The
inventory then points ``UserKnownHostsFile`` at it and asks for
``StrictHostKeyChecking=yes``, which refuses both a mismatch and an
unknown host.

When the host has no stored key yet (first contact, or a host only ever
reached through Ansible) there is nothing to pin to and the caller falls
back to ``accept-new`` — the same posture as before. That is deliberate:
refusing would make a brand-new host unmanageable, and the asyncssh
paths (preflight, fact collection, drift checks) reach every managed
host and record the key, so the unpinned window is the first connection
only.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def known_hosts_path_for(ssh_key_path: str) -> str:
    """The known-hosts path that belongs to *ssh_key_path*.

    Derived rather than allocated so that cleanup, which already knows
    the key path, can find the file without extra bookkeeping.
    """
    return f"{ssh_key_path}.known_hosts"


def write_known_hosts(host_key_entry: str | None, ssh_key_path: str) -> str | None:
    """Write *host_key_entry* beside the key file; return the path.

    Returns ``None`` when there is no entry to pin to, which the
    inventory builders read as "fall back to ``accept-new``".

    ``O_NOFOLLOW`` mirrors the private-key write in
    ``app/sync/orchestrator.py``: the containing directory is 0700 and
    owned by the worker, but a symlink appearing here should fail loudly
    rather than have us write through it.
    """
    if not host_key_entry or not host_key_entry.strip():
        return None
    path = known_hosts_path_for(ssh_key_path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, host_key_entry.strip().encode() + b"\n")
    finally:
        os.close(fd)
    return path


def remove_known_hosts(ssh_key_path: str | None) -> None:
    """Best-effort removal of the file :func:`write_known_hosts` made.

    A public key is not a secret, so this is hygiene rather than
    containment — but the file lives in tmpfs and nothing else would
    ever clean it up on the paths that unlink the key individually.
    """
    if not ssh_key_path:
        return
    path = known_hosts_path_for(ssh_key_path)
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        logger.debug("could not remove known_hosts file %s", path, exc_info=True)
