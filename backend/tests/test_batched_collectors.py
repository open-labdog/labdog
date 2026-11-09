"""BUG-158: the drift collectors ask once per module, not once per item.

Each of these used to run one remote command (or four) per package,
service, user or group: a round trip each. They now send one query for
all of them and match the answers back to the names. These tests pin the
parsing against output captured from real hosts (Debian 12, Rocky 9).
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from app.packages.collector import _package_manager, _query_installed
from app.services.collector import _service_states
from app.user_mgmt.collector import _group_states, _user_states


class _Conn:
    """Answers each command with the first handler whose prefix it starts with."""

    def __init__(self, handlers):
        self.handlers = handlers
        self.commands: list[str] = []

    async def run(self, command, check=False, **_kw):
        self.commands.append(command)
        for prefix, answer in self.handlers.items():
            if command.startswith(prefix):
                out = answer(command) if callable(answer) else answer
                if isinstance(out, tuple):
                    return SimpleNamespace(stdout=out[0], stderr="", exit_status=out[1])
                return SimpleNamespace(stdout=out, stderr="", exit_status=0)
        raise AssertionError(f"unexpected command: {command}")


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------

DPKG_QUERY = (
    "bash\tamd64\t5.2.15-2+b13\thi \n"
    "libc6\tamd64\t2.36-9+deb12u14\tii \n"
    "nginx\tamd64\t1.22.1-9\trc \n"
)


async def test_dpkg_reads_every_package_in_one_query():
    conn = _Conn({"if command -v dpkg-query": "dpkg\n", "dpkg-query -W": (DPKG_QUERY, 1)})

    manager = await _package_manager(conn)
    installed = await _query_installed(conn, manager, ["bash", "libc6:amd64", "nginx", "nope"])

    assert manager == "dpkg"
    # hi is a held package, still installed; rc left config files only.
    assert installed == {"bash": "5.2.15-2+b13", "libc6:amd64": "2.36-9+deb12u14"}
    assert len([c for c in conn.commands if c.startswith("dpkg-query")]) == 1
    assert "libc6:amd64" in conn.commands[-1]


async def test_rpm_reads_every_package_in_one_query():
    rpm = (
        "bash\tx86_64\t5.1.8-6.el9_1\n"
        "package nonexist.x86_64 is not installed\n"
        "glibc\tx86_64\t2.34-83.el9.7\n"
    )
    conn = _Conn({"if command -v dpkg-query": "rpm\n", "rpm -q --qf": (rpm, 1)})

    manager = await _package_manager(conn)
    installed = await _query_installed(conn, manager, ["bash", "nonexist.x86_64", "glibc.x86_64"])

    assert manager == "rpm"
    # Version-release without the arch, as the per-package parse gave.
    assert installed == {"bash": "5.1.8-6.el9_1", "glibc.x86_64": "2.34-83.el9.7"}


async def test_no_package_manager_means_nothing_installed():
    conn = _Conn({"if command -v dpkg-query": ""})

    assert await _query_installed(conn, await _package_manager(conn), ["bash"]) == {}


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

SHOW = (
    "ActiveState=active\nUnitFileState=enabled\n\n"
    "ActiveState=inactive\nUnitFileState=disabled\n\n"
    "ActiveState=inactive\nUnitFileState=\n"
)


async def test_services_come_from_one_systemctl_show():
    conn = _Conn({"systemctl show": SHOW})

    states = await _service_states(conn, ["nginx", "cron", "missing"])

    assert [(s.service_name, s.active_state, s.enabled) for s in states] == [
        ("nginx", "running", True),
        ("cron", "stopped", False),
        # A unit that does not exist: inactive, no unit file state.
        ("missing", "stopped", False),
    ]
    assert len(conn.commands) == 1


async def test_services_fall_back_one_by_one_when_systemctl_refuses_the_call():
    """A template name (``getty@``) makes ``systemctl show`` fail as a whole."""
    conn = _Conn(
        {
            "systemctl show": ("ActiveState=inactive\nUnitFileState=static\n", 1),
            "systemctl is-active 'getty@'": ("unknown\n", 4),
            "systemctl is-active nginx": "active\n",
            "systemctl is-enabled nginx": "enabled\n",
        }
    )

    states = await _service_states(conn, ["nginx", "getty@"])

    assert [(s.service_name, s.active_state, s.enabled) for s in states] == [
        ("nginx", "running", True),
        ("getty@", "error", False),
    ]


async def test_no_services_runs_nothing():
    conn = _Conn({})

    assert await _service_states(conn, []) == []
    assert conn.commands == []


# ---------------------------------------------------------------------------
# Users and groups
# ---------------------------------------------------------------------------

PASSWD = "root:x:0:0:root:/root:/bin/bash\nalice:x:1000:1000::/home/alice:/bin/sh\n"


def _files_script(command: str) -> str:
    """Play the remote side of the per-user files script."""
    marker = re.search(r"LABDOG-[0-9a-f]{16}", command).group(0)
    return (
        f"\n{marker} keys root\n"
        "ssh-ed25519 AAAA key1\n# a comment\n\nssh-rsa BBBB key2"
        f"\n{marker} sudo root\n"
        f"\n{marker} groups root\n"
        "root : root\n"
        f"\n{marker} keys alice\n"
        # A file that tries to pass for the next section, with a guess at the
        # marker: only the real token counts.
        "LABDOG-0000000000000000 sudo alice\n"
        f"\n{marker} sudo alice\n"
        "alice ALL=(ALL) ALL\n"
        f"\n{marker} groups alice\n"
        "alice : alice adm sudo\n"
    )


async def test_users_come_from_two_commands():
    conn = _Conn({"getent passwd": (PASSWD, 2), "printf": _files_script})

    users = await _user_states(conn, ["root", "alice", "ghost"])

    assert users == [
        {
            "username": "root",
            "state": "present",
            "uid": 0,
            "shell": "/bin/bash",
            "home_dir": "/root",
            "authorized_keys": ["ssh-ed25519 AAAA key1", "ssh-rsa BBBB key2"],
            "sudo_rule": None,
            "supplementary_groups": [],
        },
        {
            "username": "alice",
            "state": "present",
            "uid": 1000,
            "shell": "/bin/sh",
            "home_dir": "/home/alice",
            "authorized_keys": ["LABDOG-0000000000000000 sudo alice"],
            "sudo_rule": "alice ALL=(ALL) ALL",
            "supplementary_groups": ["adm", "sudo"],
        },
        {
            "username": "ghost",
            "state": "absent",
            "uid": None,
            "shell": None,
            "home_dir": None,
            "authorized_keys": [],
            "sudo_rule": None,
            "supplementary_groups": [],
        },
    ]
    assert len(conn.commands) == 2


async def test_a_home_directory_is_quoted_into_the_script():
    passwd = "eve:x:1001:1001::/home/eve; touch /pwned:/bin/sh\n"
    conn = _Conn({"getent passwd": passwd, "printf": lambda c: ""})

    await _user_states(conn, ["eve"])

    assert "cat '/home/eve; touch /pwned'/.ssh/authorized_keys" in conn.commands[1]


async def test_no_present_users_skips_the_files_script():
    conn = _Conn({"getent passwd": ("", 2)})

    users = await _user_states(conn, ["ghost"])

    assert users[0]["state"] == "absent"
    assert len(conn.commands) == 1


async def test_groups_come_from_one_getent():
    conn = _Conn({"getent group": ("ops:x:1001:\nsudo:x:27:alice\n", 2)})

    groups = await _group_states(conn, ["ops", "sudo", "nope"])

    assert groups == [
        {"groupname": "ops", "state": "present", "gid": 1001},
        {"groupname": "sudo", "state": "present", "gid": 27},
        {"groupname": "nope", "state": "absent", "gid": None},
    ]
    assert conn.commands == ["getent group ops sudo nope"]


async def test_group_names_are_quoted():
    conn = _Conn({"getent group": ("", 2)})

    await _group_states(conn, ["ops; reboot"])

    assert conn.commands == ["getent group 'ops; reboot'"]
