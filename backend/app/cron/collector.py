"""Collect current Ansible-managed cron jobs from remote hosts via SSH."""

import asyncio
import re

import asyncssh

_ANSIBLE_MARKER_RE = re.compile(r"^#Ansible:\s+(.+)$")


async def collect_cron_jobs(
    host_ip: str,
    ssh_port: int,
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
            async with asyncssh.connect(
                host_ip,
                port=ssh_port,
                username="root",
                client_keys=[private_key],
                known_hosts=None,
            ) as conn:
                for user in users:
                    try:
                        entries = await _collect_user_crontab(conn, user)
                        results.extend(entries)
                    except Exception:
                        pass
            return results

        return await asyncio.wait_for(_run(), timeout=30.0)
    except Exception:
        return []


async def _collect_user_crontab(
    conn: asyncssh.SSHClientConnection,
    user: str,
) -> list[dict]:
    result = await conn.run(f"crontab -l -u {user} 2>/dev/null", check=False)

    if result.exit_status != 0:
        return []

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
