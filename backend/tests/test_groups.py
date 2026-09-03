"""
Integration tests for group CRUD endpoints.

Tests cover:
- Creating groups with unique names and priorities
- Listing groups
- Handling duplicate names (409)
- Handling duplicate priorities (409)
- Preventing deletion of groups with assigned hosts (400)
"""

import uuid

import pytest

pytestmark = pytest.mark.integration


class TestGroups:
    """Group CRUD endpoint tests."""

    async def test_create_group(self, superuser_client):
        """POST /api/groups → 201, returns id + name."""
        group_name = f"group-{uuid.uuid4().hex[:8]}"
        priority = int(uuid.uuid4().int % 1000) + 1

        resp = await superuser_client.post(
            "/api/groups",
            json={
                "name": group_name,
                "priority": priority,
                "description": "Test group",
            },
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] is not None
        assert data["name"] == group_name
        assert data["priority"] == priority
        assert data["description"] == "Test group"
        assert "created_at" in data
        assert "updated_at" in data

    async def test_list_groups(self, superuser_client):
        """GET /api/groups → 200, returns list of groups."""
        # Create a few groups
        group1_name = f"group-{uuid.uuid4().hex[:8]}"
        group2_name = f"group-{uuid.uuid4().hex[:8]}"
        priority1 = int(uuid.uuid4().int % 1000) + 1
        priority2 = int(uuid.uuid4().int % 1000) + 1

        resp1 = await superuser_client.post(
            "/api/groups",
            json={"name": group1_name, "priority": priority1},
        )
        assert resp1.status_code == 201
        group1_id = resp1.json()["id"]

        resp2 = await superuser_client.post(
            "/api/groups",
            json={"name": group2_name, "priority": priority2},
        )
        assert resp2.status_code == 201
        group2_id = resp2.json()["id"]

        # List groups
        resp = await superuser_client.get("/api/groups")
        assert resp.status_code == 200
        groups = resp.json()
        assert isinstance(groups, list)
        assert len(groups) >= 2

        # Verify created groups are in the list
        group_ids = [g["id"] for g in groups]
        assert group1_id in group_ids
        assert group2_id in group_ids

    async def test_duplicate_name_409(self, superuser_client):
        """Create group, then POST same name → 409."""
        group_name = f"group-{uuid.uuid4().hex[:8]}"
        priority1 = int(uuid.uuid4().int % 1000) + 1
        priority2 = int(uuid.uuid4().int % 1000) + 1

        # Create first group
        resp1 = await superuser_client.post(
            "/api/groups",
            json={"name": group_name, "priority": priority1},
        )
        assert resp1.status_code == 201

        # Try to create second group with same name but different priority
        resp2 = await superuser_client.post(
            "/api/groups",
            json={"name": group_name, "priority": priority2},
        )
        assert resp2.status_code == 409
        assert "already exists" in resp2.json()["detail"]

    async def test_duplicate_priority_409(self, superuser_client):
        """Create group, then POST same priority → 409."""
        group1_name = f"group-{uuid.uuid4().hex[:8]}"
        group2_name = f"group-{uuid.uuid4().hex[:8]}"
        priority = int(uuid.uuid4().int % 1000) + 1

        # Create first group
        resp1 = await superuser_client.post(
            "/api/groups",
            json={"name": group1_name, "priority": priority},
        )
        assert resp1.status_code == 201

        # Try to create second group with same priority but different name
        resp2 = await superuser_client.post(
            "/api/groups",
            json={"name": group2_name, "priority": priority},
        )
        assert resp2.status_code == 409
        assert "priority already in use" in resp2.json()["detail"]

    async def test_delete_group_with_hosts_400(self, superuser_client, db):
        """Create group + host in that group, DELETE group → 400."""
        from tests.conftest import create_group, create_host, create_ssh_key

        # Create group, SSH key, and host in that group
        group = await create_group(
            db,
            name=f"group-{uuid.uuid4().hex[:8]}",
            priority=int(uuid.uuid4().int % 1000) + 1,
        )
        ssh_key = await create_ssh_key(db)
        await create_host(db, ssh_key_id=ssh_key.id, group_ids=[group.id])

        # Try to delete the group
        resp = await superuser_client.delete(f"/api/groups/{group.id}")
        assert resp.status_code == 400
        assert "Cannot delete group with hosts assigned" in resp.json()["detail"]

    async def test_delete_group_success(self, superuser_client):
        """Delete a group with no hosts → 204."""
        group_name = f"group-{uuid.uuid4().hex[:8]}"
        priority = int(uuid.uuid4().int % 1000) + 1

        # Create group
        resp = await superuser_client.post(
            "/api/groups",
            json={"name": group_name, "priority": priority},
        )
        assert resp.status_code == 201
        group_id = resp.json()["id"]

        # Delete group
        resp = await superuser_client.delete(f"/api/groups/{group_id}")
        assert resp.status_code == 204

        # Verify it's gone
        resp = await superuser_client.get(f"/api/groups/{group_id}")
        assert resp.status_code == 404


class TestGroupUpdateClearsNullableFields:
    """PUT /api/groups/{id} must distinguish "omitted" from "explicitly null".

    The handler used ``model_dump(exclude_none=True)``, which drops both
    cases identically — so there was no way to remove a description or
    category, or to stop overriding a chain policy. Sending
    ``{"input_policy": null}`` returned 200 with the old value still in
    place, and the group kept pushing that policy to every member host.
    """

    async def _make_group(self, superuser_client, **extra):
        payload = {
            "name": f"group-{uuid.uuid4().hex[:8]}",
            "priority": int(uuid.uuid4().int % 1000) + 1,
            **extra,
        }
        resp = await superuser_client.post("/api/groups", json=payload)
        assert resp.status_code == 201, resp.text
        return resp.json()

    async def test_explicit_null_clears_description(self, superuser_client):
        group = await self._make_group(superuser_client, description="initial")
        assert group["description"] == "initial"

        resp = await superuser_client.put(f"/api/groups/{group['id']}", json={"description": None})
        assert resp.status_code == 200, resp.text
        assert resp.json()["description"] is None

    async def test_explicit_null_clears_policy_override(self, superuser_client):
        group = await self._make_group(superuser_client, input_policy="accept")
        assert group["input_policy"] == "accept"

        resp = await superuser_client.put(f"/api/groups/{group['id']}", json={"input_policy": None})
        assert resp.status_code == 200, resp.text
        assert resp.json()["input_policy"] is None

    async def test_omitted_field_is_left_alone(self, superuser_client):
        """The other half of the contract: omission must not clear."""
        group = await self._make_group(superuser_client, description="keep me")

        resp = await superuser_client.put(f"/api/groups/{group['id']}", json={"category": "prod"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["category"] == "prod"
        assert body["description"] == "keep me"

    @pytest.mark.parametrize("field", ["name", "priority"])
    async def test_null_on_non_nullable_column_is_422(self, superuser_client, field):
        """name/priority are NOT NULL — an explicit null is a client error,
        not a clear-the-field request, and must not reach the database."""
        group = await self._make_group(superuser_client)

        resp = await superuser_client.put(f"/api/groups/{group['id']}", json={field: None})
        assert resp.status_code == 422, resp.text
