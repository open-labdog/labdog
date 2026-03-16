"""
RBAC enforcement tests for group endpoints.

Tests verify:
- Superuser bypass on write endpoints
- Unauthenticated rejection (401)
- Viewer read-only access (200 GET, 403 POST)
- Non-superuser write rejection (403)
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


class TestRBAC:
    """Test RBAC enforcement on group endpoints."""

    async def test_superuser_can_create_group(self, superuser_client):
        """Superuser POST /api/groups → 201 (bypasses all checks)."""
        group_name = f"test-group-{uuid.uuid4().hex[:8]}"
        priority = int(uuid.uuid4().int % 9999) + 1

        resp = await superuser_client.post(
            "/api/groups",
            json={"name": group_name, "priority": priority, "description": "test group"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == group_name
        assert data["priority"] == priority

    async def test_viewer_cannot_create_group(self, viewer_client):
        """Viewer POST /api/groups → 403 (write denied)."""
        group_name = f"test-group-{uuid.uuid4().hex[:8]}"
        priority = int(uuid.uuid4().int % 9999) + 1

        resp = await viewer_client.post(
            "/api/groups",
            json={"name": group_name, "priority": priority},
        )

        assert resp.status_code == 403

    async def test_viewer_can_list_groups(self, viewer_client):
        """Viewer GET /api/groups → 200 (read allowed)."""
        resp = await viewer_client.get("/api/groups")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    async def test_unauthenticated_rejected(self, client):
        """Unauthenticated GET /api/groups → 401 (no auth)."""
        resp = await client.get("/api/groups")

        assert resp.status_code == 401
