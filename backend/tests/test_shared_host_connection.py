"""BUG-159: one SSH connection per host for a preview or a collect.

Each module's collector opened its own connection, so a preview of one
host made eight handshakes (about 90 ms each on loopback, against 3 ms for
a command on an open connection), and a host that did not answer made the
preview wait out the connect timeout eight times over.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import asyncssh
import pytest

from app.ssh_utils import SharedHostConnection
from app.sync.plan import plan_host_modules
from tests.conftest import create_host, create_ssh_key


class _Conn:
    """Answers every command; counts how many run at once."""

    def __init__(self, *, max_sessions: int | None = None, delay: float = 0.01):
        self.max_sessions = max_sessions
        self.delay = delay
        self.open = 0
        self.peak = 0
        self.commands: list[str] = []

    async def run(self, command, check=False, **_kw):
        if self.max_sessions is not None and self.open >= self.max_sessions:
            # What OpenSSH sends past MaxSessions (serverloop.c).
            raise asyncssh.ChannelOpenError(asyncssh.OPEN_CONNECT_FAILED, "open failed")
        self.open += 1
        self.peak = max(self.peak, self.open)
        try:
            await asyncio.sleep(self.delay)
            self.commands.append(command)
            return SimpleNamespace(stdout=command, stderr="", exit_status=0)
        finally:
            self.open -= 1


class _Opener:
    def __init__(self, conn=None, error: Exception | None = None):
        self.conn = conn or _Conn()
        self.error = error
        self.opened = 0
        self.closed = 0

    @asynccontextmanager
    async def _cm(self):
        self.opened += 1
        if self.error is not None:
            raise self.error
        try:
            yield self.conn
        finally:
            self.closed += 1

    def __call__(self):
        return self._cm()


async def test_nothing_to_run_never_connects():
    opener = _Opener()
    shared = SharedHostConnection(opener)
    await shared.aclose()
    assert opener.opened == 0


async def test_every_command_shares_one_connection_closed_at_the_end():
    opener = _Opener()
    shared = SharedHostConnection(opener, max_channels=3)

    results = await asyncio.gather(*(shared.run(f"cmd {i}") for i in range(10)))
    await shared.aclose()

    assert [r.stdout for r in results] == [f"cmd {i}" for i in range(10)]
    assert (opener.opened, opener.closed) == (1, 1)
    # At most max_channels at a time, but more than one.
    assert 1 < opener.conn.peak <= 3


async def test_a_failed_connect_is_raised_to_every_caller_without_retrying():
    error = OSError("no route to host")
    opener = _Opener(error=error)
    shared = SharedHostConnection(opener)

    outcomes = await asyncio.gather(*(shared.run("x") for _ in range(5)), return_exceptions=True)

    assert outcomes == [error] * 5
    assert opener.opened == 1


async def test_a_host_with_fewer_sessions_falls_back_to_one_at_a_time():
    """``MaxSessions 2``: the third channel is refused. That command waits
    for the running ones and everything after it runs one at a time."""
    opener = _Opener(_Conn(max_sessions=2))
    shared = SharedHostConnection(opener, max_channels=4)

    results = await asyncio.gather(*(shared.run(f"cmd {i}") for i in range(8)))

    assert sorted(r.stdout for r in results) == sorted(f"cmd {i}" for i in range(8))
    assert shared._serial is True
    assert opener.conn.peak <= 2


async def test_a_refusal_with_nothing_else_running_is_raised():
    """Not the session limit, then: the server refuses sessions outright."""
    shared = SharedHostConnection(_Opener(_Conn(max_sessions=0)))

    with pytest.raises(asyncssh.ChannelOpenError):
        await shared.run("x")
    assert shared._serial is False


async def test_other_channel_errors_are_not_retried():
    class _Refusing(_Conn):
        async def run(self, command, check=False, **_kw):
            raise asyncssh.ChannelOpenError(asyncssh.OPEN_RESOURCE_SHORTAGE, "nope")

    shared = SharedHostConnection(_Opener(_Refusing()))

    outcomes = await asyncio.gather(*(shared.run("x") for _ in range(3)), return_exceptions=True)
    assert all(isinstance(o, asyncssh.ChannelOpenError) for o in outcomes)
    assert shared._serial is False


async def test_a_preview_collects_every_module_at_once_over_one_connection(db, monkeypatch):
    conn = _Conn()
    connects = []

    @asynccontextmanager
    async def _connect(host, db, client_keys=None):
        connects.append(host.id)
        yield conn

    monkeypatch.setattr("app.ssh_utils.ssh_connect_host", _connect)
    monkeypatch.setattr("app.ssh_utils.asyncssh.import_private_key", lambda pem: pem)

    from app.rules.model import ChainPolicies
    from app.sync.collector import CollectedFirewallState

    names = ["firewall", "services", "packages", "cron", "users", "groups"]
    arrived: list[str] = []
    everyone = asyncio.Event()
    seen_conns = []

    def _stub(name, result):
        # Firewall is called with keywords, the rest positionally.
        async def _collector(host, db=None, *args, conn=None, **kwargs):
            assert db is None, "collecting must not touch the session"
            seen_conns.append(conn)
            await conn.run(name)
            arrived.append(name)
            if len(arrived) == len(names):
                everyone.set()
            # Only returns once every collector has started: run one after
            # another, this would time out.
            await asyncio.wait_for(everyone.wait(), timeout=5)
            return result

        return _collector

    for target, name, result in (
        (
            "app.sync.collector.collect_firewall_state",
            "firewall",
            CollectedFirewallState(rules=[], policies=ChainPolicies()),
        ),
        ("app.services.collector.collect_service_states", "services", []),
        ("app.packages.collector.collect_package_states", "packages", []),
        ("app.cron.collector.collect_cron_jobs", "cron", []),
        ("app.user_mgmt.collector.collect_user_states", "users", []),
        ("app.user_mgmt.collector.collect_group_states", "groups", []),
    ):
        monkeypatch.setattr(target, _stub(name, result))

    key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=key.id)
    host.firewall_backend = "nftables"
    await db.flush()

    diffs = await plan_host_modules(host.id, None, db)

    assert [d.error for d in diffs] == [None] * len(diffs)
    assert sorted(arrived) == sorted(names)
    assert connects == [host.id]
    assert len({id(c) for c in seen_conns}) == 1


async def test_an_unreachable_host_fails_every_module_after_one_connect(db, monkeypatch):
    connects = []

    @asynccontextmanager
    async def _connect(host, db, client_keys=None):
        connects.append(host.id)
        raise OSError("connect timed out")
        yield  # pragma: no cover

    monkeypatch.setattr("app.ssh_utils.ssh_connect_host", _connect)
    monkeypatch.setattr("app.ssh_utils.asyncssh.import_private_key", lambda pem: pem)

    key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=key.id)
    host.firewall_backend = "nftables"
    await db.flush()

    diffs = await plan_host_modules(host.id, ["firewall", "services"], db)

    assert connects == [host.id]
    fw = next(d for d in diffs if d.module == "firewall")
    assert "connect timed out" in (fw.error or "")
    # The services collector reports an unreadable state per service on a
    # failed connection, as it did before; with none desired, nothing.
    assert next(d for d in diffs if d.module == "services").error is None
