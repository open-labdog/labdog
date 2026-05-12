import base64
import binascii
import os

from cryptography.exceptions import UnsupportedAlgorithm

from app.config import settings


def get_master_key() -> bytes:
    """Load master key from ENCRYPTION_KEY env var.

    Must decode to exactly 32 bytes. Accepts both standard
    (``+``/``/``) and url-safe (``-``/``_``) base64 alphabets, with
    or without ``=`` padding. Invalid characters are rejected with a
    clear error rather than being silently dropped.
    """
    raw = settings.security.encryption_key
    # Normalise url-safe chars to standard so a single strict decode
    # accepts either alphabet. Without validate=True, b64decode would
    # silently drop unknown chars and produce a shorter byte string —
    # the original BUG-45 failure mode.
    normalised = raw.translate(_URLSAFE_TO_STANDARD)
    padded = normalised + "=" * (-len(normalised) % 4)
    try:
        key = base64.b64decode(padded, validate=True)
    except binascii.Error as e:
        raise ValueError(
            "ENCRYPTION_KEY is not valid base64 "
            f"(standard or url-safe, 32 bytes): {e}"
        ) from e
    if len(key) != 32:
        raise ValueError(f"ENCRYPTION_KEY must decode to exactly 32 bytes, got {len(key)}")
    return key


_URLSAFE_TO_STANDARD = str.maketrans({"-": "+", "_": "/"})


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
