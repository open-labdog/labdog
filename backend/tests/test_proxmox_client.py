"""Matrix-driven tests for :class:`app.proxmox.client.ProxmoxClient`.

Asserts the ``verify=`` kwarg passed to ``httpx.AsyncClient`` per the BUG-52
verify behavior matrix, plus SSLContext caching and defensive handling of a
malformed stored PEM. These tests mock httpx and build certs in-memory — no
database or network is involved.
"""

from __future__ import annotations

import datetime as dt
import ssl
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.proxmox.client import ProxmoxClient, ProxmoxError


def _make_self_signed_cert() -> tuple[str, bytes]:
    """Return (PEM string, DER bytes) for a fresh self-signed test cert."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pve.test")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    der = cert.public_bytes(serialization.Encoding.DER)
    return pem, der


@asynccontextmanager
async def _capture_async_client():
    """Patch ``httpx.AsyncClient`` and capture the kwargs of each construction.

    Yields a list that accumulates the ``verify`` value for every client built.
    The returned client is a MagicMock whose ``request`` is an async no-op and
    which supports ``async with``.
    """
    captured: list[object] = []

    def _factory(*_args, **kwargs):
        captured.append(kwargs.get("verify"))
        fake = MagicMock()
        response = MagicMock()
        response.is_success = True
        response.json.return_value = {"data": {"version": "8.0"}}

        async def _request(*_a, **_k):
            return response

        fake.request = _request

        async def _aenter(*_a, **_k):
            return fake

        async def _aexit(*_a, **_k):
            return None

        fake.__aenter__ = _aenter
        fake.__aexit__ = _aexit
        return fake

    with patch("app.proxmox.client.httpx.AsyncClient", side_effect=_factory):
        yield captured


@pytest.mark.asyncio
async def test_verify_false_no_ca():
    client = ProxmoxClient("https://pve.test:8006", "root@pam!t", "secret", verify_ssl=False)
    async with _capture_async_client() as captured:
        await client.test_connection()
    assert captured == [False]


@pytest.mark.asyncio
async def test_verify_false_with_ca_ignored():
    pem, _ = _make_self_signed_cert()
    client = ProxmoxClient(
        "https://pve.test:8006", "root@pam!t", "secret", verify_ssl=False, ca_cert_pem=pem
    )
    async with _capture_async_client() as captured:
        await client.test_connection()
    assert captured == [False]


@pytest.mark.asyncio
async def test_verify_true_no_ca():
    client = ProxmoxClient("https://pve.test:8006", "root@pam!t", "secret", verify_ssl=True)
    async with _capture_async_client() as captured:
        await client.test_connection()
    assert captured == [True]


@pytest.mark.asyncio
async def test_verify_true_with_ca_builds_context():
    pem, der = _make_self_signed_cert()
    client = ProxmoxClient(
        "https://pve.test:8006", "root@pam!t", "secret", verify_ssl=True, ca_cert_pem=pem
    )
    async with _capture_async_client() as captured:
        await client.test_connection()
    assert len(captured) == 1
    ctx = captured[0]
    assert isinstance(ctx, ssl.SSLContext)
    # The uploaded CA must be among the context's trusted certs.
    trusted = ctx.get_ca_certs(binary_form=True)
    assert der in trusted


@pytest.mark.asyncio
async def test_ssl_context_cached_across_requests():
    pem, _ = _make_self_signed_cert()
    client = ProxmoxClient(
        "https://pve.test:8006", "root@pam!t", "secret", verify_ssl=True, ca_cert_pem=pem
    )
    async with _capture_async_client() as captured:
        await client.test_connection()
        await client.test_connection()
    assert len(captured) == 2
    # Both requests reuse the single cached SSLContext instance.
    assert captured[0] is captured[1]


@pytest.mark.asyncio
async def test_malformed_ca_raises_proxmox_error():
    client = ProxmoxClient(
        "https://pve.test:8006",
        "root@pam!t",
        "secret",
        verify_ssl=True,
        ca_cert_pem="-----BEGIN CERTIFICATE-----\nnot a real cert\n-----END CERTIFICATE-----",
    )
    with pytest.raises(ProxmoxError):
        client._get_ssl_context()
