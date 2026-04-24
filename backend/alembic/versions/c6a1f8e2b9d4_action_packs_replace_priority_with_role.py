"""action_packs: replace priority with role

Revision ID: c6a1f8e2b9d4
Revises: b4e8d1c2f3a9
Create Date: 2026-04-24 09:00:00.000000

Admins should never type integer priorities. Priority is now derived
from (source_type, role) — roles are ``default`` (canonical baseline)
or ``override`` (customisation on top). Local packs don't use role;
they're always placed above all git tiers.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6a1f8e2b9d4"
down_revision: str | Sequence[str] | None = "b4e8d1c2f3a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


pack_role = postgresql.ENUM(
    "default",
    "override",
    name="packrole",
    create_type=False,
)


def upgrade() -> None:
    pack_role.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "action_packs",
        sa.Column(
            "role",
            pack_role,
            nullable=False,
            server_default="override",
        ),
    )

    # Backfill: packs whose old priority was <= 10 become 'default',
    # everything else becomes 'override'. Matches the semantic we used
    # before the rename.
    op.execute(
        "UPDATE action_packs SET role = 'default' WHERE priority <= 10"
    )

    op.drop_column("action_packs", "priority")


def downgrade() -> None:
    op.add_column(
        "action_packs",
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
    )
    op.execute(
        "UPDATE action_packs SET priority = CASE "
        "WHEN role = 'default' THEN 10 "
        "ELSE 100 END"
    )
    op.drop_column("action_packs", "role")
    pack_role.drop(op.get_bind(), checkfirst=True)
