"""Integration tests for CA cert action endpoints and auto-enqueue."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .conftest import create_group, create_host, create_ssh_key

pytestmark = pytest.mark.integration


def _make_pem(common_name: str = "Test CA") -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
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
# Manual deploy endpoints
# ---------------------------------------------------------------------------


class TestManualDeploy:
    async def test_deploy_to_host_creates_action_run(self, superuser_client, db):
        key = await create_ssh_key(db)
        group = await create_group(db, priority=100)
        await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "Acme CA", "pem_content": _make_pem()},
        )
        host = await create_host(db, ssh_key_id=key.id, group_ids=[group.id])

        with patch("app.tasks.ca_cert_action.run_ca_cert_action.delay") as mock_delay:
            resp = await superuser_client.post(
                f"/api/ca-certs/hosts/{host.id}/deploy"
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["host_id"] == host.id
        assert data["status"] == "pending"
        mock_delay.assert_called_once()

    async def test_deploy_rejects_host_without_ssh_key(self, superuser_client, db):
        host = await create_host(db, ssh_key_id=None)
        resp = await superuser_client.post(
            f"/api/ca-certs/hosts/{host.id}/deploy"
        )
        assert resp.status_code == 400

    async def test_deploy_to_unknown_host_404(self, superuser_client):
        resp = await superuser_client.post("/api/ca-certs/hosts/999999/deploy")
        assert resp.status_code == 404

    async def test_deploy_to_group_dispatches_per_host(self, superuser_client, db):
        key = await create_ssh_key(db)
        group = await create_group(db, priority=100)
        await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "X", "pem_content": _make_pem()},
        )
        await create_host(db, ssh_key_id=key.id, group_ids=[group.id], ip="10.0.0.1")
        await create_host(db, ssh_key_id=key.id, group_ids=[group.id], ip="10.0.0.2")

        with patch("app.tasks.ca_cert_action.run_ca_cert_action.delay") as mock_delay:
            resp = await superuser_client.post(
                f"/api/ca-certs/groups/{group.id}/deploy"
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["triggered"] == 2
        assert data["total_hosts"] == 2
        assert mock_delay.call_count == 2


# ---------------------------------------------------------------------------
# Auto-enqueue on host-add-to-group
# ---------------------------------------------------------------------------


class TestAutoEnqueue:
    async def test_adding_host_to_group_with_certs_enqueues_action(
        self, superuser_client, db
    ):
        key = await create_ssh_key(db)
        group = await create_group(db, priority=100)
        await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "AutoCA", "pem_content": _make_pem()},
        )
        host = await create_host(db, ssh_key_id=key.id, group_ids=[])

        with patch("app.tasks.ca_cert_action.run_ca_cert_action.delay") as mock_delay:
            resp = await superuser_client.post(
                f"/api/groups/{group.id}/hosts",
                json={"host_ids": [host.id]},
            )
        assert resp.status_code == 200
        mock_delay.assert_called_once()

    async def test_adding_host_to_group_without_certs_does_not_enqueue(
        self, superuser_client, db
    ):
        key = await create_ssh_key(db)
        group = await create_group(db, priority=100)
        # No certs in this group
        host = await create_host(db, ssh_key_id=key.id, group_ids=[])

        with patch("app.tasks.ca_cert_action.run_ca_cert_action.delay") as mock_delay:
            resp = await superuser_client.post(
                f"/api/groups/{group.id}/hosts",
                json={"host_ids": [host.id]},
            )
        assert resp.status_code == 200
        mock_delay.assert_not_called()

    async def test_adding_host_already_in_group_skips_enqueue(
        self, superuser_client, db
    ):
        key = await create_ssh_key(db)
        group = await create_group(db, priority=100)
        await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "X", "pem_content": _make_pem()},
        )
        host = await create_host(db, ssh_key_id=key.id, group_ids=[group.id])

        with patch("app.tasks.ca_cert_action.run_ca_cert_action.delay") as mock_delay:
            resp = await superuser_client.post(
                f"/api/groups/{group.id}/hosts",
                json={"host_ids": [host.id]},
            )
        assert resp.status_code == 200
        # Already a member — no new action should fire
        mock_delay.assert_not_called()

    async def test_host_without_ssh_key_does_not_enqueue(
        self, superuser_client, db
    ):
        group = await create_group(db, priority=100)
        await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "X", "pem_content": _make_pem()},
        )
        host = await create_host(db, ssh_key_id=None, group_ids=[])

        with patch("app.tasks.ca_cert_action.run_ca_cert_action.delay") as mock_delay:
            resp = await superuser_client.post(
                f"/api/groups/{group.id}/hosts",
                json={"host_ids": [host.id]},
            )
        assert resp.status_code == 200
        mock_delay.assert_not_called()


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------


class TestRunHistory:
    async def test_list_host_runs_empty(self, superuser_client, db):
        key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=key.id)
        resp = await superuser_client.get(f"/api/ca-certs/hosts/{host.id}/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_host_runs_after_deploy(self, superuser_client, db):
        key = await create_ssh_key(db)
        group = await create_group(db, priority=100)
        await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "X", "pem_content": _make_pem()},
        )
        host = await create_host(db, ssh_key_id=key.id, group_ids=[group.id])

        with patch("app.tasks.ca_cert_action.run_ca_cert_action.delay"):
            await superuser_client.post(f"/api/ca-certs/hosts/{host.id}/deploy")

        resp = await superuser_client.get(f"/api/ca-certs/hosts/{host.id}/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["host_id"] == host.id
        assert data[0]["status"] == "pending"

    async def test_get_run_returns_single(self, superuser_client, db):
        key = await create_ssh_key(db)
        group = await create_group(db, priority=100)
        await superuser_client.post(
            f"/api/groups/{group.id}/ca-certs",
            json={"name": "X", "pem_content": _make_pem()},
        )
        host = await create_host(db, ssh_key_id=key.id, group_ids=[group.id])

        with patch("app.tasks.ca_cert_action.run_ca_cert_action.delay"):
            create_resp = await superuser_client.post(
                f"/api/ca-certs/hosts/{host.id}/deploy"
            )
        run_id = create_resp.json()["id"]

        resp = await superuser_client.get(f"/api/ca-certs/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == run_id

    async def test_get_run_404_for_unknown(self, superuser_client):
        resp = await superuser_client.get("/api/ca-certs/runs/999999")
        assert resp.status_code == 404
