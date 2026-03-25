"""create app_settings table

Revision ID: a1b2c3d4e5f6
Revises: 61ac0afc8586
Create Date: 2026-03-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '61ac0afc8586'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = op.create_table(
        'app_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('value_type', sa.String(length=20), server_default='string', nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_index('ix_app_settings_key', 'app_settings', ['key'])

    # Seed default values
    op.bulk_insert(table, [
        {"key": "drift.check_interval_minutes", "value": "30", "value_type": "int", "description": "Minutes between automatic drift checks"},
        {"key": "ssh.connect_timeout", "value": "10", "value_type": "int", "description": "SSH connection timeout in seconds"},
        {"key": "ansible.playbook_timeout", "value": "300", "value_type": "int", "description": "Ansible playbook execution timeout in seconds"},
        {"key": "discovery.scan_timeout", "value": "1.0", "value_type": "float", "description": "Per-host TCP scan timeout during discovery (seconds)"},
        {"key": "discovery.max_concurrent", "value": "100", "value_type": "int", "description": "Maximum concurrent connections during network scan"},
        {"key": "ssh.idle_timeout_seconds", "value": "1800", "value_type": "int", "description": "SSH terminal idle timeout before auto-disconnect (seconds)"},
        {"key": "logging.audit_retention_days", "value": "90", "value_type": "int", "description": "Days to retain audit log entries"},
        {"key": "logging.level", "value": "info", "value_type": "string", "description": "Application log level"},
    ])


def downgrade() -> None:
    op.drop_index('ix_app_settings_key')
    op.drop_table('app_settings')
