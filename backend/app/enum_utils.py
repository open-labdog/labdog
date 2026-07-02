"""Small helpers for StrEnum-or-plain-string values.

SQLAlchemy columns typed as a ``StrEnum`` come back as the enum member on a
freshly-constructed object but as a plain string on a reloaded one. ``enum_str``
normalizes both to the underlying string so call sites don't each re-implement
the ``.value if hasattr(...) else str(...)`` coercion.
"""

from typing import Any


def enum_str(value: Any) -> str:
    """Return the underlying string of a StrEnum member (or a plain string)."""
    return value.value if hasattr(value, "value") else str(value)
