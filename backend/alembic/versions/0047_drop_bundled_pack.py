"""Drop the bundled action pack: every winner is a DB pack.

The image no longer ships pack content, so nothing can win an action
key without an ``action_packs`` row. ``pack_id NULL`` used to mean "the
bundled pack wins" in ``action_resolution`` and in
``action_registry_snapshot``. Those rows now point at nothing. Delete
them and make the column NOT NULL on both tables.

A deleted pin leaves its key to the next registry rebuild. A key that
one pack still declares is uncontested and wins on its own. A key that
several packs declare is unresolved until the operator picks a winner,
because the snapshot rows that would have frozen it are gone too.

Downgrade relaxes the columns again. The deleted rows are not restored.

Revision ID: 0047_drop_bundled_pack
Revises: 0046_remediation_check
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0047_drop_bundled_pack"
down_revision = "0046_remediation_check"
branch_labels = None
depends_on = None

_TABLES = ("action_resolution", "action_registry_snapshot")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(sa.text(f"DELETE FROM {table} WHERE pack_id IS NULL"))
        op.alter_column(table, "pack_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    for table in _TABLES:
        op.alter_column(table, "pack_id", existing_type=sa.Integer(), nullable=True)
