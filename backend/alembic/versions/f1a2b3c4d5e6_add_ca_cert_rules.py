"""add ca_cert_rules table

Revision ID: f1a2b3c4d5e6
Revises: e4f6a1c83b2d
Create Date: 2026-04-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e4f6a1c83b2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


certstate = postgresql.ENUM('present', 'absent', name='certstate', create_type=False)


def upgrade() -> None:
    """Create the ca_cert_rules table and certstate enum."""
    certstate.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'ca_cert_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=True),
        sa.Column('host_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('pem_content', sa.Text(), nullable=False),
        sa.Column('fingerprint_sha256', sa.String(length=95), nullable=False),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('issuer', sa.String(length=500), nullable=True),
        sa.Column('not_before', sa.DateTime(timezone=True), nullable=True),
        sa.Column('not_after', sa.DateTime(timezone=True), nullable=True),
        sa.Column('state', certstate, nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            '(group_id IS NOT NULL AND host_id IS NULL) OR '
            '(group_id IS NULL AND host_id IS NOT NULL)',
            name='ck_ca_cert_rules_scope',
        ),
        sa.ForeignKeyConstraint(
            ['group_id'], ['host_groups.id'],
            name=op.f('fk_ca_cert_rules_group_id_host_groups'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['host_id'], ['hosts.id'],
            name=op.f('fk_ca_cert_rules_host_id_hosts'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_ca_cert_rules')),
        sa.UniqueConstraint(
            'group_id', 'fingerprint_sha256',
            name='uq_ca_cert_rules_group_fp',
        ),
        sa.UniqueConstraint(
            'host_id', 'fingerprint_sha256',
            name='uq_ca_cert_rules_host_fp',
        ),
    )


def downgrade() -> None:
    op.drop_table('ca_cert_rules')
    certstate.drop(op.get_bind(), checkfirst=True)
