"""Integration tests for CA cert API endpoints and merge engine."""
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


def _make_pem(common_name: str = "Test CA") -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


# ---------------------------------------------------------------------------
# Group-level CRUD
# ---------------------------------------------------------------------------


class TestGroupCACertCRUD:
    async def test_create_extracts_metadata(self, superuser_client, db):
        group = await create_group(db, priority=100)
        pem = _make_pem("Acme Root")
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "Acme Root", "pem_content": pem},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["name"] == "Acme Root"
        assert data["state"] == "present"
        assert data["fingerprint_sha256"].count(":") == 31
        assert "Acme Root" in data["subject"]
        assert data["not_after"] is not None
        assert data["group_id"] == group.id
        assert data["host_id"] is None

    async def test_create_rejects_invalid_pem(self, superuser_client, db):
        group = await create_group(db, priority=100)
        resp = await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "X", "pem_content": "not a cert"},
        )
        assert resp.status_code == 422

    async def test_create_rejects_duplicate_fingerprint(self, superuser_client, db):
        group = await create_group(db, priority=100)
        pem = _make_pem("Dup CA")
        r1 = await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "First", "pem_content": pem},
        )
        assert r1.status_code == 201
        r2 = await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "Second", "pem_content": pem},
        )
        assert r2.status_code == 409

    async def test_list_returns_certs(self, superuser_client, db):
        group = await create_group(db, priority=100)
        pem1 = _make_pem("CA One")
        pem2 = _make_pem("CA Two")
        await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "One", "pem_content": pem1},
        )
        await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "Two", "pem_content": pem2},
        )
        resp = await superuser_client.get(f"/api/groups/{group.id}/ca-certs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {d["name"] for d in data}
        assert names == {"One", "Two"}

    async def test_update_name_and_state(self, superuser_client, db):
        group = await create_group(db, priority=100)
        pem = _make_pem()
        create_resp = await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "Original", "pem_content": pem},
        )
        rule_id = create_resp.json()["id"]

        resp = await superuser_client.put(
            f"/api/groups/{group.id}/ca-certs/{rule_id}",
            json={"name": "Renamed", "state": "absent"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Renamed"
        assert data["state"] == "absent"
        # PEM and fingerprint unchanged
        assert data["fingerprint_sha256"] == create_resp.json()["fingerprint_sha256"]

    async def test_delete_removes_cert(self, superuser_client, db):
        group = await create_group(db, priority=100)
        pem = _make_pem()
        create_resp = await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "Doomed", "pem_content": pem},
        )
        rule_id = create_resp.json()["id"]

        resp = await superuser_client.delete(
            f"/api/groups/{group.id}/ca-certs/{rule_id}"
        )
        assert resp.status_code == 204

        list_resp = await superuser_client.get(f"/api/groups/{group.id}/ca-certs")
        assert list_resp.json() == []

    async def test_404_on_unknown_group(self, superuser_client):
        resp = await superuser_client.get("/api/groups/999999/ca-certs")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Host-level CRUD
# ---------------------------------------------------------------------------


class TestHostCACertCRUD:
    async def test_create_host_override(self, superuser_client, db):
        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        pem = _make_pem()
        resp = await superuser_client.post(
            f"/api/hosts/{host.id}/ca-certs",
            json={"name": "Host Cert", "pem_content": pem, "state": "absent"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["host_id"] == host.id
        assert data["group_id"] is None
        assert data["state"] == "absent"

    async def test_duplicate_fingerprint_on_host(self, superuser_client, db):
        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        pem = _make_pem()
        r1 = await superuser_client.post(
            f"/api/hosts/{host.id}/ca-certs",
            json={"name": "X", "pem_content": pem},
        )
        assert r1.status_code == 201
        r2 = await superuser_client.post(
            f"/api/hosts/{host.id}/ca-certs",
            json={"name": "Y", "pem_content": pem},
        )
        assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Effective merge endpoint
# ---------------------------------------------------------------------------


class TestEffectiveCACerts:
    async def test_single_group_certs_present(self, superuser_client, db):
        key = await create_ssh_key(db)
        group = await create_group(db, priority=100)
        pem_a = _make_pem("Group CA A")
        pem_b = _make_pem("Group CA B")
        await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "A", "pem_content": pem_a},
        )
        await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "B", "pem_content": pem_b},
        )
        host = await create_host(db, ssh_key_id=key.id, group_ids=[group.id])

        resp = await superuser_client.get(f"/api/hosts/{host.id}/effective-ca-certs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert all(d["source"] == "group" for d in data)
        assert all(d["state"] == "present" for d in data)

    async def test_two_groups_union(self, superuser_client, db):
        key = await create_ssh_key(db)
        g1 = await create_group(db, priority=100)
        g2 = await create_group(db, priority=200)
        await superuser_client.post(
            f"/api/groups/{g1.id}/ca-certs",
            json={"name": "From G1", "pem_content": _make_pem("G1 CA")},
        )
        await superuser_client.post(
            f"/api/groups/{g2.id}/ca-certs",
            json={"name": "From G2", "pem_content": _make_pem("G2 CA")},
        )
        host = await create_host(db, ssh_key_id=key.id, group_ids=[g1.id, g2.id])

        resp = await superuser_client.get(f"/api/hosts/{host.id}/effective-ca-certs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    async def test_host_override_can_mark_inherited_cert_absent(
        self, superuser_client, db
    ):
        key = await create_ssh_key(db)
        group = await create_group(db, priority=100)
        pem = _make_pem("Shared CA")

        # Add to group as present
        gresp = await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "Shared", "pem_content": pem},
        )
        assert gresp.status_code == 201
        fp = gresp.json()["fingerprint_sha256"]

        # Add host, then host-level absent override with the same PEM
        host = await create_host(db, ssh_key_id=key.id, group_ids=[group.id])
        hresp = await superuser_client.post(
            f"/api/hosts/{host.id}/ca-certs",
            json={"name": "Shared (opt-out)", "pem_content": pem, "state": "absent"},
        )
        assert hresp.status_code == 201

        # Effective merge: should show 1 entry, source=host, state=absent
        eff_resp = await superuser_client.get(
            f"/api/hosts/{host.id}/effective-ca-certs"
        )
        assert eff_resp.status_code == 200
        data = eff_resp.json()
        assert len(data) == 1
        assert data[0]["fingerprint_sha256"] == fp
        assert data[0]["source"] == "host"
        assert data[0]["state"] == "absent"

    async def test_host_with_no_groups_returns_empty(self, superuser_client, db):
        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id, group_ids=[])
        resp = await superuser_client.get(f"/api/hosts/{host.id}/effective-ca-certs")
        assert resp.status_code == 200
        assert resp.json() == []
