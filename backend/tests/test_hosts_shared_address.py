"""BUG-118: hosts entries that share an address each keep their line.

lin-manager's ``add-hosts`` group mapped ``loki`` (priority 100) and
``mimir`` (priority 101) to one address. The merge was keyed on the
address, so only mimir survived, and the next sync dropped loki from
``/etc/hosts`` without a word. An address may carry any number of lines.
"""

import uuid

import pytest

from app.hosts_mgmt.collector import ParsedHostsEntry
from app.hosts_mgmt.diff import compute_hosts_diff
from app.hosts_mgmt.merge import get_effective_hosts_entries, render_hosts_file
from app.hosts_mgmt.models import HostsEntry
from app.hosts_mgmt.schemas import EffectiveHostsEntryResponse
from tests.conftest import create_group, create_host

pytestmark = pytest.mark.integration


async def test_two_names_for_one_address_both_reach_the_file(db):
    group = await create_group(db, name=f"g-{uuid.uuid4().hex[:8]}")
    host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}", group_ids=[group.id])
    for name, priority in (("loki.lan", 100), ("mimir.lan", 101)):
        db.add(
            HostsEntry(
                group_id=group.id, ip_address="10.10.200.3", hostname=name, priority=priority
            )
        )
        await db.flush()

    entries = await get_effective_hosts_entries(host.id, db)

    shared = [e.hostname for e in entries if e.ip_address == "10.10.200.3"]
    assert shared == ["mimir.lan", "loki.lan"]
    content = render_hosts_file(entries)
    assert "10.10.200.3 mimir.lan\n10.10.200.3 loki.lan\n" in content


async def test_a_host_override_still_replaces_the_same_line(db):
    group = await create_group(db, name=f"g-{uuid.uuid4().hex[:8]}")
    host = await create_host(db, hostname=f"h-{uuid.uuid4().hex[:8]}", group_ids=[group.id])
    db.add(HostsEntry(group_id=group.id, ip_address="10.0.9.9", hostname="nas", aliases=["old"]))
    db.add(HostsEntry(host_id=host.id, ip_address="10.0.9.9", hostname="nas", aliases=["new"]))
    await db.flush()

    entries = await get_effective_hosts_entries(host.id, db)

    assert [e.aliases for e in entries if e.ip_address == "10.0.9.9"] == [["new"]]


def _desired(ip: str, name: str) -> EffectiveHostsEntryResponse:
    return EffectiveHostsEntryResponse(
        ip_address=ip,
        hostname=name,
        aliases=[],
        comment=None,
        is_system=False,
        source="group",
        source_id=1,
        source_name="g",
    )


class TestTheDiffReadsEveryLine:
    def test_two_lines_for_one_address_are_in_sync(self):
        current = [
            ParsedHostsEntry("10.10.200.3", "mimir.lan", []),
            ParsedHostsEntry("10.10.200.3", "loki.lan", []),
        ]
        desired = [_desired("10.10.200.3", "mimir.lan"), _desired("10.10.200.3", "loki.lan")]

        diff = compute_hosts_diff(current, desired)

        assert not diff.has_changes
        assert diff.entries_in_sync == ["10.10.200.3", "10.10.200.3"]

    def test_a_missing_second_line_is_added(self):
        current = [ParsedHostsEntry("10.10.200.3", "mimir.lan", [])]
        desired = [_desired("10.10.200.3", "mimir.lan"), _desired("10.10.200.3", "loki.lan")]

        diff = compute_hosts_diff(current, desired)

        assert [(e.hostname, e.reason) for e in diff.entries_to_add] == [("loki.lan", "missing")]
        assert not diff.entries_to_remove and not diff.entries_to_update

    def test_an_unwanted_second_line_is_removed(self):
        current = [
            ParsedHostsEntry("10.10.200.3", "mimir.lan", []),
            ParsedHostsEntry("10.10.200.3", "old.lan", []),
        ]
        desired = [_desired("10.10.200.3", "mimir.lan")]

        diff = compute_hosts_diff(current, desired)

        assert [(e.hostname, e.reason) for e in diff.entries_to_remove] == [("old.lan", "extra")]
        assert not diff.entries_to_add

    def test_one_line_each_side_is_still_an_update(self):
        current = [ParsedHostsEntry("10.0.0.1", "web1", [])]
        desired = [_desired("10.0.0.1", "web2")]

        diff = compute_hosts_diff(current, desired)

        assert [e.reason for e in diff.entries_to_update] == ["hostname_mismatch"]
