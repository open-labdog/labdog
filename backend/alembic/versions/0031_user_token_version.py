"""SEC-30: give sessions something to revoke.

The auth cookie is a stateless JWT. Logging out cleared it in the
browser and did nothing else, so a cookie somebody had already copied
stayed valid for the rest of ``session_lifetime_seconds`` (24h by
default). The same was true after changing a password or after an
administrator reset one — the two moments when revocation is the whole
point.

``users.token_version`` is a session generation. The token carries it as
a ``tv`` claim and every request compares the two, so bumping the column
invalidates every token issued before the bump, immediately and without
a second datastore to consult (a Redis denylist fails open when the
store is unreachable, which is the wrong direction for this).

Existing tokens carry no ``tv`` claim and are rejected, so **every
session is logged out once on upgrade**. That is the safe direction:
accepting a claimless token would leave exactly the tokens this change
exists to revoke working until they expired on their own.

Revision ID: 0031_user_token_version
Revises: 0030_encrypt_webhook_secret
"""

import sqlalchemy as sa

from alembic import op

revision = "0031_user_token_version"
down_revision = "0030_encrypt_webhook_secret"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
