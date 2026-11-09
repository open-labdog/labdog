"""Characterisation tests for ``collect_module_state`` and its collectors.

No test reached the body of the queued state collection: the BUG-74 tests
stop at the queue, and the firewall-detection tests stop at the ladder.
These drive it with SSH and every source-module collector stubbed, and
pin its exits (missing host or key, unknown module, a changed host key,
an unreachable host), what each collector reads from the host, how a
failing collector is recorded, and the competing-store notice, so a
restructuring of ``app.api.host_state`` has something to answer to.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.api.host_state import COLLECTABLE_MODULES, collect_module_state
from app.api.host_state import _run_inline_drift as _REAL_INLINE_DRIFT
from app.models.host import Host, SyncStatus
from app.models.host_module_status import HostModuleStatus
from app.ssh_utils import HostKeyMismatchError
from tests.conftest import create_host, create_ssh_key

pytestmark = pytest.mark.integration

ORDER = ["firewall", "service", "hosts_file", "linux_user", "cron", "package", "resolver"]

PASSWD = "root 0 /root /bin/bash\nalice 1000 /home/alice /bin/zsh\nbroken 1001\n"
GROUPS = "root 0\nstaff 1000\nlonely\n"
CRON_LISTING = "alice\n\n bob; rm -rf / \n"
CRONTABS = {
    "alice": "# comment\n\n*/5 * * * * /usr/bin/backup --all now\n@reboot short\n",
    "'bob; rm -rf /'": "0 1 * * 0 echo bob\n",
}


@dataclass
class _Rule:
    action: str
    port: int


class _Conn:
    """Answers the commands the user and cron collectors send."""

    def __init__(self, cron_listing: str = CRON_LISTING) -> None:
        self.cron_listing = cron_listing
        self.commands: list[str] = []

    async def run(self, command: str, check: bool = False):
        assert check is False
        self.commands.append(command)
        if command.startswith("getent passwd"):
            out = PASSWD
        elif command.startswith("getent group"):
            out = GROUPS
        elif command.startswith("ls /var/spool/cron"):
            out = self.cron_listing
        elif command.startswith("crontab -u "):
            user = command.removeprefix("crontab -u ").removesuffix(" -l 2>/dev/null || true")
            out = CRONTABS.get(user, "")
        else:
            raise AssertionError(f"unexpected command {command!r}")
        return SimpleNamespace(stdout=out)


class _World:
    def __init__(self) -> None:
        self.conn = _Conn()
        self.connect_error: BaseException | None = None
        self.connects = 0
        self.services = AsyncMock(return_value=[{"name": "nginx", "state": "running"}])
        self.hosts_file = AsyncMock(
            return_value=[SimpleNamespace(ip_address="10.0.0.5", hostname="db", aliases=["d"])]
        )
        self.effective_packages = AsyncMock(return_value=[SimpleNamespace(package_name="curl")])
        self.package_states = AsyncMock(return_value=[{"name": "curl", "installed": True}])
        self.repo_sources = AsyncMock(return_value=[{"uri": "http://deb.test"}])
        self.effective_resolver = AsyncMock(return_value=SimpleNamespace(resolver_type="resolved"))
        self.resolver_state = AsyncMock(return_value={"nameservers": ["1.1.1.1"]})
        self.rules = AsyncMock(return_value=[_Rule("allow", 22)])
        self.detect = AsyncMock(return_value=("nftables", ["picked nftables"]))
        self.competing = AsyncMock(return_value=None)
        self.drift = AsyncMock()

    @asynccontextmanager
    async def _connect(self, host, db, client_keys):  # noqa: ARG002
        self.connects += 1
        assert len(client_keys) == 1
        if self.connect_error is not None:
            raise self.connect_error
        yield self.conn

    @asynccontextmanager
    async def active(self):
        with (
            patch("app.api.host_state.ssh_connect_host", self._connect),
            patch("app.api.host_state.get_source_ip", AsyncMock(return_value="10.9.9.9")),
            patch("app.api.host_state._probe_competing_firewall_store", self.competing),
            patch("app.api.host_state._detect_firewall_backend", self.detect),
            patch("app.api.host_state._run_inline_drift", self.drift),
            patch("app.services.collector.list_all_services", self.services),
            patch("app.hosts_mgmt.collector.collect_hosts_file", self.hosts_file),
            patch("app.packages.merge.get_effective_packages", self.effective_packages),
            patch("app.packages.collector.collect_package_states", self.package_states),
            patch("app.packages.collector.collect_repo_sources", self.repo_sources),
            patch("app.resolver.merge.get_effective_resolver", self.effective_resolver),
            patch("app.resolver.collector.collect_resolver_state", self.resolver_state),
            patch("app.sync.collector.collect_current_rules", self.rules),
        ):
            yield


async def _host(db, **fields) -> Host:
    key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=key.id)
    for name, value in fields.items():
        setattr(host, name, value)
    await db.flush()
    await db.commit()
    return host


async def _hms(db, host_id) -> dict[str, HostModuleStatus]:
    rows = (
        (await db.execute(select(HostModuleStatus).where(HostModuleStatus.host_id == host_id)))
        .scalars()
        .all()
    )
    for row in rows:
        await db.refresh(row)
    return {row.module_type: row for row in rows}


async def test_a_missing_host_is_a_lookup_error(db):
    with pytest.raises(LookupError, match="^Host 987654 not found$"):
        await collect_module_state(987654, None, db)


async def test_a_host_without_a_key_is_a_lookup_error(db):
    host = await create_host(db)
    with pytest.raises(LookupError, match=f"^Host {host.id} has no SSH key assigned$"):
        await collect_module_state(host.id, None, db)


async def test_an_unknown_module_is_refused_before_connecting(db):
    host = await _host(db)
    world = _World()
    async with world.active():
        with pytest.raises(ValueError, match="^Unknown module: bogus$"):
            await collect_module_state(host.id, "bogus", db)
    assert world.connects == 0


async def test_every_collectable_module_has_a_collector(db):
    host = await _host(db)
    world = _World()
    async with world.active():
        states, _ = await collect_module_state(host.id, None, db)
    assert [s.module_type for s in states] == ORDER
    assert set(ORDER) == COLLECTABLE_MODULES


async def test_a_changed_host_key_propagates(db):
    host = await _host(db)
    world = _World()
    world.connect_error = HostKeyMismatchError("key changed")
    async with world.active():
        with pytest.raises(HostKeyMismatchError):
            await collect_module_state(host.id, None, db)
    assert await _hms(db, host.id) == {}


async def test_an_unreachable_host_marks_every_module_and_stops(db, caplog):
    host = await _host(db)
    world = _World()
    world.connect_error = OSError()

    with caplog.at_level(logging.WARNING, logger="app.api.host_state"):
        async with world.active():
            states, notices = await collect_module_state(host.id, None, db)

    assert notices == []
    assert world.connects == 1
    assert f"Host {host.id} unreachable, skipping all collectors: connection timed out" in (
        caplog.text
    )
    message = "Host unreachable: connection timed out"
    assert [(s.module_type, s.sync_status, s.error_message) for s in states] == [
        (m, "unknown", message) for m in ORDER
    ]
    assert all(s.warnings == [] and "warnings" not in s.model_fields_set for s in states)
    assert all(s.collected_at is not None for s in states)
    rows = await _hms(db, host.id)
    assert {m: (r.sync_status, r.error_message) for m, r in rows.items()} == {
        m: ("unknown", message) for m in ORDER
    }
    await db.refresh(host)
    assert host.sync_status == SyncStatus.error
    world.drift.assert_not_awaited()
    world.services.assert_not_awaited()


async def test_a_timeout_message_is_kept_when_it_has_one(db):
    host = await _host(db)
    world = _World()
    world.connect_error = TimeoutError("took too long")
    async with world.active():
        states, _ = await collect_module_state(host.id, "cron", db)
    assert [(s.module_type, s.error_message) for s in states] == [
        ("cron", "Host unreachable: took too long")
    ]


async def test_a_full_collection_reads_every_module(db, caplog):
    host = await _host(db, firewall_backend="nftables")
    world = _World()
    world.competing.return_value = "LabDog rules also found in iptables"

    with caplog.at_level(logging.INFO, logger="app.api.host_state"):
        async with world.active():
            states, notices = await collect_module_state(host.id, None, db)

    by_module = {s.module_type: s for s in states}
    assert [s.module_type for s in states] == ORDER
    assert all(s.sync_status == "collected" and s.error_message is None for s in states)
    assert by_module["linux_user"].collected_state == {
        "users": [
            {"username": "root", "uid": 0, "home": "/root", "shell": "/bin/bash"},
            {"username": "alice", "uid": 1000, "home": "/home/alice", "shell": "/bin/zsh"},
        ],
        "groups": [{"groupname": "root", "gid": 0}, {"groupname": "staff", "gid": 1000}],
    }
    assert by_module["cron"].collected_state == [
        {
            "user": "alice",
            "minute": "*/5",
            "hour": "*",
            "day": "*",
            "month": "*",
            "weekday": "*",
            "command": "/usr/bin/backup --all now",
        },
        {
            "user": "bob; rm -rf /",
            "minute": "0",
            "hour": "1",
            "day": "*",
            "month": "*",
            "weekday": "0",
            "command": "echo bob",
        },
    ]
    assert "crontab -u 'bob; rm -rf /' -l 2>/dev/null || true" in world.conn.commands
    assert by_module["service"].collected_state == [{"name": "nginx", "state": "running"}]
    assert by_module["hosts_file"].collected_state == [
        {"ip_address": "10.0.0.5", "hostname": "db", "aliases": ["d"]}
    ]
    assert by_module["package"].collected_state == {
        "packages": [{"name": "curl", "installed": True}],
        "repos": [{"uri": "http://deb.test"}],
    }
    assert world.package_states.await_args.args[3] == ["curl"]
    assert by_module["resolver"].collected_state == {"nameservers": ["1.1.1.1"]}
    assert world.resolver_state.await_args.kwargs == {"resolver_type": "resolved"}
    assert by_module["firewall"].collected_state == [{"action": "allow", "port": 22}]
    assert world.rules.await_args.args[3] == "nftables"
    world.detect.assert_not_awaited()

    warning = "LabDog rules also found in iptables"
    assert notices == [warning]
    assert by_module["firewall"].warnings == [warning]
    assert all(s.warnings == [] for m, s in by_module.items() if m != "firewall")
    assert world.competing.await_args.args[1] == "nftables"
    assert f"Host {host.id}: {warning}" in caplog.text
    assert list(world.drift.await_args.args[2]) == ORDER

    await db.refresh(host)
    assert host.labdog_source_ip == "10.9.9.9"
    rows = await _hms(db, host.id)
    assert set(rows) == set(ORDER)
    # One probe connection, then one each for the users and cron collectors.
    assert world.connects == 3


async def test_a_failing_collector_is_recorded_and_the_rest_still_run(db, caplog):
    host = await _host(db, firewall_backend="iptables")
    world = _World()
    world.services.side_effect = RuntimeError("systemctl exploded")

    with caplog.at_level(logging.WARNING, logger="app.api.host_state"):
        async with world.active():
            states, notices = await collect_module_state(host.id, None, db)

    by_module = {s.module_type: s for s in states}
    assert (by_module["service"].sync_status, by_module["service"].error_message) == (
        "error",
        "systemctl exploded",
    )
    assert by_module["service"].collected_state is None
    assert by_module["service"].collected_at is not None
    assert f"Collection failed for service on host {host.id}: systemctl exploded" in caplog.text
    assert by_module["cron"].sync_status == "collected"
    assert notices == []
    await db.refresh(host)
    assert host.sync_status == SyncStatus.error


async def test_one_module_collects_only_that_module(db):
    host = await _host(db, firewall_backend="nftables")
    world = _World()
    world.conn = _Conn(cron_listing="")

    async with world.active():
        states, notices = await collect_module_state(host.id, "cron", db)

    assert [s.module_type for s in states] == ["cron"]
    # An empty spool listing falls back to root's crontab.
    assert states[0].collected_state == []
    assert world.conn.commands[-1] == "crontab -u root -l 2>/dev/null || true"
    world.competing.assert_not_awaited()
    world.services.assert_not_awaited()
    assert list(await _hms(db, host.id)) == ["cron"]


async def test_an_unknown_backend_is_detected_first(db, caplog):
    host = await _host(db)
    world = _World()

    with caplog.at_level(logging.INFO, logger="app.api.host_state"):
        async with world.active():
            states, _ = await collect_module_state(host.id, "firewall", db)

    assert states[0].collected_state == [{"action": "allow", "port": 22}]
    assert world.rules.await_args.args[3] == "nftables"
    assert f"Host {host.id}: picked nftables" in caplog.text
    kwargs = world.detect.await_args.kwargs
    assert (kwargs["host_id"], kwargs["host"].id) == (host.id, host.id)
    assert world.detect.await_args.args[0] == host.ip_address
    await db.refresh(host)
    assert str(getattr(host.firewall_backend, "value", host.firewall_backend)) == "nftables"
    world.competing.assert_not_awaited()


async def test_no_detectable_backend_is_collected_as_an_error_payload(db, caplog):
    host = await _host(db)
    world = _World()
    world.detect.return_value = (None, ["no firewall binary"])

    with caplog.at_level(logging.INFO, logger="app.api.host_state"):
        async with world.active():
            states, _ = await collect_module_state(host.id, "firewall", db)

    assert states[0].sync_status == "collected"
    assert states[0].collected_state == {
        "error": "No supported firewall detected (nftables or iptables)"
    }
    assert "no firewall binary" not in caplog.text
    world.rules.assert_not_awaited()


async def test_packages_skip_the_state_probe_when_none_are_desired(db):
    host = await _host(db)
    world = _World()
    world.effective_packages.return_value = []
    world.effective_resolver.return_value = None

    async with world.active():
        await collect_module_state(host.id, "package", db)
        states, _ = await collect_module_state(host.id, "resolver", db)

    world.package_states.assert_not_awaited()
    rows = await _hms(db, host.id)
    assert rows["package"].collected_state == {
        "packages": [],
        "repos": [{"uri": "http://deb.test"}],
    }
    assert world.resolver_state.await_args.kwargs == {"resolver_type": "resolv_conf"}
    assert states[0].collected_state == {"nameservers": ["1.1.1.1"]}


async def test_inline_drift_settles_a_collected_module(db):
    """The real drift check runs on what was collected: with nothing desired,
    jobs LabDog does not manage are not drift."""
    host = await _host(db)
    world = _World()

    async with world.active():
        with patch("app.api.host_state._run_inline_drift", _REAL_INLINE_DRIFT):
            states, _ = await collect_module_state(host.id, "cron", db)

    assert states[0].sync_status == "in_sync"
    assert (await _hms(db, host.id))["cron"].sync_status == "in_sync"
