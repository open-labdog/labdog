"""Scrub secret material out of captured subprocess output before persist.

Used primarily on the stderr of failed git invocations. The caller passes
whatever secret strings it wants masked (typically a PAT); anywhere they
appear in the text is replaced with a fixed marker. SSH private keys
don't end up in git stderr, so the common case is a one-element list
containing a token.
"""

from __future__ import annotations

REDACTED = "***REDACTED***"


def redact(text: str | None, secrets: list[str] | None) -> str | None:
    """Return *text* with every non-empty secret in *secrets* masked.

    A ``None`` input or a ``None``/empty secret list returns *text*
    unchanged. Short secrets (< 4 chars) are skipped to avoid mangling
    incidental common substrings.
    """
    if text is None or not secrets:
        return text
    out = text
    for secret in secrets:
        if not secret or len(secret) < 4:
            continue
        out = out.replace(secret, REDACTED)
    return out
