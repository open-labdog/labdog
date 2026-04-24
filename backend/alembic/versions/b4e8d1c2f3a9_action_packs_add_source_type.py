"""action_packs: add source_type

Revision ID: b4e8d1c2f3a9
Revises: a7c3e9f2d1b8
Create Date: 2026-04-24 08:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e8d1c2f3a9"
down_revision: str | Sequence[str] | None = "a7c3e9f2d1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


pack_source_type = postgresql.ENUM(
    "git",
    "local",
    name="packsourcetype",
    create_type=False,
)


def upgrade() -> None:
    pack_source_type.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "action_packs",
        sa.Column(
            "source_type",
            pack_source_type,
            nullable=False,
            server_default="git",
        ),
    )

    # Local packs must have auth_type='none' (no clone happens, so SSH
    # keys / tokens make no sense). The existing auth-consistency
    # constraint already forces creds to be NULL when auth_type='none',
    # so this single-line addition is sufficient.
    op.create_check_constraint(
        "ck_action_packs_local_requires_no_auth",
        "action_packs",
        "source_type = 'git' OR auth_type = 'none'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_action_packs_local_requires_no_auth",
        "action_packs",
        type_="check",
    )
    op.drop_column("action_packs", "source_type")
    pack_source_type.drop(op.get_bind(), checkfirst=True)
