"""Collect current Ansible-managed cron jobs from remote hosts via SSH."""

import asyncio
import re
import shlex
from typing import TYPE_CHECKING

import asyncssh

from app.ssh_utils import ssh_connect_host

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host

_ANSIBLE_MARKER_RE = re.compile(r"^#Ansible:\s+(.+)$")


class CronCollectionError(RuntimeError):
    """A user's crontab could not be read, as opposed to being empty."""


async def collect_cron_jobs(
    host: "Host",
    db: "AsyncSession",
    private_key_pem: str,
    users: list[str],
) -> list[dict]:
    """SSH into a host and parse Ansible-managed crontab entries for each user.

    Only entries preceded by an ``#Ansible: <name>`` marker comment are
    collected.  Non-managed entries are ignored.

    Returns a list of dicts with keys: name, user, schedule, command.
    """
    results: list[dict] = []

    try:
        private_key = asyncssh.import_private_key(private_key_pem)

        async def _run() -> list[dict]:
            async with ssh_connect_host(host, db, client_keys=[private_key]) as conn:
                for user in users:
                    results.extend(await _collect_user_crontab(conn, user))
            return results

        return await asyncio.wait_for(_run(), timeout=30.0)
    except CronCollectionError:
        # BUG-130: an unreadable crontab is not an empty one. Reporting it
        # as empty made every managed entry look missing.
        raise
    except Exception:
        return []


async def _collect_user_crontab(
    # A BoundedConnection from app.ssh_utils in practice — it delegates
    # every attribute except run(), which carries a deadline.
    conn: asyncssh.SSHClientConnection,
    user: str,
) -> list[dict]:
    # BUG-130: `crontab -l -u` needs root. As a non-root SSH user it failed
    # with "must be privileged to use -u", which read as an empty crontab,
    # so cron drift was always out_of_sync. Go through `sudo -n` unless
    # already root (a root-only host may have no sudo at all).
    quoted = shlex.quote(user)
    result = await conn.run(
        f'if [ "$(id -u)" = 0 ]; then crontab -l -u {quoted}; '
        f"else sudo -n crontab -l -u {quoted}; fi",
        check=False,
    )

    if result.exit_status != 0:
        stderr = result.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        if "no crontab for" in stderr:
            return []
        reason = stderr.strip() or f"exit status {result.exit_status}"
        raise CronCollectionError(f"could not read the crontab of {user!r}: {reason}")

    stdout = result.stdout
    if stdout is None:
        return []
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")

    if not stdout.strip():
        return []

    return _parse_crontab(stdout, user)


def _parse_crontab(raw: str, user: str) -> list[dict]:
    """Parse crontab output for ``#Ansible:`` marker blocks.

    Algorithm:
    1. Split into lines.
    2. When a line matches ``#Ansible: <name>``, the NEXT non-empty,
       non-comment line is the cron entry.
    3. The cron entry is split into 6 parts: 5 schedule fields + command.
    """
    lines = raw.splitlines()
    entries: list[dict] = []
    pending_name: str | None = None

    for line in lines:
        stripped = line.strip()

        marker_match = _ANSIBLE_MARKER_RE.match(stripped)
        if marker_match:
            pending_name = marker_match.group(1).strip()
            continue

        if pending_name is not None:
            if not stripped or stripped.startswith("#"):
                continue

            entry = _parse_cron_line(stripped, pending_name, user)
            if entry is not None:
                entries.append(entry)
            pending_name = None

    return entries


def _parse_cron_line(line: str, name: str, user: str) -> dict | None:
    parts = line.split(None, 5)
    if len(parts) < 6:
        return None

    schedule = " ".join(parts[:5])
    command = parts[5]

    return {
        "name": name,
        "user": user,
        "schedule": schedule,
        "command": command,
    }
