"""BUG-115: a synced /etc/hosts keeps the host's own name, and a host no
entry applies to keeps its file.

The hosts sync wrote the merged entries as the whole file, and the merge
seeded only ``127.0.0.1 localhost`` and ``::1 localhost``. On valheim no
entry applied at all, yet a sync still wrote those two lines over the
Debian file and dropped ``127.0.1.1 valheim``. Its resolver had no answer
for the name, so from then on the host could not resolve itself and every
``sudo`` complained about it.
"""

import uuid

import pytest

from app.hosts_mgmt.collector import ParsedHostsEntry
from app.hosts_mgmt.diff import compute_hosts_diff
from app.hosts_mgmt.merge import get_effective_hosts_entries, render_hosts_file
from app.hosts_mgmt.models import HostsEntry
from app.tasks.facts import _pick_reported_hostname
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


def _name() -> str:
    return f"h{uuid.uuid4().hex[:8]}"


async def _managed_host(db, *, hostname: str | None = None, reported: str | None = None):
    """A host in a group with one ordinary entry, so the module manages it."""
    group = await create_group(db)
    host = await create_host(db, hostname=hostname or _name(), group_ids=[group.id])
    host.reported_hostname = reported
    db.add(HostsEntry(group_id=group.id, ip_address="10.0.0.5", hostname="db01"))
    await db.flush()
    return host, group


def _own_line(entries):
    return [e for e in entries if e.ip_address == "127.0.1.1"]


class TestHostWithNoEntries:
    async def test_merge_returns_nothing(self, db):
        group = await create_group(db)
        host = await create_host(db, hostname=_name(), group_ids=[group.id])

        assert await get_effective_hosts_entries(host.id, db) == []

    async def test_sync_leaves_the_file_alone(self, db, tmp_path):
        from app.models.host import FirewallBackend
        from app.sync.orchestrator import build_host_sync_plan

        ssh_key = await create_ssh_key(db)
        host = await create_host(db, hostname=_name(), ssh_key_id=ssh_key.id)
        host.firewall_backend = FirewallBackend.nftables
        await db.flush()

        async def plan(module_filter):
            return await build_host_sync_plan(
                host.id,
                module_filter,
                db,
                decrypt_key_fn=lambda _key, _master: b"FAKE PRIVATE KEY",
                ssh_key_path=str(tmp_path / f"key-{uuid.uuid4().hex}"),
            )

        # The hosts tab's own sync: nothing to run at all.
        assert (await plan(["hosts-file"])).playbook_yaml == ""
        # "Sync all": the other modules run, the hosts file is not written.
        everything = await plan(None)
        assert everything.playbook_yaml
        assert "/etc/hosts" not in everything.playbook_yaml
        assert "hosts-file" in everything.modules_to_run

    def test_drift_finds_nothing_extra(self):
        current = [
            ParsedHostsEntry(ip_address="127.0.0.1", hostname="localhost", aliases=[]),
            ParsedHostsEntry(ip_address="127.0.1.1", hostname="valheim", aliases=[]),
        ]

        assert not compute_hosts_diff(current, []).has_changes

    async def test_preview_is_empty(self, superuser_client, db):
        host = await create_host(db, hostname=_name())
        await db.commit()

        resp = await superuser_client.get(f"/api/hosts/{host.id}/hosts-file-preview")

        assert resp.status_code == 200
        assert resp.text == ""


class TestOwnName:
    async def test_reported_name_goes_on_127_0_1_1(self, db):
        host, _ = await _managed_host(db, reported="valheim.lan")

        entries = await get_effective_hosts_entries(host.id, db)

        assert [e.ip_address for e in entries] == ["127.0.0.1", "127.0.1.1", "::1", "10.0.0.5"]
        (own,) = _own_line(entries)
        assert (own.hostname, own.aliases) == ("valheim.lan", ["valheim"])
        assert own.is_system and own.source == "system"
        assert "\n127.0.1.1 valheim.lan valheim\n" in render_hosts_file(entries)

    async def test_short_name_alone(self, db):
        host, _ = await _managed_host(db, reported="valheim")

        (own,) = _own_line(await get_effective_hosts_entries(host.id, db))

        assert (own.hostname, own.aliases) == ("valheim", [])

    async def test_falls_back_to_the_labdog_name(self, db):
        name = _name()
        host, _ = await _managed_host(db, hostname=name)

        (own,) = _own_line(await get_effective_hosts_entries(host.id, db))

        assert own.hostname == name

    async def test_no_line_for_a_name_that_is_not_a_hostname(self, db):
        host, _ = await _managed_host(db, hostname=f"Game server {uuid.uuid4().hex[:6]}")

        assert _own_line(await get_effective_hosts_entries(host.id, db)) == []

    async def test_an_entry_that_maps_the_name_wins(self, db):
        """A Proxmox node must resolve its own name to its real address."""
        host, group = await _managed_host(db, reported="pve1.lan")
        db.add(
            HostsEntry(group_id=group.id, ip_address="10.10.5.20", hostname="PVE1", aliases=["pve"])
        )
        await db.flush()

        entries = await get_effective_hosts_entries(host.id, db)

        assert _own_line(entries) == []
        assert "10.10.5.20" in {e.ip_address for e in entries}

    async def test_a_registered_host_entry_for_itself_wins(self, db):
        name = _name()
        host, group = await _managed_host(db, hostname=name, reported=f"{name}.lan")
        db.add(HostsEntry(group_id=group.id, host_ref_id=host.id))
        await db.flush()

        entries = await get_effective_hosts_entries(host.id, db)

        assert _own_line(entries) == []
        assert any(e.hostname == name and e.ip_address == host.ip_address for e in entries)

    async def test_an_entry_on_127_0_1_1_wins(self, db):
        host, _ = await _managed_host(db, reported="valheim")
        db.add(HostsEntry(host_id=host.id, ip_address="127.0.1.1", hostname="something-else"))
        await db.flush()

        (line,) = _own_line(await get_effective_hosts_entries(host.id, db))

        assert (line.hostname, line.source) == ("something-else", "host")


class TestReportedHostname:
    @pytest.mark.parametrize(
        ("nodename", "full_name", "expected"),
        [
            ("valheim", "valheim.lan", "valheim.lan"),
            ("valheim\n", "valheim.lan\n", "valheim.lan"),
            ("Valheim", "valheim.LAN", "valheim.LAN"),
            ("valheim.lan", "valheim.lan", "valheim.lan"),
            # `hostname -f` fails on a host that cannot resolve itself.
            ("valheim", "", "valheim"),
            # ... or names something else, e.g. when /etc/hosts has the
            # name on the 127.0.0.1 line.
            ("valheim", "localhost", "valheim"),
            ("valheim", "other.lan", "valheim"),
            ("valheim.lan", "valheim", "valheim.lan"),
            ("", "", None),
            ("bad_name", "", None),
        ],
    )
    def test_pick(self, nodename, full_name, expected):
        assert _pick_reported_hostname(nodename, full_name) == expected
