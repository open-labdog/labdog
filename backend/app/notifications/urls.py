"""The address LabDog puts in links it sends.

Configured, never inferred. The obvious shortcut — build it from the
request that triggered the notification — fails twice: most notifications
are triggered by a webhook or a Celery task with no browser behind it,
and where there is a request, its ``Host`` header is whatever the client
sent. A link built from that is a phishing link waiting for an attacker
to choose the host.

Stdlib only: :mod:`app.settings_service` imports this for validation.
"""

from __future__ import annotations

from urllib.parse import urlsplit


def validate_public_url(raw: str) -> str:
    """Normalise ``raw`` (no trailing slash), or raise ``ValueError``.

    Empty is allowed and means "not set": nothing with a link is sent.
    """
    value = raw.strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(
            "must be an http:// or https:// address, such as https://labdog.example.com"
        )
    if parts.query or parts.fragment:
        raise ValueError("must not have a query string or fragment")
    return value.rstrip("/")
