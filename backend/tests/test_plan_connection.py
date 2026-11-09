"""BUG-153: previewing hosts must not hold a database connection over SSH.

The Plans page sends one preview per host in scope, and each held the
request's pooled connection while it read the host over SSH: up to a
connect timeout per module on a host that does not answer. Fifteen such
hosts took every connection in the default pool. The group plan did the
same, one member after another inside one request.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.sync import PreviewRequest, plan_group, preview_host
from app.models.host import Host, HostGroupMembership
from app.models.host_group import HostGroup
from app.models.ssh_key import SSHKey
from app.rules.model import ChainPolicies
from app.sync.collector import CollectedFirewallState
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


class _Conn:
    async def run(self, command, check=False, **_kw):
        return SimpleNamespace(stdout="", stderr="", exit_status=0)


@pytest.fixture
async def pool_of_one(pg_url):
    """An engine with a single connection, and rows committed through it
    (a savepoint-wrapped test session would be invisible to it)."""
    from app.models import import_all_models

    import_all_models()
    engine = create_async_engine(pg_url, pool_size=1, max_overflow=0, pool_timeout=2)
    made: dict[str, list[int]] = {"hosts": [], "keys": [], "groups": []}

    async def make_hosts(count: int, *, group: bool = False) -> list[int]:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            key = await create_ssh_key(s)
            made["keys"].append(key.id)
            group_ids = None
            if group:
                g = await create_group(s)
                made["groups"].append(g.id)
                group_ids = [g.id]
            for _ in range(count):
                host = await create_host(s, ssh_key_id=key.id, group_ids=group_ids)
                host.firewall_backend = "nftables"
                made["hosts"].append(host.id)
            await s.commit()
        return list(made["hosts"])

    yield engine, make_hosts, made

    async with engine.begin() as conn:
        await conn.execute(
            delete(HostGroupMembership).where(HostGroupMembership.c.host_id.in_(made["hosts"]))
        )
        await conn.execute(delete(Host).where(Host.id.in_(made["hosts"])))
        await conn.execute(delete(HostGroup).where(HostGroup.id.in_(made["groups"])))
        await conn.execute(delete(SSHKey).where(SSHKey.id.in_(made["keys"])))
    await engine.dispose()


async def _another_request_gets_a_connection(engine) -> bool:
    async with AsyncSession(engine) as other:
        return await asyncio.wait_for(other.scalar(select(1)), timeout=5) == 1


async def test_a_preview_reads_the_host_with_no_connection_checked_out(pool_of_one, monkeypatch):
    engine, make_hosts, _ = pool_of_one
    [host_id] = await make_hosts(1)
    seen: dict[str, object] = {}

    @asynccontextmanager
    async def _connect(host, db, client_keys=None):
        yield _Conn()

    async def _services(host, db, pem, names, conn=None):
        await conn.run("systemctl show")
        seen["checked_out"] = engine.pool.checkedout()
        seen["other"] = await _another_request_gets_a_connection(engine)
        return []

    monkeypatch.setattr("app.ssh_utils.ssh_connect_host", _connect)
    monkeypatch.setattr("app.ssh_utils.asyncssh.import_private_key", lambda pem: pem)
    monkeypatch.setattr("app.services.collector.collect_service_states", _services)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        diffs = await preview_host(
            host_id, PreviewRequest(module_filter=["services"]), None, session
        )

    assert [d.error for d in diffs] == [None]
    assert seen == {"checked_out": 0, "other": True}


async def test_a_group_plan_reads_its_members_at_once_with_no_connection(pool_of_one, monkeypatch):
    engine, make_hosts, made = pool_of_one
    host_ids = await make_hosts(3, group=True)
    arrived: list[int] = []
    everyone = asyncio.Event()
    checked_out: list[int] = []

    async def _firewall(host, db, private_key_pem, firewall_backend, conn=None):
        assert db is None
        checked_out.append(engine.pool.checkedout())
        # As TOFU does on a first connect without a session.
        host.ssh_host_key_entry = f"{host.ip_address} ssh-ed25519 AAAA{host.id}"
        arrived.append(host.id)
        if len(arrived) == len(host_ids):
            everyone.set()
        # Run one after another, this would time out.
        await asyncio.wait_for(everyone.wait(), timeout=5)
        return CollectedFirewallState(rules=[], policies=ChainPolicies())

    monkeypatch.setattr("app.sync.collector.collect_firewall_state", _firewall)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        diffs = await plan_group(made["groups"][0], None, session)

    assert sorted(d.host_id for d in diffs) == sorted(host_ids)
    assert all(d.error is None for d in diffs)
    assert checked_out == [0, 0, 0]
    # The host keys seen for the first time were still recorded.
    async with AsyncSession(engine) as s:
        keys = (
            await s.execute(select(Host.ssh_host_key_entry).where(Host.id.in_(host_ids)))
        ).scalars()
        assert all(k and k.endswith(tuple(f"AAAA{h}" for h in host_ids)) for k in keys)


async def test_a_group_plan_reports_an_unreachable_member_and_keeps_the_rest(
    pool_of_one, monkeypatch
):
    engine, make_hosts, made = pool_of_one
    host_ids = await make_hosts(2, group=True)

    async def _firewall(host, db, private_key_pem, firewall_backend, conn=None):
        if host.id == host_ids[0]:
            raise OSError("connect timed out")
        return CollectedFirewallState(rules=[], policies=ChainPolicies())

    monkeypatch.setattr("app.sync.collector.collect_firewall_state", _firewall)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        diffs = await plan_group(made["groups"][0], None, session)

    by_host = {d.host_id: d for d in diffs}
    assert by_host[host_ids[0]].error == "Cannot reach host: connect timed out"
    assert by_host[host_ids[1]].error is None
