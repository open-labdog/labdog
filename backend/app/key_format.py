"""Decoding rules for the AES master key, importable from anywhere.

Lives at the top of ``app`` rather than inside ``app.crypto`` because
``app.config`` needs these rules to reject a malformed key at startup
(SEC-31), and importing anything from ``app.crypto`` runs that package's
``__init__``, which imports ``app.crypto.key_management``, which imports
``app.config`` — a cycle that fails while ``app.config`` is still
executing. Nothing here imports from ``app`` at all, so it is safe from
anywhere.

The rules themselves are unchanged; this is the same code that used to
sit inside ``get_master_key``, moved so both callers share one copy
rather than two that can drift.
"""

from __future__ import annotations

import base64
import binascii

#: Accept both base64 alphabets so an operator pasting a url-safe key
#: from one generator and a standard one from another both work.
_URLSAFE_TO_STANDARD = str.maketrans({"-": "+", "_": "/"})

KEY_BYTES = 32


def decode_master_key(raw: str) -> bytes:
    """Decode *raw* to exactly 32 key bytes, or raise ``ValueError``.

    ``validate=True`` is load-bearing: without it ``b64decode`` silently
    drops characters it does not recognise and returns a short key,
    which was the original BUG-45 failure mode — a typo in the key
    produced a working-looking but different key.
    """
    normalised = raw.translate(_URLSAFE_TO_STANDARD)
    padded = normalised + "=" * (-len(normalised) % 4)
    try:
        key = base64.b64decode(padded, validate=True)
    except binascii.Error as e:
        raise ValueError(
            f"ENCRYPTION_KEY is not valid base64 (standard or url-safe, {KEY_BYTES} bytes): {e}"
        ) from e
    if len(key) != KEY_BYTES:
        raise ValueError(f"ENCRYPTION_KEY must decode to exactly {KEY_BYTES} bytes, got {len(key)}")
    return key
