"""The extracted merge scaffold: query count, precedence, provenance.

The six non-firewall merge modules used to repeat the same twenty lines —
membership query, a SELECT per group inside the loop, first-wins dict,
host overwrite, sort. They now share ``load_owned_rows`` + ``first_wins``
in ``app.merge_utils``.

Two things that refactor changes are worth pinning, because neither is
visible in the effective-list output the other suites assert on: the
number of round trips, and the fact that one first-wins pass over a
host-then-groups list produces what two passes used to.
"""

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.cron.merge import get_effective_cron_jobs
from app.hosts_mgmt.merge import get_effective_hosts_entries
from app.merge_utils import OwnedRow, first_wins, load_owned_rows
from app.packages.merge import get_effective_packages
from app.resolver.merge import get_effective_resolver
from app.services.merge import get_effective_services
from app.services.models import ServiceRule
from app.user_mgmt.merge import get_effective_groups, get_effective_users
from tests.conftest import create_group, create_host


class _SelectCounter:
    """Count SELECTs issued while the block is open."""

    def __init__(self):
        self.count = 0

    def __enter__(self):
        def _on_execute(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                self.count += 1

        self._handler = _on_execute
        event.listen(Engine, "before_cursor_execute", self._handler)
        return self

    def __exit__(self, *exc):
        event.remove(Engine, "before_cursor_execute", self._handler)
        return False


async def _service(db, *, name, group_id=None, host_id=None, priority=0, state="running"):
    rule = ServiceRule(
        service_name=name,
        state=state,
        enabled=True,
        group_id=group_id,
        host_id=host_id,
        priority=priority,
    )
    db.add(rule)
    await db.flush()
    return rule


class TestTheMergeNoLongerQueriesPerGroup:
    """The per-group SELECT inside the loop was an N+1."""

    async def test_query_count_does_not_grow_with_group_count(self, db):
        few = await create_host(db)
        many = await create_host(db)

        one_group = await create_group(db)
        await _service(db, name="nginx", group_id=one_group.id)
        from sqlalchemy import insert

        from app.models.host import HostGroupMembership

        await db.execute(insert(HostGroupMembership).values(host_id=few.id, group_id=one_group.id))

        for _ in range(8):
            group = await create_group(db)
            await _service(db, name=f"svc-{group.id}", group_id=group.id)
            await db.execute(insert(HostGroupMembership).values(host_id=many.id, group_id=group.id))
        await db.flush()

        with _SelectCounter() as one:
            await get_effective_services(few.id, db)
        with _SelectCounter() as eight:
            result = await get_effective_services(many.id, db)

        assert len(result) == 8, "all eight groups still contribute"
        assert one.count == eight.count, (
            f"one group cost {one.count} SELECTs, eight cost {eight.count} — "
            "the per-group query is back"
        )

    @pytest.mark.parametrize(
        "merge_fn",
        [
            get_effective_services,
            get_effective_cron_jobs,
            get_effective_packages,
            get_effective_users,
            get_effective_groups,
            get_effective_hosts_entries,
            get_effective_resolver,
        ],
    )
    async def test_every_module_reads_a_fixed_number_of_times(self, db, merge_fn):
        """Three: host rows, the group list, group rows.

        ``get_effective_hosts_entries`` adds a fourth for the host_ref
        lookup, but only when an entry actually references a host — there
        are none here.
        """
        from sqlalchemy import insert

        from app.models.host import HostGroupMembership

        host = await create_host(db)
        for _ in range(5):
            group = await create_group(db)
            await db.execute(insert(HostGroupMembership).values(host_id=host.id, group_id=group.id))
        await db.flush()

        with _SelectCounter() as counter:
            await merge_fn(host.id, db)

        assert counter.count <= 3, f"{merge_fn.__name__} issued {counter.count} SELECTs"


class TestOnePassSettlesBothQuestions:
    """Host-over-group and highest-priority-wins, in a single first-wins pass."""

    async def test_host_rows_lead_group_rows(self, db):
        from sqlalchemy import insert

        from app.models.host import HostGroupMembership

        host = await create_host(db)
        group = await create_group(db)
        await db.execute(insert(HostGroupMembership).values(host_id=host.id, group_id=group.id))
        await _service(db, name="nginx", group_id=group.id, priority=9999)
        await _service(db, name="nginx", host_id=host.id, priority=0)
        await db.flush()

        owned = await load_owned_rows(db, host.id, ServiceRule)

        assert [o.source for o in owned] == ["host", "group"], (
            "host rows must sort ahead of every group row, whatever the priorities — "
            "scope beats priority, and that is what makes one pass enough"
        )

    async def test_first_wins_keeps_the_leading_row_per_key(self):
        rows = [
            OwnedRow("winner", "host", 1, "host override"),
            OwnedRow("loser", "group", 2, "web"),
            OwnedRow("other", "group", 2, "web"),
        ]
        kept = first_wins(rows, key=lambda r: "same" if r != "other" else "different")

        assert [o.row for o in kept] == ["winner", "other"]


class TestHostsEntriesKeepTheirAsymmetry:
    """A group may not displace a system entry; a host override may."""

    async def test_a_group_cannot_replace_localhost(self, db):
        from sqlalchemy import insert

        from app.hosts_mgmt.models import HostsEntry
        from app.models.host import HostGroupMembership

        host = await create_host(db)
        group = await create_group(db)
        await db.execute(insert(HostGroupMembership).values(host_id=host.id, group_id=group.id))
        db.add(
            HostsEntry(
                ip_address="127.0.0.1", hostname="hijacked", group_id=group.id, priority=9999
            )
        )
        await db.flush()

        entries = await get_effective_hosts_entries(host.id, db)
        loopback = [e for e in entries if e.ip_address == "127.0.0.1"]

        assert len(loopback) == 1
        assert loopback[0].hostname == "localhost"
        assert loopback[0].is_system is True

    async def test_a_host_override_can_replace_localhost(self, db):
        from app.hosts_mgmt.models import HostsEntry

        host = await create_host(db)
        db.add(
            HostsEntry(ip_address="127.0.0.1", hostname="deliberate", host_id=host.id, priority=0)
        )
        await db.flush()

        entries = await get_effective_hosts_entries(host.id, db)
        loopback = [e for e in entries if e.ip_address == "127.0.0.1"]

        assert len(loopback) == 1
        assert loopback[0].hostname == "deliberate", (
            "a host-level entry replaced the system one before the merge scaffold "
            "was extracted, and still must — it is the only level at which someone "
            "can be doing this on purpose"
        )
        assert loopback[0].source == "host"
