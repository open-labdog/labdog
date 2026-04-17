import pytest
from pydantic import ValidationError

from app.hosts_mgmt.collector import ParsedHostsEntry
from app.hosts_mgmt.diff import compute_hosts_diff
from app.hosts_mgmt.merge import render_hosts_file
from app.hosts_mgmt.schemas import EffectiveHostsEntryResponse, HostsEntryCreate


class TestHostsSchemas:
    def test_valid_ipv4(self):
        entry = HostsEntryCreate(ip_address="192.168.1.1", hostname="web1.example.com")
        assert entry.ip_address == "192.168.1.1"

    def test_valid_ipv6(self):
        entry = HostsEntryCreate(ip_address="fe80::1", hostname="web1.example.com")
        assert entry.ip_address == "fe80::1"

    def test_invalid_ip_rejected(self):
        with pytest.raises(ValidationError):
            HostsEntryCreate(ip_address="not-an-ip", hostname="web1.example.com")

    def test_valid_hostname(self):
        entry = HostsEntryCreate(ip_address="10.0.0.1", hostname="my-server.example.com")
        assert entry.hostname == "my-server.example.com"

    def test_hostname_too_long_rejected(self):
        with pytest.raises(ValidationError):
            HostsEntryCreate(ip_address="10.0.0.1", hostname="a" * 254)

    def test_hostname_with_spaces_rejected(self):
        with pytest.raises(ValidationError):
            HostsEntryCreate(ip_address="10.0.0.1", hostname="bad hostname")

    def test_aliases_validated(self):
        entry = HostsEntryCreate(
            ip_address="10.0.0.1", hostname="web1", aliases=["web1-alias", "web1.local"]
        )
        assert len(entry.aliases) == 2

    def test_invalid_alias_rejected(self):
        with pytest.raises(ValidationError):
            HostsEntryCreate(ip_address="10.0.0.1", hostname="web1", aliases=["bad hostname!"])


class TestHostsRenderer:
    def test_system_entries_present(self):
        entries = [
            EffectiveHostsEntryResponse(
                ip_address="127.0.0.1",
                hostname="localhost",
                aliases=[],
                comment=None,
                is_system=True,
                source="system",
                source_id=0,
                source_name="system",
            ),
            EffectiveHostsEntryResponse(
                ip_address="::1",
                hostname="localhost",
                aliases=["ip6-localhost", "ip6-loopback"],
                comment=None,
                is_system=True,
                source="system",
                source_id=0,
                source_name="system",
            ),
        ]
        rendered = render_hosts_file(entries)
        assert "127.0.0.1 localhost" in rendered
        assert "::1 localhost ip6-localhost ip6-loopback" in rendered
        assert "Managed by Barricade" in rendered

    def test_custom_entry_rendered(self):
        entries = [
            EffectiveHostsEntryResponse(
                ip_address="10.0.0.5",
                hostname="db.internal",
                aliases=["db"],
                comment="Database server",
                is_system=False,
                source="group",
                source_id=1,
                source_name="prod",
            ),
        ]
        rendered = render_hosts_file(entries)
        assert "10.0.0.5 db.internal db  # Database server" in rendered

    def test_trailing_newline(self):
        rendered = render_hosts_file([])
        assert rendered.endswith("\n")


class TestHostsDiff:
    def test_in_sync(self):
        current = [ParsedHostsEntry(ip_address="10.0.0.1", hostname="web1", aliases=[])]
        desired = [
            EffectiveHostsEntryResponse(
                ip_address="10.0.0.1",
                hostname="web1",
                aliases=[],
                comment=None,
                is_system=False,
                source="group",
                source_id=1,
                source_name="g",
            )
        ]
        diff = compute_hosts_diff(current, desired)
        assert "10.0.0.1" in diff.entries_in_sync
        assert not diff.has_changes

    def test_missing_entry(self):
        current = []
        desired = [
            EffectiveHostsEntryResponse(
                ip_address="10.0.0.1",
                hostname="web1",
                aliases=[],
                comment=None,
                is_system=False,
                source="group",
                source_id=1,
                source_name="g",
            )
        ]
        diff = compute_hosts_diff(current, desired)
        assert len(diff.entries_to_add) == 1
        assert diff.entries_to_add[0].reason == "missing"

    def test_extra_entry(self):
        current = [ParsedHostsEntry(ip_address="10.0.0.99", hostname="rogue", aliases=[])]
        desired = []
        diff = compute_hosts_diff(current, desired)
        assert len(diff.entries_to_remove) == 1
        assert diff.entries_to_remove[0].reason == "extra"

    def test_hostname_mismatch(self):
        current = [ParsedHostsEntry(ip_address="10.0.0.1", hostname="old-name", aliases=[])]
        desired = [
            EffectiveHostsEntryResponse(
                ip_address="10.0.0.1",
                hostname="new-name",
                aliases=[],
                comment=None,
                is_system=False,
                source="group",
                source_id=1,
                source_name="g",
            )
        ]
        diff = compute_hosts_diff(current, desired)
        assert diff.has_changes
        assert diff.entries_to_update[0].reason == "hostname_mismatch"


class TestHostsAPI:
    @pytest.mark.asyncio
    async def test_create_group_entry(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/hosts-entries",
            json={"ip_address": "10.0.0.5", "hostname": "web1.internal", "aliases": ["web1"]},
        )
        assert resp.status_code == 201
        assert resp.json()["ip_address"] == "10.0.0.5"
        assert resp.json()["hostname"] == "web1.internal"

    @pytest.mark.asyncio
    async def test_invalid_ip_rejected_by_api(self, superuser_client, db):
        from tests.conftest import create_group

        group = await create_group(db)
        await db.commit()
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/hosts-entries",
            json={"ip_address": "not-valid", "hostname": "web1"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_hosts_file_preview(self, superuser_client, db):
        from tests.conftest import create_group, create_host, create_ssh_key

        group = await create_group(db)
        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        await db.commit()
        await superuser_client.post(
            f"/api/groups/{group.id}/hosts-entries",
            json={"ip_address": "10.0.0.5", "hostname": "web1.internal"},
        )
        resp = await superuser_client.get(f"/api/hosts/{host.id}/hosts-file-preview")
        assert resp.status_code == 200
        text = resp.text
        assert "127.0.0.1 localhost" in text
        assert "10.0.0.5 web1.internal" in text
        assert "Managed by Barricade" in text
