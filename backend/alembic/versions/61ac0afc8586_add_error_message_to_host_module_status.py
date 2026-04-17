"""add error_message to host_module_status

Revision ID: 61ac0afc8586
Revises: 10dc6150ee58
Create Date: 2026-03-25 10:50:15.076295

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "61ac0afc8586"
down_revision: str | Sequence[str] | None = "10dc6150ee58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("host_module_status", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("host_module_status", "error_message")
