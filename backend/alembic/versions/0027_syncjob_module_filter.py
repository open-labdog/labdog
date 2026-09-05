"""Persist which modules a sync job was asked to apply.

A bulk sync stores the literal ``module_type="bulk"`` and passes the real
module list only as a Celery kwarg. That kwarg does not survive a defer:
when the target host is busy the job sits in ``pending``, and the
re-dispatch rebuilds the filter from the row with
``_filter_from_module_type("bulk")``, which returns ``None`` — meaning
*every* module.

So an operator who asked to reapply firewall on a busy host got packages
reinstalled, services restarted and ``/etc/hosts`` rewritten when the
queue drained. The API already half-knew: ``api/sync.py`` returns
``module_filter=None`` on the idempotent-200 path with a comment saying
the row does not persist the filter.

NULL means "not recorded" and falls back to ``module_type`` as before,
which is the correct answer for the per-module endpoints (their
``module_type`` names their one module) and for existing bulk rows, which
did genuinely mean every module.

Revision ID: 0027_syncjob_module_filter
Revises: 0026_run_target_kind
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0027_syncjob_module_filter"
down_revision = "0026_run_target_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sync_jobs",
        sa.Column("module_filter", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sync_jobs", "module_filter")
