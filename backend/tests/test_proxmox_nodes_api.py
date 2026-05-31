"""API tests for per-node Proxmox CA certificate trust (BUG-52).

Covers create/update/clear flows, response shape (metadata only, never the
raw PEM), and PEM validation (invalid / oversized → 422). Uses the conftest
savepoint-session + dependency-override pattern via the ``client`` fixture.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

pytestmark = pytest.mark.integration

_BASE = "/api/proxmox/nodes"


def _make_cert_pem() -> tuple[str, str]:
    """Return (PEM string, expected SHA-256 fingerprint hex)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pve.test")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    return pem, fingerprint


def _body(**overrides) -> dict:
    body = {
        "name": f"pve-{uuid.uuid4().hex[:8]}",
        "api_url": "https://pve.example.com:8006",
        "token_id": "root@pam!labdog",
        "token_secret": "secret-token-value",
        "verify_ssl": True,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_create_with_ca(regular_user_client):
    pem, fingerprint = _make_cert_pem()
    resp = await regular_user_client.post(_BASE, json=_body(ca_cert_pem=pem))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["has_ca_cert"] is True
    assert data["ca_cert_fingerprint"] == fingerprint
    assert "ca_cert_pem" not in data


@pytest.mark.asyncio
async def test_create_without_ca(regular_user_client):
    resp = await regular_user_client.post(_BASE, json=_body())
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["has_ca_cert"] is False
    assert data["ca_cert_fingerprint"] is None


@pytest.mark.asyncio
async def test_put_omit_leaves_ca_unchanged(regular_user_client):
    pem, fingerprint = _make_cert_pem()
    created = (await regular_user_client.post(_BASE, json=_body(ca_cert_pem=pem))).json()

    resp = await regular_user_client.put(f"{_BASE}/{created['id']}", json={"name": created["name"]})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_ca_cert"] is True
    assert data["ca_cert_fingerprint"] == fingerprint


@pytest.mark.asyncio
async def test_put_new_ca_replaces(regular_user_client):
    pem1, _ = _make_cert_pem()
    created = (await regular_user_client.post(_BASE, json=_body(ca_cert_pem=pem1))).json()

    pem2, fingerprint2 = _make_cert_pem()
    resp = await regular_user_client.put(f"{_BASE}/{created['id']}", json={"ca_cert_pem": pem2})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_ca_cert"] is True
    assert data["ca_cert_fingerprint"] == fingerprint2


@pytest.mark.asyncio
async def test_put_empty_string_clears_ca(regular_user_client):
    pem, _ = _make_cert_pem()
    created = (await regular_user_client.post(_BASE, json=_body(ca_cert_pem=pem))).json()

    resp = await regular_user_client.put(f"{_BASE}/{created['id']}", json={"ca_cert_pem": ""})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_ca_cert"] is False
    assert data["ca_cert_fingerprint"] is None


@pytest.mark.asyncio
async def test_get_never_returns_raw_pem(regular_user_client):
    pem, _ = _make_cert_pem()
    created = (await regular_user_client.post(_BASE, json=_body(ca_cert_pem=pem))).json()

    resp = await regular_user_client.get(f"{_BASE}/{created['id']}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "ca_cert_pem" not in data
    assert "BEGIN CERTIFICATE" not in resp.text
    assert data["has_ca_cert"] is True

    list_resp = await regular_user_client.get(_BASE)
    assert "BEGIN CERTIFICATE" not in list_resp.text


@pytest.mark.asyncio
async def test_create_invalid_pem_rejected(regular_user_client):
    body = _body(ca_cert_pem="this is not a certificate")
    resp = await regular_user_client.post(_BASE, json=body)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_create_oversized_pem_rejected(regular_user_client):
    oversized = (
        "-----BEGIN CERTIFICATE-----\n" + ("A" * (65 * 1024)) + "\n-----END CERTIFICATE-----"
    )
    resp = await regular_user_client.post(_BASE, json=_body(ca_cert_pem=oversized))
    assert resp.status_code == 422, resp.text
