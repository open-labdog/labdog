"""Tests for the ssh_keys CRUD API, including audit-log coverage (SEC-14)."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from app.models.audit_log import AuditLog


def _generate_pem() -> str:
    """Return a fresh Ed25519 private key in OpenSSH PEM format."""
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------


async def test_list_ssh_keys_requires_login(client):
    resp = await client.get("/api/ssh-keys")
    assert resp.status_code in (401, 403)


async def test_create_ssh_key_accessible_to_regular_user(regular_user_client):
    resp = await regular_user_client.post(
        "/api/ssh-keys",
        json={"name": "k", "private_key": _generate_pem()},
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_create_ssh_key_returns_201(superuser_client):
    resp = await superuser_client.post(
        "/api/ssh-keys",
        json={"name": "test-key", "private_key": _generate_pem(), "ssh_user": "deploy"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "test-key"
    assert body["ssh_user"] == "deploy"
    assert body["is_default"] is False
    # Secret material must never appear in the response.
    assert "private_key" not in body
    assert "encrypted_private_key" not in body


async def test_create_ssh_key_duplicate_name_returns_409(superuser_client):
    payload = {"name": "dup-key", "private_key": _generate_pem()}
    r1 = await superuser_client.post("/api/ssh-keys", json=payload)
    assert r1.status_code == 201
    r2 = await superuser_client.post(
        "/api/ssh-keys", json={**payload, "private_key": _generate_pem()}
    )
    assert r2.status_code == 409


async def test_create_ssh_key_emits_audit_row(superuser_client, db):
    resp = await superuser_client.post(
        "/api/ssh-keys",
        json={"name": "audit-create-key", "private_key": _generate_pem(), "ssh_user": "root"},
    )
    assert resp.status_code == 201, resp.text
    key_id = resp.json()["id"]

    row = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "create",
                AuditLog.entity_type == "ssh_key",
                AuditLog.entity_id == key_id,
            )
        )
    ).scalar_one_or_none()

    assert row is not None
    assert row.after_state["name"] == "audit-create-key"
    assert row.after_state["ssh_user"] == "root"
    assert row.after_state["is_default"] is False
    # Secret material must never appear in the audit payload.
    assert "private_key" not in row.after_state
    assert "encrypted_private_key" not in row.after_state
    assert row.before_state is None


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def test_update_ssh_key_returns_200(superuser_client):
    create = await superuser_client.post(
        "/api/ssh-keys",
        json={"name": "update-me", "private_key": _generate_pem()},
    )
    key_id = create.json()["id"]

    resp = await superuser_client.put(
        f"/api/ssh-keys/{key_id}",
        json={"name": "updated-name", "ssh_user": "git"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "updated-name"
    assert body["ssh_user"] == "git"


async def test_update_ssh_key_not_found_returns_404(superuser_client):
    resp = await superuser_client.put("/api/ssh-keys/9999", json={"name": "x"})
    assert resp.status_code == 404


async def test_update_ssh_key_emits_audit_row(superuser_client, db):
    create = await superuser_client.post(
        "/api/ssh-keys",
        json={"name": "audit-update-key", "private_key": _generate_pem(), "ssh_user": "root"},
    )
    key_id = create.json()["id"]

    await superuser_client.put(
        f"/api/ssh-keys/{key_id}",
        json={"ssh_user": "deploy"},
    )

    row = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "update",
                AuditLog.entity_type == "ssh_key",
                AuditLog.entity_id == key_id,
            )
        )
    ).scalar_one_or_none()

    assert row is not None
    assert row.before_state["ssh_user"] == "root"
    assert row.after_state["ssh_user"] == "deploy"
    assert row.after_state["name"] == "audit-update-key"
    assert "private_key" not in row.after_state
    assert "encrypted_private_key" not in row.after_state


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_ssh_key_returns_204(superuser_client):
    create = await superuser_client.post(
        "/api/ssh-keys",
        json={"name": "delete-me", "private_key": _generate_pem()},
    )
    key_id = create.json()["id"]

    resp = await superuser_client.delete(f"/api/ssh-keys/{key_id}")
    assert resp.status_code == 204

    get = await superuser_client.get("/api/ssh-keys")
    ids = [k["id"] for k in get.json()]
    assert key_id not in ids


async def test_delete_ssh_key_not_found_returns_404(superuser_client):
    resp = await superuser_client.delete("/api/ssh-keys/9999")
    assert resp.status_code == 404


async def test_delete_ssh_key_emits_audit_row(superuser_client, db):
    create = await superuser_client.post(
        "/api/ssh-keys",
        json={"name": "audit-delete-key", "private_key": _generate_pem(), "ssh_user": "ops"},
    )
    key_id = create.json()["id"]

    resp = await superuser_client.delete(f"/api/ssh-keys/{key_id}")
    assert resp.status_code == 204

    row = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "delete",
                AuditLog.entity_type == "ssh_key",
                AuditLog.entity_id == key_id,
            )
        )
    ).scalar_one_or_none()

    assert row is not None
    assert row.before_state["name"] == "audit-delete-key"
    assert row.before_state["ssh_user"] == "ops"
    assert row.after_state is None
    assert "private_key" not in row.before_state
    assert "encrypted_private_key" not in row.before_state
