"""Unit tests for CA certificate parsing, validation, and schemas.

These tests are pure-unit (no DB). Merge engine tests live in
test_ca_certs_merge.py because they require an async DB session.
"""

from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pydantic import ValidationError

from app.ca_certs.pem_utils import (
    compute_fingerprint,
    parse_pem_certificate,
    validate_pem_content,
)
from app.ca_certs.schemas import CACertRuleCreate, CACertRuleUpdate

# ---------------------------------------------------------------------------
# Test certificate fixtures (generated in-memory; no fixtures on disk)
# ---------------------------------------------------------------------------


def _make_ca_cert(
    common_name: str = "Test Internal CA",
    is_ca: bool = True,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
) -> str:
    """Generate a self-signed cert (CA or leaf) and return its PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Barricade Test"),
        ]
    )
    nb = not_before or datetime.now(UTC) - timedelta(days=1)
    na = not_after or datetime.now(UTC) + timedelta(days=365)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(nb)
        .not_valid_after(na)
        .add_extension(
            x509.BasicConstraints(ca=is_ca, path_length=None if is_ca else None),
            critical=True,
        )
    )
    cert = builder.sign(private_key=key, algorithm=hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _make_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


# ---------------------------------------------------------------------------
# pem_utils tests
# ---------------------------------------------------------------------------


class TestParsePemCertificate:
    def test_valid_ca_cert_extracts_metadata(self):
        pem = _make_ca_cert("Acme Root CA")
        meta = parse_pem_certificate(pem)
        assert meta.is_ca is True
        assert "Acme Root CA" in meta.subject
        assert "Acme Root CA" in meta.issuer  # self-signed
        assert meta.fingerprint_sha256.count(":") == 31  # 32 bytes => 31 colons
        assert all(c in "0123456789ABCDEF:" for c in meta.fingerprint_sha256)
        assert meta.not_after > meta.not_before

    def test_fingerprint_is_deterministic(self):
        pem = _make_ca_cert("Determinism Test")
        fp1 = compute_fingerprint(pem)
        fp2 = compute_fingerprint(pem)
        assert fp1 == fp2

    def test_two_different_certs_have_different_fingerprints(self):
        pem_a = _make_ca_cert("CA A")
        pem_b = _make_ca_cert("CA B")
        assert compute_fingerprint(pem_a) != compute_fingerprint(pem_b)

    def test_empty_input_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            parse_pem_certificate("")
        with pytest.raises(ValueError, match="empty"):
            parse_pem_certificate("   \n  ")

    def test_garbage_input_rejected(self):
        with pytest.raises(ValueError, match="CERTIFICATE block"):
            parse_pem_certificate("not a certificate")

    def test_private_key_rejected(self):
        pem = _make_private_key_pem()
        with pytest.raises(ValueError, match="CERTIFICATE block"):
            parse_pem_certificate(pem)

    def test_non_ca_cert_rejected(self):
        pem = _make_ca_cert("Leaf Cert", is_ca=False)
        with pytest.raises(ValueError, match="not a CA certificate"):
            parse_pem_certificate(pem)

    def test_validate_pem_content_strips_whitespace(self):
        pem = _make_ca_cert()
        wrapped = f"\n\n  {pem}  \n"
        cleaned = validate_pem_content(wrapped)
        assert cleaned == pem.strip()


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestCACertRuleCreate:
    def test_valid_input_accepted(self):
        pem = _make_ca_cert("Schema Test CA")
        rule = CACertRuleCreate(
            name="Internal Root",
            pem_content=pem,
            state="present",
        )
        assert rule.name == "Internal Root"
        assert rule.state == "present"
        assert rule.comment is None

    def test_default_state_is_present(self):
        rule = CACertRuleCreate(name="X", pem_content=_make_ca_cert())
        assert rule.state == "present"

    def test_invalid_pem_rejected(self):
        with pytest.raises(ValidationError):
            CACertRuleCreate(name="X", pem_content="garbage")

    def test_non_ca_pem_rejected(self):
        with pytest.raises(ValidationError):
            CACertRuleCreate(name="X", pem_content=_make_ca_cert(is_ca=False))

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError, match="empty"):
            CACertRuleCreate(name="   ", pem_content=_make_ca_cert())

    def test_long_name_rejected(self):
        with pytest.raises(ValidationError, match="200"):
            CACertRuleCreate(name="x" * 201, pem_content=_make_ca_cert())

    def test_name_is_stripped(self):
        rule = CACertRuleCreate(name="  My CA  ", pem_content=_make_ca_cert())
        assert rule.name == "My CA"


class TestCACertRuleUpdate:
    def test_partial_update_allowed(self):
        u = CACertRuleUpdate(state="absent")
        assert u.state == "absent"
        assert u.name is None
        assert u.comment is None

    def test_no_pem_field(self):
        # Update schema must NOT accept pem_content — certs are immutable
        u = CACertRuleUpdate(name="renamed")
        assert not hasattr(u, "pem_content")

    def test_empty_name_rejected_on_update(self):
        with pytest.raises(ValidationError, match="empty"):
            CACertRuleUpdate(name="   ")
