"""Shared schema validators reused across multiple Pydantic schema modules."""

import re

# POSIX-portable username pattern matching useradd's default policy.
# Mirrors the regex already used in user_mgmt/schemas.py (BUG-49).
LINUX_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def validate_linux_username(v: str) -> str:
    """Validate a Linux/POSIX username against the allow-list regex.

    Accepts names matching ``^[a-z_][a-z0-9_-]{0,31}$`` (max 32 chars,
    lowercase, digits, hyphens, underscores — same policy as useradd).

    Raises:
        ValueError: when *v* does not match.
    """
    if not LINUX_USERNAME_RE.match(v):
        raise ValueError(
            f"ssh_user '{v}' is not a valid Linux username: "
            "must match ^[a-z_][a-z0-9_-]{0,31}$ "
            "(lowercase, digits, hyphens, underscores; max 32 chars; no leading hyphen)"
        )
    return v
