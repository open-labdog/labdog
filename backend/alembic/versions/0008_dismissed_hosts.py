"""Add ``dismissed_hosts`` table.

Hosts dismissed from the pending-review queue are now remembered so that
scheduled scans never re-surface them.  A manual scan (``is_manual=True``)
bypasses this table so an operator can re-review an IP if their intent
changes.

Revision ID: 0008_dismissed_hosts
Revises: 0007_ssh_session_transcript
Create Date: 2026-05-23
"""

from __future__ import annotations

from alembic import op

revision = "0008_dismissed_hosts"
down_revision = "0007_ssh_session_transcript"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dismissed_hosts (
            id                   SERIAL       PRIMARY KEY,
            scan_config_id       INTEGER      NOT NULL
                                              REFERENCES scan_configs(id) ON DELETE CASCADE,
            ip_address           VARCHAR(45)  NOT NULL,
            dismissed_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            dismissed_by_user_id INTEGER      REFERENCES users(id) ON DELETE SET NULL,
            CONSTRAINT uq_dismissed_scan_ip UNIQUE (scan_config_id, ip_address)
        )
    """)
    op.execute("CREATE INDEX ix_dismissed_hosts_scan_config_id ON dismissed_hosts (scan_config_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dismissed_hosts")
