"""
Integration tests for host CRUD operations.

Tests the /api/hosts endpoints for creating, reading, and deleting hosts.
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


class TestHosts:
    """Test suite for host CRUD operations."""

    async def test_create_host_with_group(self, superuser_client):
        """Create group via API, then POST /api/hosts with group assignment → 201."""
        # Create group via API
        group_resp = await superuser_client.post(
            "/api/groups",
            json={"name": f"test-group-{uuid.uuid4().hex[:8]}", "priority": 500},
        )
        assert group_resp.status_code == 201
        group_id = group_resp.json()["id"]

        # Create host via API (without SSH key for simplicity)
        resp = await superuser_client.post(
            "/api/hosts",
            json={
                "hostname": f"test-host-{uuid.uuid4().hex[:8]}.example.com",
                "ip_address": "10.0.0.100",
                "ssh_port": 22,
                "ssh_key_id": None,
                "group_ids": [group_id],
            },
        )

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "hostname" in data
        assert data["ip_address"] == "10.0.0.100"

    async def test_get_host(self, superuser_client):
        """Create host via API, GET /api/hosts/{id} → 200, response has hostname."""
        # Create host via API
        resp = await superuser_client.post(
            "/api/hosts",
            json={
                "hostname": f"test-host-{uuid.uuid4().hex[:8]}.example.com",
                "ip_address": "10.0.0.50",
                "ssh_port": 22,
                "ssh_key_id": None,
                "group_ids": [],
            },
        )
        assert resp.status_code == 201
        host_id = resp.json()["id"]

        # Get host via API
        resp = await superuser_client.get(f"/api/hosts/{host_id}")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["id"] == host_id
        assert "hostname" in data
        assert data["ip_address"] == "10.0.0.50"

    async def test_get_nonexistent_404(self, superuser_client):
        """GET /api/hosts/99999 → 404."""
        resp = await superuser_client.get("/api/hosts/99999")

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "detail" in data

    async def test_delete_host(self, superuser_client):
        """Create host via API, DELETE /api/hosts/{id} → 204."""
        # Create host via API
        resp = await superuser_client.post(
            "/api/hosts",
            json={
                "hostname": f"test-host-{uuid.uuid4().hex[:8]}.example.com",
                "ip_address": "10.0.0.75",
                "ssh_port": 22,
                "ssh_key_id": None,
                "group_ids": [],
            },
        )
        assert resp.status_code == 201
        host_id = resp.json()["id"]

        # Delete host via API
        resp = await superuser_client.delete(f"/api/hosts/{host_id}")

        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

        # Verify host is deleted
        resp = await superuser_client.get(f"/api/hosts/{host_id}")
        assert resp.status_code == 404, "Host should be deleted"
