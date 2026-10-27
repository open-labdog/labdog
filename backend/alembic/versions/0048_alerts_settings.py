"""Alert intake settings are ``alerts.*``, not ``ai.*``.

Recording alerts never needed the AI, and alert-triggered schedules run
actions with the AI off, so the two intake switches move out of the
``ai`` namespace before 1.0.0 freezes setting names. Stored rows are
renamed so an install keeps its values.

Revision ID: 0048_alerts_settings
Revises: 0047_drop_bundled_pack
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0048_alerts_settings"
down_revision = "0047_drop_bundled_pack"
branch_labels = None
depends_on = None

_RENAMES = (
    ("ai.alert_intake_enabled", "alerts.intake_enabled"),
    ("ai.alertmanager_poll_minutes", "alerts.poll_minutes"),
)


def _rename(old: str, new: str) -> None:
    op.execute(
        sa.text(
            "UPDATE app_settings SET key = :new WHERE key = :old "
            "AND NOT EXISTS (SELECT 1 FROM app_settings WHERE key = :new)"
        ).bindparams(old=old, new=new)
    )


def upgrade() -> None:
    for old, new in _RENAMES:
        _rename(old, new)


def downgrade() -> None:
    for old, new in _RENAMES:
        _rename(new, old)
