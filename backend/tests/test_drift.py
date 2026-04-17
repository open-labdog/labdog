"""
Integration tests for drift detection endpoints.

Tests the /api/drift endpoints for checking host drift and updating drift settings.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import create_group, create_host, create_rule, create_ssh_key

pytestmark = pytest.mark.integration


class TestDrift:
    """Test suite for drift detection endpoints."""

    @pytest.mark.asyncio
    async def test_check_drift_returns_status(self, superuser_client):
        """POST /api/drift/hosts/{id}/check → 200 with status and host_id."""
        # Create group via API
        resp = await superuser_client.post(
            "/api/groups",
            json={"name": f"test-group-{uuid.uuid4().hex[:6]}", "priority": 100},
        )
        assert resp.status_code == 201, f"Group creation failed: {resp.text}"
        group_id = resp.json()["id"]

        # Create host via API (without SSH key)
        resp = await superuser_client.post(
            "/api/hosts",
            json={
                "hostname": f"test-host-{uuid.uuid4().hex[:6]}.test",
                "ip_address": "10.0.0.1",
                "group_ids": [group_id],
            },
        )
        assert resp.status_code == 201, f"Host creation failed: {resp.text}"
        host_id = resp.json()["id"]

        # Check drift via API
        resp = await superuser_client.post(f"/api/drift/hosts/{host_id}/check")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "status" in data, "Response should have 'status' field"
        assert "host_id" in data, "Response should have 'host_id' field"
        assert data["host_id"] == host_id
        assert data["status"] in ["in_sync", "out_of_sync", "unknown", "error"]
        assert "checked_at" in data
        assert "has_changes" in data
        assert "add_count" in data
        assert "remove_count" in data

    @pytest.mark.asyncio
    async def test_update_drift_settings(self, superuser_client):
        """PUT /api/drift/hosts/{id}/settings → 200, toggles drift_check_enabled."""
        # Create host via API (without SSH key)
        resp = await superuser_client.post(
            "/api/hosts",
            json={
                "hostname": f"test-host-{uuid.uuid4().hex[:6]}.test",
                "ip_address": "10.0.0.1",
            },
        )
        assert resp.status_code == 201, f"Host creation failed: {resp.text}"
        host_id = resp.json()["id"]

        # Enable drift checking via API
        resp = await superuser_client.put(
            f"/api/drift/hosts/{host_id}/settings",
            json={"drift_check_enabled": True},
        )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["drift_check_enabled"] is True

        # Disable drift checking via API
        resp = await superuser_client.put(
            f"/api/drift/hosts/{host_id}/settings",
            json={"drift_check_enabled": False},
        )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["drift_check_enabled"] is False

    @pytest.mark.asyncio
    async def test_check_drift_nonexistent_404(self, db: AsyncSession, superuser_client):
        """POST /api/drift/hosts/99999/check → 404."""
        resp = await superuser_client.post("/api/drift/hosts/99999/check")

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "detail" in data

    @pytest.mark.asyncio
    async def test_update_drift_settings_nonexistent_404(self, db: AsyncSession, superuser_client):
        """PUT /api/drift/hosts/99999/settings → 404."""
        resp = await superuser_client.put(
            "/api/drift/hosts/99999/settings",
            json={"drift_check_enabled": True},
        )

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "detail" in data

    @pytest.mark.asyncio
    async def test_check_drift_with_rules_out_of_sync(self, db: AsyncSession, superuser_client):
        """Host with rules, drift check → out_of_sync (stub returns [])."""
        from app.models.host import FirewallBackend

        # Setup: create SSH key, group, host, and add a rule
        ssh_key = await create_ssh_key(db)
        group = await create_group(db)
        host = await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])
        host.firewall_backend = FirewallBackend.nftables
        await db.flush()

        # Add a rule to the group
        await create_rule(
            db,
            group_id=group.id,
            action="allow",
            protocol="tcp",
            direction="input",
            source_cidr="0.0.0.0/0",
            destination_cidr="10.0.0.1/32",
            port_start=22,
            port_end=22,
        )

        # Mock fetch_current_firewall_state to return empty state (no rules on host)
        from app.rules.model import ChainPolicies
        from app.sync.collector import CollectedFirewallState

        empty_state = CollectedFirewallState(rules=[], policies=ChainPolicies())
        with patch(
            "app.drift.detector.fetch_current_firewall_state",
            new=AsyncMock(return_value=empty_state),
        ):
            resp = await superuser_client.post(f"/api/drift/hosts/{host.id}/check")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        # Since current state is [], and we have rules, status should be out_of_sync
        assert data["status"] == "out_of_sync"
        assert data["has_changes"] is True
        assert data["add_count"] > 0
