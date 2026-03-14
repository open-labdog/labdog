import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE = 12  # 96-bit nonce for AES-GCM


def encrypt_ssh_key(plaintext_key: str, master_key: bytes) -> bytes:
    """Encrypt SSH private key using AES-256-GCM.
    Returns: nonce (12 bytes) || ciphertext || tag (16 bytes)
    """
    aesgcm = AESGCM(master_key)
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = aesgcm.encrypt(nonce, plaintext_key.encode(), None)
    return nonce + ciphertext  # ciphertext already includes the 16-byte tag


def decrypt_ssh_key(encrypted_data: bytes, master_key: bytes) -> str:
    """Decrypt SSH private key. Raises InvalidTag if key is wrong."""
    nonce = encrypted_data[:NONCE_SIZE]
    ciphertext = encrypted_data[NONCE_SIZE:]
    aesgcm = AESGCM(master_key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode()
