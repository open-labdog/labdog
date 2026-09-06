import base64
import os

from cryptography.exceptions import UnsupportedAlgorithm

from app.config import settings
from app.key_format import decode_master_key


def get_master_key() -> bytes:
    """Load master key from ENCRYPTION_KEY env var.

    Must decode to exactly 32 bytes. Accepts both standard
    (``+``/``/``) and url-safe (``-``/``_``) base64 alphabets, with
    or without ``=`` padding. Invalid characters are rejected with a
    clear error rather than being silently dropped.

    The rules live in :mod:`app.key_format` so that
    ``app.config`` can apply the same ones at startup (SEC-31) without
    importing this module, which imports ``app.config``.
    """
    return decode_master_key(settings.security.encryption_key)


def generate_master_key() -> str:
    """Generate a new 32-byte random key, return as base64 string for .env."""
    return base64.b64encode(os.urandom(32)).decode()


def validate_ssh_key_no_passphrase(private_key_text: str) -> None:
    """Raise ValueError if SSH key has a passphrase. V1 limitation."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        load_pem_private_key(private_key_text.encode(), password=None)
    except TypeError:
        raise ValueError("SSH keys with passphrases are not supported in v1")
    except (ValueError, UnsupportedAlgorithm):
        # Not a PEM key or unsupported format — let it through, will fail later at use time
        pass


if __name__ == "__main__":
    print("Generated ENCRYPTION_KEY:", generate_master_key())
