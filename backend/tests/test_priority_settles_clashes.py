"""BUG-57: a clash is settled by priority, at every level.

LabDog's merge model is priority-ordered: groups are walked highest
priority first, and the first entry to claim a merge key wins. The
per-entry ``priority`` column was not part of that. BUG-69 wired it in
for group-level entries — those reads are ordered ``priority DESC,
id ASC`` and the loop is first-wins — but the host-override loops
assigned unconditionally over that same ordered read, so among two host
entries with the same key the *last* one seen won, which is the one with
the **lowest** priority. Before BUG-69 that was merely undefined; after
it, it was reliably backwards.

``/etc/hosts`` is the one module where the field means more than a
tie-break: the file is read top to bottom and the first line matching a
name wins, so priority decides which of two entries sharing a hostname
resolves.
"""

import uuid

import pytest

from tests.conftest import create_group, create_host

pytestmark = pytest.mark.integration


def _name() -> str:
    return f"g-{uuid.uuid4().hex[:8]}"


class TestHostOverridesAreSettledByPriority:
    async def test_cron(self, db):
        from app.cron.merge import get_effective_cron_jobs
        from app.cron.models import CronJob

        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        for command, priority in (("loser", 1), ("winner", 9)):
            db.add(
                CronJob(
                    host_id=host.id,
                    name="backup",
                    user="root",
                    schedule="0 3 * * *",
                    command=command,
                    priority=priority,
                )
            )
            await db.flush()

        jobs = await get_effective_cron_jobs(host.id, db)
        assert [j.command for j in jobs] == ["winner"]

    async def test_services(self, db):
        from app.services.merge import get_effective_services
        from app.services.models import ServiceRule

        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        for enabled, priority in ((False, 1), (True, 9)):
            db.add(
                ServiceRule(
                    host_id=host.id, service_name="nginx", enabled=enabled, priority=priority
                )
            )
            await db.flush()

        services = await get_effective_services(host.id, db)
        assert [s.enabled for s in services] == [True]

    async def test_packages(self, db):
        from app.packages.merge import get_effective_packages
        from app.packages.models import PackageRule

        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        # ``uq_package_rules_host_pkg`` forbids two rows for one package on
        # one host, so the clash this settles cannot be created here. The
        # single row must still survive the loop.
        db.add(PackageRule(host_id=host.id, package_name="nginx", priority=5))
        await db.flush()

        pkgs = await get_effective_packages(host.id, db)
        assert [p.package_name for p in pkgs] == ["nginx"]

    async def test_linux_users(self, db):
        from app.user_mgmt.merge import get_effective_users
        from app.user_mgmt.models import LinuxUser

        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        for shell, priority in (("/bin/sh", 1), ("/bin/bash", 9)):
            db.add(LinuxUser(host_id=host.id, username="deploy", shell=shell, priority=priority))
            await db.flush()

        users = await get_effective_users(host.id, db)
        assert [u.shell for u in users] == ["/bin/bash"]

    async def test_linux_groups(self, db):
        from app.user_mgmt.merge import get_effective_groups
        from app.user_mgmt.models import LinuxGroup

        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        for gid, priority in ((5001, 1), (5002, 9)):
            db.add(LinuxGroup(host_id=host.id, groupname="deploy", gid=gid, priority=priority))
            await db.flush()

        groups = await get_effective_groups(host.id, db)
        assert [g.gid for g in groups] == [5002]

    async def test_hosts_entries(self, db):
        from app.hosts_mgmt.merge import get_effective_hosts_entries
        from app.hosts_mgmt.models import HostsEntry

        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        for hostname, priority in (("loser", 1), ("winner", 9)):
            db.add(
                HostsEntry(
                    host_id=host.id, ip_address="10.5.0.1", hostname=hostname, priority=priority
                )
            )
            await db.flush()

        entries = await get_effective_hosts_entries(host.id, db)
        match = [e for e in entries if e.ip_address == "10.5.0.1"]
        assert [e.hostname for e in match] == ["winner"]


class TestScopeStillBeatsPriority:
    async def test_a_host_override_wins_however_low_its_priority(self, db):
        """Host-over-group is scope, not a clash. A group entry cannot
        outrank an override by carrying a bigger number."""
        from app.cron.merge import get_effective_cron_jobs
        from app.cron.models import CronJob

        group = await create_group(db, name=_name())
        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}", group_ids=[group.id])
        db.add(
            CronJob(
                group_id=group.id,
                name="backup",
                user="root",
                schedule="0 3 * * *",
                command="from-group",
                priority=10000,
            )
        )
        db.add(
            CronJob(
                host_id=host.id,
                name="backup",
                user="root",
                schedule="0 3 * * *",
                command="from-host",
                priority=0,
            )
        )
        await db.flush()

        jobs = await get_effective_cron_jobs(host.id, db)
        assert [j.command for j in jobs] == ["from-host"]


class TestTheHostsFileIsOrderedByPriority:
    """The file is read top to bottom and the first match for a name wins,
    so order is the behaviour, not presentation."""

    async def test_the_higher_priority_entry_is_emitted_first(self, db):
        from app.hosts_mgmt.merge import get_effective_hosts_entries, render_hosts_file
        from app.hosts_mgmt.models import HostsEntry

        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        # Same hostname, different IPs — both survive the merge, which
        # keys on the address, so the file decides which `db` resolves to.
        # The low-priority one sorts first by IP string, so a stable-but-
        # arbitrary sort would put the wrong one on top.
        db.add(HostsEntry(host_id=host.id, ip_address="10.0.0.1", hostname="db", priority=1))
        db.add(HostsEntry(host_id=host.id, ip_address="10.0.0.2", hostname="db", priority=9))
        await db.flush()

        rendered = render_hosts_file(await get_effective_hosts_entries(host.id, db))
        lines = [ln for ln in rendered.splitlines() if " db" in ln]
        assert lines[0].startswith("10.0.0.2"), rendered

    async def test_system_entries_still_come_first(self, db):
        from app.hosts_mgmt.merge import get_effective_hosts_entries, render_hosts_file
        from app.hosts_mgmt.models import HostsEntry

        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        db.add(HostsEntry(host_id=host.id, ip_address="10.0.0.3", hostname="app", priority=10000))
        await db.flush()

        rendered = render_hosts_file(await get_effective_hosts_entries(host.id, db))
        body = [ln for ln in rendered.splitlines() if ln and not ln.startswith("#")]
        assert body[0].startswith("127.0.0.1"), rendered

    async def test_equal_priorities_still_render_stably(self, db):
        from app.hosts_mgmt.merge import get_effective_hosts_entries
        from app.hosts_mgmt.models import HostsEntry

        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        for ip in ("10.9.0.2", "10.9.0.1"):
            db.add(HostsEntry(host_id=host.id, ip_address=ip, hostname=f"n{ip[-1]}"))
            await db.flush()

        first = [e.ip_address for e in await get_effective_hosts_entries(host.id, db)]
        second = [e.ip_address for e in await get_effective_hosts_entries(host.id, db)]
        assert first == second

    async def test_the_effective_entry_carries_its_priority(self, db):
        from app.hosts_mgmt.merge import get_effective_hosts_entries
        from app.hosts_mgmt.models import HostsEntry

        host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}")
        db.add(HostsEntry(host_id=host.id, ip_address="10.4.0.1", hostname="x", priority=42))
        await db.flush()

        entries = await get_effective_hosts_entries(host.id, db)
        assert [e.priority for e in entries if e.ip_address == "10.4.0.1"] == [42]
