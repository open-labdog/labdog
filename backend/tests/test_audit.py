"""
Integration tests for audit log read API.

Tests the /api/audit-log endpoint for listing, filtering, and pagination.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

pytestmark = pytest.mark.integration


class TestAudit:
    """Test suite for audit log read operations."""

    @pytest.mark.asyncio
    async def test_list_empty(self, superuser_client):
        """GET /api/audit-log with no entries → 200, empty list."""
        resp = await superuser_client.get("/api/audit-log")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 0

    @pytest.mark.asyncio
    async def test_list_with_entries(self, superuser_client, db: AsyncSession):
        """Insert 3 AuditLog records, GET /api/audit-log → returns list with 3 items."""
        # Insert 3 audit log entries directly
        for i in range(3):
            entry = AuditLog(
                action="create",
                entity_type="rule",
                entity_id=i + 1,
                user_id=None,
                ip_address="127.0.0.1",
            )
            db.add(entry)
        await db.flush()

        # Get audit logs via API
        resp = await superuser_client.get("/api/audit-log")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 3
        # Verify structure of first entry
        assert "id" in data[0]
        assert "action" in data[0]
        assert "entity_type" in data[0]
        assert "created_at" in data[0]

    @pytest.mark.asyncio
    async def test_filter_by_entity_type(self, superuser_client, db: AsyncSession):
        """Insert records with different entity_type, filter → only rule entries."""
        # Insert entries with different entity types
        entity_types = ["rule", "host", "group", "rule", "host"]
        for i, entity_type in enumerate(entity_types):
            entry = AuditLog(
                action="create",
                entity_type=entity_type,
                entity_id=i + 1,
                user_id=None,
                ip_address="127.0.0.1",
            )
            db.add(entry)
        await db.flush()

        # Filter by entity_type=rule
        resp = await superuser_client.get("/api/audit-log?entity_type=rule")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert isinstance(data, list)
        # Should return 2 rule entries (in descending id order)
        assert len(data) == 2
        assert all(entry["entity_type"] == "rule" for entry in data)

    @pytest.mark.asyncio
    async def test_cursor_pagination(self, superuser_client, db: AsyncSession):
        """Insert 5 records, GET limit=2 → 2 items, then cursor to get next batch."""
        # Insert 5 audit log entries
        for i in range(5):
            entry = AuditLog(
                action="create",
                entity_type="rule",
                entity_id=i + 1,
                user_id=None,
                ip_address="127.0.0.1",
            )
            db.add(entry)
        await db.flush()

        # First page: limit=2
        resp = await superuser_client.get("/api/audit-log?limit=2")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert len(data) == 2
        # Records are returned in descending id order
        first_page_ids = [entry["id"] for entry in data]
        assert len(first_page_ids) == 2
        # First page should have the two highest IDs
        assert first_page_ids[0] > first_page_ids[1]

        # Second page: use cursor (last seen id from first page)
        last_id = data[-1]["id"]
        resp = await superuser_client.get(f"/api/audit-log?limit=2&cursor={last_id}")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert len(data) == 2
        # Second page should have the next two highest IDs
        second_page_ids = [entry["id"] for entry in data]
        assert len(second_page_ids) == 2
        # Second page IDs should be less than first page IDs
        assert second_page_ids[0] < first_page_ids[1]

    @pytest.mark.asyncio
    async def test_filter_by_action(self, superuser_client, db: AsyncSession):
        """Insert records with different actions, filter → only matching entries."""
        # Insert entries with different actions
        actions = ["create", "update", "delete", "create", "update"]
        for i, action in enumerate(actions):
            entry = AuditLog(
                action=action,
                entity_type="rule",
                entity_id=i + 1,
                user_id=None,
                ip_address="127.0.0.1",
            )
            db.add(entry)
        await db.flush()

        # Filter by action=create
        resp = await superuser_client.get("/api/audit-log?action=create")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert isinstance(data, list)
        # Should return 2 create entries
        assert len(data) == 2
        assert all(entry["action"] == "create" for entry in data)
