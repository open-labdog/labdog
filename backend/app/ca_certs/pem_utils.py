"""Utilities for parsing and validating PEM-encoded X.509 CA certificates.

Used by the API layer to extract metadata (fingerprint, subject, issuer,
validity dates) from PEM content provided by users when defining CA cert
rules. Metadata is stored alongside the PEM in the database for display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes


@dataclass(frozen=True)
class CertMetadata:
    fingerprint_sha256: str  # colon-separated uppercase hex
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    is_ca: bool


def _format_fingerprint(raw: bytes) -> str:
    return ":".join(f"{b:02X}" for b in raw)


def _name_to_string(name: x509.Name) -> str:
    # rfc4514_string() is the standard machine-parseable form
    try:
        return name.rfc4514_string()
    except Exception:
        return str(name)


def parse_pem_certificate(pem_content: str) -> CertMetadata:
    """Parse a PEM-encoded certificate and extract its metadata.

    Raises ValueError if the input is not a valid PEM certificate, if it
    contains a private key instead of a certificate, or if the cert is not
    a CA certificate (basicConstraints CA:FALSE).
    """
    if not pem_content or not pem_content.strip():
        raise ValueError("PEM content is empty")

    cleaned = pem_content.strip()
    if "-----BEGIN CERTIFICATE-----" not in cleaned:
        raise ValueError(
            "PEM content does not contain a CERTIFICATE block. "
            "Make sure you are not pasting a private key."
        )

    try:
        cert = x509.load_pem_x509_certificate(cleaned.encode("utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to parse PEM certificate: {e}") from e

    is_ca = False
    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        is_ca = bool(bc.value.ca)
    except x509.ExtensionNotFound:
        is_ca = False

    if not is_ca:
        raise ValueError(
            "Certificate is not a CA certificate (basicConstraints extension missing or CA:FALSE)"
        )

    fingerprint = _format_fingerprint(cert.fingerprint(hashes.SHA256()))

    return CertMetadata(
        fingerprint_sha256=fingerprint,
        subject=_name_to_string(cert.subject),
        issuer=_name_to_string(cert.issuer),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        is_ca=True,
    )


def validate_pem_content(pem_content: str) -> str:
    """Strip and validate PEM content, returning the cleaned form.

    Used as a Pydantic field validator. Raises ValueError on invalid input.
    """
    cleaned = (pem_content or "").strip()
    parse_pem_certificate(cleaned)  # raises if invalid
    return cleaned


def compute_fingerprint(pem_content: str) -> str:
    """Compute the SHA-256 fingerprint of a PEM cert as colon-separated hex."""
    return parse_pem_certificate(pem_content).fingerprint_sha256
