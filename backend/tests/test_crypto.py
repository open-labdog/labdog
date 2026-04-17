import base64

import pytest

from app.crypto.encryption import decrypt_ssh_key, encrypt_ssh_key
from app.crypto.key_management import generate_master_key


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
