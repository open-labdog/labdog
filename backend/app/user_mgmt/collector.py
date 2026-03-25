"""Collect current Linux user and group states from remote hosts via SSH."""

import asyncio

import asyncssh

from app.ssh_utils import ssh_connect


async def collect_user_states(
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    usernames: list[str],
    ssh_user: str = "root",
) -> list[dict]:
    results: list[dict] = []

    try:
        private_key = asyncssh.import_private_key(private_key_pem)

        async def _run() -> list[dict]:
            async with ssh_connect(
                host_ip,
                port=ssh_port,
                username=ssh_user,
                client_keys=[private_key],
            ) as conn:
                for username in usernames:
                    try:
                        entry = await _collect_single_user(conn, username)
                        results.append(entry)
                    except Exception:
                        results.append(_absent_user(username))
            return results

        return await asyncio.wait_for(_run(), timeout=30.0)
    except Exception:
        return [_absent_user(u) for u in usernames]


async def _collect_single_user(
    conn: asyncssh.SSHClientConnection,
    username: str,
) -> dict:
    passwd_result = await conn.run(f"getent passwd {username}", check=False)
    if passwd_result.exit_status != 0:
        return _absent_user(username)

    # getent passwd format: username:x:uid:gid:comment:home:shell
    fields = (passwd_result.stdout or "").strip().split(":")
    if len(fields) < 7:
        return _absent_user(username)

    uid = int(fields[2])
    home_dir = fields[5]
    shell = fields[6]

    ak_result = await conn.run(
        f"cat {home_dir}/.ssh/authorized_keys 2>/dev/null", check=False
    )
    authorized_keys: list[str] = []
    if ak_result.exit_status == 0 and ak_result.stdout:
        for line in ak_result.stdout.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                authorized_keys.append(stripped)

    sudo_result = await conn.run(
        f"cat /etc/sudoers.d/{username} 2>/dev/null", check=False
    )
    sudo_rule: str | None = None
    if sudo_result.exit_status == 0 and sudo_result.stdout:
        content = sudo_result.stdout.strip()
        if content:
            sudo_rule = content

    # groups output format: "username : primary group1 group2" - first is primary, rest supplementary
    groups_result = await conn.run(f"groups {username} 2>/dev/null", check=False)
    supplementary_groups: list[str] = []
    if groups_result.exit_status == 0 and groups_result.stdout:
        stdout = groups_result.stdout.strip()
        if ":" in stdout:
            groups_part = stdout.split(":", 1)[1].strip()
            if groups_part:
                all_groups = groups_part.split()
                supplementary_groups = all_groups[1:] if len(all_groups) > 1 else []

    return {
        "username": username,
        "state": "present",
        "uid": uid,
        "shell": shell,
        "home_dir": home_dir,
        "authorized_keys": authorized_keys,
        "sudo_rule": sudo_rule,
        "supplementary_groups": supplementary_groups,
    }


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
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    groupnames: list[str],
    ssh_user: str = "root",
) -> list[dict]:
    results: list[dict] = []

    try:
        private_key = asyncssh.import_private_key(private_key_pem)

        async def _run() -> list[dict]:
            async with ssh_connect(
                host_ip,
                port=ssh_port,
                username=ssh_user,
                client_keys=[private_key],
            ) as conn:
                for groupname in groupnames:
                    try:
                        result = await conn.run(
                            f"getent group {groupname}", check=False
                        )
                        if result.exit_status != 0:
                            results.append(_absent_group(groupname))
                            continue

                        # getent group format: groupname:x:gid:members
                        fields = (result.stdout or "").strip().split(":")
                        if len(fields) < 3:
                            results.append(_absent_group(groupname))
                            continue

                        gid = int(fields[2])
                        results.append(
                            {
                                "groupname": groupname,
                                "state": "present",
                                "gid": gid,
                            }
                        )
                    except Exception:
                        results.append(_absent_group(groupname))
            return results

        return await asyncio.wait_for(_run(), timeout=30.0)
    except Exception:
        return [_absent_group(g) for g in groupnames]


def _absent_group(groupname: str) -> dict:
    return {
        "groupname": groupname,
        "state": "absent",
        "gid": None,
    }
