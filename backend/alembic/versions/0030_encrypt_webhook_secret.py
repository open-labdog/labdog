"""SEC-28: encrypt the webhook secret at rest.

``git_repositories.webhook_secret`` held its value in plaintext and the
API returned it, under a comment claiming it was "not a credential". It
is exactly one: the HMAC key every inbound push webhook is verified
against. Anyone who could read it could forge a push and make LabDog
import configuration from a commit of their choosing — and any
authenticated user could read it, since ``GET /api/git-repos`` returned
it verbatim.

The backfill encrypts existing values in place rather than dropping
them, because discarding them would silently break every configured
webhook — the receiver would start answering 401 and the only symptom
would be pushes that stop importing. Encryption matches
``encrypted_https_token`` on the same row: AES-256-GCM under the master
key, no AAD.

This migration therefore needs the master key, which is why it imports
from ``app``. It is the only migration that does. The key is already
required for the process to start at all (``config._validate_required``),
so there is no new failure mode, but the import is deferred into
``upgrade()`` so that merely loading the revision file does not pull the
app in.

Downgrade decrypts back to plaintext. It is offered so a rollback is
possible, not because plaintext is an acceptable resting state.

Revision ID: 0030_encrypt_webhook_secret
Revises: 0029_git_repo_host_key
"""

import sqlalchemy as sa

from alembic import op

revision = "0030_encrypt_webhook_secret"
down_revision = "0029_git_repo_host_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.crypto import encrypt_ssh_key, get_master_key

    op.add_column(
        "git_repositories",
        sa.Column("encrypted_webhook_secret", sa.LargeBinary(), nullable=True),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, webhook_secret FROM git_repositories "
            "WHERE webhook_secret IS NOT NULL AND webhook_secret <> ''"
        )
    ).fetchall()
    if rows:
        master_key = get_master_key()
        for repo_id, secret in rows:
            conn.execute(
                sa.text(
                    "UPDATE git_repositories SET encrypted_webhook_secret = :blob WHERE id = :id"
                ),
                {"blob": encrypt_ssh_key(secret, master_key), "id": repo_id},
            )

    op.drop_column("git_repositories", "webhook_secret")


def downgrade() -> None:
    from app.crypto import decrypt_ssh_key, get_master_key

    op.add_column(
        "git_repositories",
        sa.Column("webhook_secret", sa.String(length=200), nullable=True),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, encrypted_webhook_secret FROM git_repositories "
            "WHERE encrypted_webhook_secret IS NOT NULL"
        )
    ).fetchall()
    if rows:
        master_key = get_master_key()
        for repo_id, blob in rows:
            conn.execute(
                sa.text("UPDATE git_repositories SET webhook_secret = :secret WHERE id = :id"),
                {"secret": decrypt_ssh_key(bytes(blob), master_key), "id": repo_id},
            )

    op.drop_column("git_repositories", "encrypted_webhook_secret")
