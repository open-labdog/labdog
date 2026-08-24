"""Secret-redaction tests.

Two properties matter: credentials never survive into the transcript, and
ordinary diagnostic output survives intact — an over-eager redactor that
scrubs hostnames and disk percentages would make the whole feature
useless.
"""

from __future__ import annotations

import pytest

from app.ai.redaction import PLACEHOLDER, redact

SECRETS = [
    ("PASSWORD=hunter2", "hunter2"),
    ("db_password: s3cr3t-value", "s3cr3t-value"),
    ("API_KEY=abcdef123456", "abcdef123456"),
    ("api_token = 'tok_live_abc123'", "tok_live_abc123"),
    ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG", "wJalrXUtnFEMI/K7MDENG"),
    ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
    ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnop"),
    ("sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "sk-ant-api03-abcdef"),
    ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("postgres://user:hunter2@db:5432/labdog", "hunter2"),
    (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij",
        "eyJhbGciOiJIUzI1NiJ9",
    ),
]

BENIGN = [
    "3 units running, 0 failed",
    "/dev/sda1  20G  8.4G  11G  45% /",
    "Linux node1 6.1.0-18-amd64 #1 SMP Debian",
    "load average: 0.15, 0.09, 0.03",
    "openssh-server 1:9.2p1-2+deb12u3 amd64",
    "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*",
    "d41d8cd98f00b204e9800998ecf8427e  /etc/hosts",
    "commit 3f2a9c1e8b7d6a5f4e3c2b1a0d9e8f7c6b5a4d3e",
]


@pytest.mark.parametrize("text,secret", SECRETS)
def test_secrets_are_removed(text: str, secret: str) -> None:
    result = redact(text)
    assert secret not in result, f"{secret!r} survived redaction of {text!r}"
    assert PLACEHOLDER in result


@pytest.mark.parametrize("text", BENIGN)
def test_ordinary_output_is_untouched(text: str) -> None:
    assert redact(text) == text


def test_pem_private_key_body_is_removed() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAxK3vT9pQm2nR5sL8dF6gH1jK4mN7pQ2rS5tU8vW1xY3zA4bC\n"
        "5dE6fG7hI8jK9lM0nO1pQ2rS3tU4vW5xY6zA7bC8dE9fG0hI1jK2lM3nO4pQ5rS6\n"
        "-----END RSA PRIVATE KEY-----"
    )
    result = redact(pem)
    assert "MIIEowIBAAKCAQEAxK3vT9pQm2nR5sL8dF6gH1jK4mN7pQ2rS5tU8vW1xY3zA4bC" not in result
    assert PLACEHOLDER in result


def test_shadow_hashes_are_removed() -> None:
    line = "root:$6$rounds=5000$saltsalt$hashhashhashhash:19000:0:99999:7:::"
    result = redact(line)
    assert "$6$rounds=5000$saltsalt$hashhashhashhash" not in result
    assert result.startswith("root:")


def test_long_base64_blob_is_removed() -> None:
    blob = "A" * 150
    assert blob not in redact(f"key data: {blob}")


def test_short_hex_digests_survive() -> None:
    """A SHA-256 digest is not a secret and stays readable."""
    digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert digest in redact(f"checksum: {digest}")


def test_multiline_output_redacts_only_the_secret_line() -> None:
    text = "\n".join(
        [
            "Reading config...",
            "DB_PASSWORD=supersecret",
            "Listening on port 8080",
        ]
    )
    result = redact(text)
    assert "supersecret" not in result
    assert "Reading config..." in result
    assert "Listening on port 8080" in result


def test_empty_input() -> None:
    assert redact("") == ""
