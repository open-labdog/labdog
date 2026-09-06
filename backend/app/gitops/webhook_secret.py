"""SEC-28: the webhook secret is a credential and is stored like one.

``git_repositories.webhook_secret`` held its value in plaintext and the
API returned it, under an inline comment reading "OK to return — it's
for webhook validation, not a credential". It is precisely a credential:
the HMAC key that ``app/api/webhooks.py`` compares every inbound push
signature against. Anyone who could read it — any authenticated user
through ``GET /api/git-repos``, anyone with a database dump, anyone
reading a log that captured a response body — could forge a push
webhook and make LabDog import configuration from a commit of their
choosing.

It was also the only secret in the codebase not held in an
``encrypted_*`` column, which is what made the exception easy to miss.

Encryption matches ``encrypted_https_token`` on the same row:
AES-256-GCM under the master key, no AAD. Keeping the two columns
identical in treatment matters more here than binding the ciphertext to
its row id would gain — a mismatch between the two would be a footgun
for whoever touches this next.
"""

from __future__ import annotations

from app.crypto import decrypt_ssh_key, encrypt_ssh_key, get_master_key
from app.models.git_repository import GitRepository


def set_webhook_secret(repo: GitRepository, secret: str | None) -> None:
    """Store *secret* on *repo*, encrypted. ``None`` clears it."""
    if not secret:
        repo.encrypted_webhook_secret = None
        return
    repo.encrypted_webhook_secret = encrypt_ssh_key(secret, get_master_key())


def get_webhook_secret(repo: GitRepository) -> str | None:
    """The plaintext secret, or ``None`` when none is configured.

    Raises whatever the crypto layer raises on a wrong master key —
    silently returning ``None`` would turn a key-management problem into
    "every webhook is unsigned", which fails open.
    """
    if not repo.encrypted_webhook_secret:
        return None
    return decrypt_ssh_key(repo.encrypted_webhook_secret, get_master_key())
