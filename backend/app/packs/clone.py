"""Run a git checkout without blocking the caller's event loop (BUG-71).

``app.actions.git_sync.sync_remote_pack`` shells out to git with a 120 s
timeout per invocation. Every caller of it is a coroutine, and two of
them are HTTP handlers in a single-worker API process, so a slow or
unreachable remote did not just make one request wait — it froze the
whole loop, ``/health`` and the terminal WebSocket included, for as long
as git took to give up.

The clone and the auth context it needs both live entirely inside the
worker thread. Nothing here touches the database: the ``learned``
host key comes back to the caller, which is on the loop and owns the
session, so the trust-on-first-use write happens where it should.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.actions.git_sync import sync_remote_pack
from app.packs.git_auth import git_auth_context


async def clone_to_thread(
    url: str,
    branch: str,
    dest: Path,
    *,
    ssh_key: str | None,
    token: str | None,
    host_key_entry: str | None,
) -> tuple[str, str | None]:
    """Check *url* out at *branch* into *dest*, off the event loop.

    Returns ``(head_sha, learned_host_key)``. ``learned_host_key`` is
    the entry to record for trust-on-first-use, or ``None`` when the
    host key was already pinned. Raises whatever ``sync_remote_pack``
    raises — ``GitSyncError`` or ``ValueError`` — unchanged.
    """

    def _blocking() -> tuple[str, str | None]:
        with git_auth_context(
            ssh_private_key=ssh_key, token=token, host_key_entry=host_key_entry
        ) as auth:
            return sync_remote_pack(url, branch, dest, auth=auth), auth.learned_host_key()

    return await asyncio.to_thread(_blocking)
