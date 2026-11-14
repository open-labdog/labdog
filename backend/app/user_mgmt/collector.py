"""Collect current Linux user and group states from remote hosts via SSH."""

import asyncio
import secrets
import shlex
from typing import TYPE_CHECKING

import asyncssh

from app.ssh_utils import host_connection

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host


#: Sections ``_read_user_files`` adds for a file it could not read.
_FILE_NAMES = {"x-keys": "authorized_keys", "x-sudo": "sudoers drop-in"}


class UserCollectionError(RuntimeError):
    """A user's keys or sudoers drop-in could not be read, as opposed to
    there being none (BUG-172)."""


async def collect_user_states(
    host: "Host",
    db: "AsyncSession",
    private_key_pem: str,
    usernames: list[str],
    conn=None,
) -> list[dict]:
    """The state of each user in ``usernames``, absent where it does not exist.

    Two remote commands whatever the number of users (BUG-158): one
    ``getent passwd`` for all of them, then one script that prints each
    present user's ``authorized_keys``, sudoers drop-in and groups between
    markers. It used to run four commands per user, a round trip each.

    ``conn``: an open connection to use (see ``app.ssh_utils.host_connection``).
    """
    if not usernames:
        return []
    try:

        async def _run() -> list[dict]:
            async with host_connection(host, db, private_key_pem, conn) as c:
                return await _user_states(c, usernames)

        return await asyncio.wait_for(_run(), timeout=30.0)
    except UserCollectionError:
        # BUG-172: an unreadable file is not an empty one. Reporting it as
        # empty made every managed key and sudo rule look missing.
        raise
    except Exception:
        return [_absent_user(u) for u in usernames]


async def _user_states(
    # A BoundedConnection from app.ssh_utils in practice — it delegates
    # every attribute except run(), which carries a deadline.
    conn: asyncssh.SSHClientConnection,
    usernames: list[str],
) -> list[dict]:
    names = " ".join(shlex.quote(u) for u in usernames)
    # Exit 2 means some name was not found; the found ones are still printed.
    passwd = await conn.run(f"getent passwd {names}", check=False)
    accounts = _parse_passwd(passwd.stdout or "")
    present = [u for u in dict.fromkeys(usernames) if u in accounts]
    sections = await _read_user_files(conn, present, accounts) if present else {}
    unreadable = sorted(
        f"{_FILE_NAMES[what]} of {user!r}" for what, user in sections if what in _FILE_NAMES
    )
    if unreadable:
        raise UserCollectionError(
            f"could not read the {', '.join(unreadable)}: unbork's SSH user needs to be "
            "root or have passwordless sudo"
        )

    results = []
    for username in usernames:
        account = accounts.get(username)
        if account is None:
            results.append(_absent_user(username))
            continue
        uid, home_dir, shell = account
        results.append(
            {
                "username": username,
                "state": "present",
                "uid": uid,
                "shell": shell,
                "home_dir": home_dir,
                "authorized_keys": _parse_authorized_keys(sections.get(("keys", username), [])),
                "sudo_rule": "\n".join(sections.get(("sudo", username), [])).strip() or None,
                "supplementary_groups": _parse_groups_output(
                    sections.get(("groups", username), [])
                ),
            }
        )
    return results


def _parse_passwd(output: str) -> dict[str, tuple[int, str, str]]:
    """``getent passwd`` lines → ``{username: (uid, home, shell)}``.

    Format: ``username:x:uid:gid:comment:home:shell``. A line that does not
    have seven fields, or whose uid is not a number, is skipped, so that
    user reads as absent, as it did when it was looked up on its own.
    """
    accounts: dict[str, tuple[int, str, str]] = {}
    for line in output.splitlines():
        fields = line.strip().split(":")
        if len(fields) < 7:
            continue
        try:
            uid = int(fields[2])
        except ValueError:
            continue
        accounts.setdefault(fields[0], (uid, fields[5], fields[6]))
    return accounts


async def _read_user_files(
    conn: asyncssh.SSHClientConnection,
    usernames: list[str],
    accounts: dict[str, tuple[int, str, str]],
) -> dict[tuple[str, str], list[str]]:
    """Each user's keys, sudoers drop-in and ``groups`` line, in one command.

    The output is split on a marker line carrying a random token, so
    nothing a file contains can pass for another section. Each marker
    starts with a newline, in case the file before it does not end in one.

    The key file is ``0600`` in a home that is often ``0700``, and sudoers
    drop-ins are ``0440 root``, so a non-root SSH user cannot see them.
    Each file is tested and read through ``sudo -n`` unless already root
    (a root-only host may have no sudo at all), as the cron collector does
    for ``crontab -l`` (BUG-130). A missing file is an empty section; one
    that exists but cannot be read, or a test that cannot run because sudo
    refuses, adds an ``x-keys`` / ``x-sudo`` section, which the caller
    reports as an error (BUG-172).
    """
    marker = f"LABDOG-{secrets.token_hex(8)}"
    # `test -e` exits 1 for a missing file; sudo exits 1 too when it
    # refuses, so the status alone cannot tell them apart. `sudo -n true`
    # once up front decides whether sudo works at all.
    steps = [
        'if [ "$(id -u)" = 0 ]; then S=; '
        "elif sudo -n true 2>/dev/null; then S='sudo -n'; else S=none; fi",
    ]

    def read(path: str, section: str, user: str) -> str:
        return (
            f"if [ \"$S\" = none ]; then printf '\\n%s x-{section} %s\\n' {marker} {user}; "
            f"elif $S test -e {path}; then $S cat -- {path} 2>/dev/null "
            f"|| printf '\\n%s x-{section} %s\\n' {marker} {user}; fi"
        )

    for username in usernames:
        user = shlex.quote(username)
        # home_dir comes from the remote host's getent output — quote it so a
        # compromised host can't inject shell metacharacters via the home path.
        home = shlex.quote(accounts[username][1].rstrip("/") + "/.ssh/authorized_keys")
        steps.extend(
            [
                f"printf '\\n%s keys %s\\n' {marker} {user}",
                read(home, "keys", user),
                f"printf '\\n%s sudo %s\\n' {marker} {user}",
                read(shlex.quote(f"/etc/sudoers.d/{username}"), "sudo", user),
                f"printf '\\n%s groups %s\\n' {marker} {user}",
                f"groups {user} 2>/dev/null",
            ]
        )
    result = await conn.run("; ".join(steps) + "; true", check=False)

    sections: dict[tuple[str, str], list[str]] = {}
    current: list[str] | None = None
    for line in (result.stdout or "").splitlines():
        if line.startswith(marker + " "):
            _, section, username = line.split(" ", 2)
            current = sections.setdefault((section, username), [])
        elif current is not None:
            current.append(line)
    return sections


def _parse_authorized_keys(lines: list[str]) -> list[str]:
    """Non-empty, non-comment lines, stripped."""
    return [s for s in (line.strip() for line in lines) if s and not s.startswith("#")]


def _parse_groups_output(lines: list[str]) -> list[str]:
    """``groups`` prints ``username : primary group1 group2``; the first group
    is the primary one, the rest are supplementary."""
    stdout = "\n".join(lines).strip()
    if ":" not in stdout:
        return []
    groups_part = stdout.split(":", 1)[1].strip()
    all_groups = groups_part.split()
    return all_groups[1:] if len(all_groups) > 1 else []


def _absent_user(username: str) -> dict:
    return {
        "username": username,
        "state": "absent",
        "uid": None,
        "shell": None,
        "home_dir": None,
        "authorized_keys": [],
        "sudo_rule": None,
        "supplementary_groups": [],
    }


async def collect_group_states(
    host: "Host",
    db: "AsyncSession",
    private_key_pem: str,
    groupnames: list[str],
    conn=None,
) -> list[dict]:
    """The state of each group in ``groupnames``: one ``getent group`` for all
    of them (BUG-158; it used to be one per group)."""
    if not groupnames:
        return []
    try:

        async def _run() -> list[dict]:
            async with host_connection(host, db, private_key_pem, conn) as c:
                return await _group_states(c, groupnames)

        return await asyncio.wait_for(_run(), timeout=30.0)
    except Exception:
        return [_absent_group(g) for g in groupnames]


async def _group_states(conn: asyncssh.SSHClientConnection, groupnames: list[str]) -> list[dict]:
    # Quoted: the schema's name check was all that kept this safe before.
    names = " ".join(shlex.quote(g) for g in groupnames)
    result = await conn.run(f"getent group {names}", check=False)
    gids: dict[str, int] = {}
    for line in (result.stdout or "").splitlines():
        # getent group format: groupname:x:gid:members
        fields = line.strip().split(":")
        if len(fields) < 3:
            continue
        try:
            gids.setdefault(fields[0], int(fields[2]))
        except ValueError:
            continue
    return [
        {"groupname": g, "state": "present", "gid": gids[g]} if g in gids else _absent_group(g)
        for g in groupnames
    ]


def _absent_group(groupname: str) -> dict:
    return {
        "groupname": groupname,
        "state": "absent",
        "gid": None,
    }
