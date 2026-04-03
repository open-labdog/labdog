import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE = 12  # 96-bit nonce for AES-GCM


def encrypt_ssh_key(
    plaintext_key: str, master_key: bytes, context_id: str = "",
) -> bytes:
    """Encrypt SSH private key using AES-256-GCM.

    *context_id* is used as Associated Authenticated Data (AAD) to bind
    the ciphertext to its intended record (e.g. ``"ssh_key:42"``).
    Returns: nonce (12 bytes) || ciphertext || tag (16 bytes)
    """
    aesgcm = AESGCM(master_key)
    nonce = os.urandom(NONCE_SIZE)
    aad = context_id.encode() if context_id else None
    ciphertext = aesgcm.encrypt(nonce, plaintext_key.encode(), aad)
    return nonce + ciphertext  # ciphertext already includes the 16-byte tag


def decrypt_ssh_key(
    encrypted_data: bytes, master_key: bytes, context_id: str = "",
) -> str:
    """Decrypt SSH private key. Raises InvalidTag if key is wrong or context mismatches."""
    nonce = encrypted_data[:NONCE_SIZE]
    ciphertext = encrypted_data[NONCE_SIZE:]
    aesgcm = AESGCM(master_key)
    aad = context_id.encode() if context_id else None
    return aesgcm.decrypt(nonce, ciphertext, aad).decode()
