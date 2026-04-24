"""SSH ``known_hosts`` assembly for git sync.

LabDog does NOT bake in SSH host fingerprints for any provider. Shipping
the wrong fingerprint by accident would either break connections or, in
the worst case, silently enable a MITM — neither outcome is acceptable.

Admins paste the entries they want alongside the pack's SSH key in the
UI. The recommended UX is a helper link next to the textarea that points
at each provider's canonical fingerprint page (GitHub's docs, GitLab's
docs, etc.) — rendered on the frontend; this module only merges whatever
the admin provided.
"""

from __future__ import annotations


def build_known_hosts(user_entries: str | None) -> str:
    """Normalise pasted ``known_hosts`` text into a complete file body.

    Strips blank lines and comments, de-duplicates, trailing newline.
    Empty input produces an empty string — callers should treat that as
    a configuration error (ssh will refuse the connection rather than
    fall back to TOFU, which is the safer default).
    """
    if not user_entries:
        return ""
    seen: set[str] = set()
    out: list[str] = []
    for raw in user_entries.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        out.append(stripped)
    return "\n".join(out) + ("\n" if out else "")
