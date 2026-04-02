"""add chain policy columns to host_groups

Revision ID: b2e4f8a31c9d
Revises: 3f8c1a92e047
Create Date: 2026-04-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2e4f8a31c9d'
down_revision: Union[str, Sequence[str], None] = '3f8c1a92e047'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('host_groups', sa.Column('input_policy', sa.String(6), nullable=True))
    op.add_column('host_groups', sa.Column('output_policy', sa.String(6), nullable=True))


def downgrade() -> None:
    op.drop_column('host_groups', 'output_policy')
    op.drop_column('host_groups', 'input_policy')
