import base64
import os

import pytest

from app.config import settings
from app.crypto.encryption import decrypt_ssh_key, encrypt_ssh_key
from app.crypto.key_management import generate_master_key, get_master_key


def _make_key() -> bytes:
    return base64.b64decode(generate_master_key())


def test_encrypt_decrypt_roundtrip():
    key = _make_key()
    original = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\ntest key content\n-----END OPENSSH PRIVATE KEY-----"
    )
    encrypted = encrypt_ssh_key(original, key)
    decrypted = decrypt_ssh_key(encrypted, key)
    assert decrypted == original


def test_wrong_key_fails():
    key1 = _make_key()
    key2 = _make_key()
    encrypted = encrypt_ssh_key("test data", key1)
    with pytest.raises(Exception):  # InvalidTag
        decrypt_ssh_key(encrypted, key2)


def test_encrypted_data_is_different_each_time():
    key = _make_key()
    enc1 = encrypt_ssh_key("same data", key)
    enc2 = encrypt_ssh_key("same data", key)
    assert enc1 != enc2  # different nonces


def test_generate_master_key_is_32_bytes():
    b64key = generate_master_key()
    raw = base64.b64decode(b64key)
    assert len(raw) == 32


@pytest.fixture
def restore_encryption_key():
    saved = settings.security.encryption_key
    try:
        yield
    finally:
        settings.security.encryption_key = saved


def test_get_master_key_accepts_standard_base64(restore_encryption_key):
    raw_bytes = os.urandom(32)
    settings.security.encryption_key = base64.b64encode(raw_bytes).decode()
    assert get_master_key() == raw_bytes


def test_get_master_key_accepts_urlsafe_base64(restore_encryption_key):
    # BUG-45: url-safe input (chars '-' / '_') used to be silently truncated.
    # Force url-safe chars in the output by picking bytes that base64-encode
    # to include '+' / '/' under the standard alphabet.
    raw_bytes = bytes([0xFB, 0xEF]) + os.urandom(30)
    urlsafe = base64.urlsafe_b64encode(raw_bytes).decode()
    assert "-" in urlsafe or "_" in urlsafe, "test setup: expected url-safe-specific char"
    settings.security.encryption_key = urlsafe
    assert get_master_key() == raw_bytes


def test_get_master_key_rejects_garbage(restore_encryption_key):
    settings.security.encryption_key = "this is not base64!!!"
    with pytest.raises(ValueError, match="not valid base64"):
        get_master_key()


def test_get_master_key_rejects_wrong_length(restore_encryption_key):
    settings.security.encryption_key = base64.b64encode(b"too short").decode()
    with pytest.raises(ValueError, match="must decode to exactly 32 bytes"):
        get_master_key()
