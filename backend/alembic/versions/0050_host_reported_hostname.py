"""Add ``hosts.reported_hostname``.

The name the host calls itself, as facts collection reads it
(``hostname -f`` when that names the kernel hostname, else ``hostname``).
The hosts-file merge maps it to 127.0.1.1, the line a synced
``/etc/hosts`` used to drop (BUG-115).

Existing rows start with NULL and fill in at the next facts collection;
until then the merge falls back to the host's LabDog name.

Revision ID: 0050_host_reported_hostname
Revises: 0049_alert_actions
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0050_host_reported_hostname"
down_revision = "0049_alert_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hosts",
        sa.Column("reported_hostname", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hosts", "reported_hostname")
