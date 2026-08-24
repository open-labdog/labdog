"""Record when a provider's credential was written.

`claude setup-token` mints an OAuth token that lasts one year, and
Anthropic's own documentation warns that a session running unattended
"stops making progress once the credential expires and can't recover
until you sign in again". LabDog's scheduled AI checks are exactly that
kind of session, so the first sign of an expired token would otherwise be
nightly runs quietly failing.

``updated_at`` cannot answer the question — it moves whenever any field on
the row is edited — so the write time of the credential itself is tracked
separately.

Backfilled from ``updated_at`` rather than left NULL. It is an
overestimate of the credential's age at worst, and the alternative is
every existing provider reporting "unknown" forever with no way to
distinguish that from a fresh one.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022_ai_credential_set_at"
down_revision = "0021_ai_snapshot_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_providers",
        sa.Column("credential_set_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE ai_providers SET credential_set_at = updated_at WHERE encrypted_api_key IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("ai_providers", "credential_set_at")
