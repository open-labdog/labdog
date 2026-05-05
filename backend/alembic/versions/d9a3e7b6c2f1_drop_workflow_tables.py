"""backfill scheduled_actions and drop legacy workflow tables

Revision ID: d9a3e7b6c2f1
Revises: c8d5f4a2e6b1
Create Date: 2026-05-05 14:30:00.000000

This migration finalises the unification of UpdateWorkflow into the
unified ``scheduled_actions`` model. The forward path:

1. Copy each ``update_workflows`` row into ``scheduled_actions`` with
   ``target_kind='group'``, action key + parameters preserved verbatim,
   and the universal destructive-flow flags mapped from the legacy
   columns.
2. Drop ``workflow_host_runs`` (FK to ``workflow_runs``).
3. Drop ``workflow_runs`` (FK to ``update_workflows``).
4. Drop ``update_workflows``.
5. Drop the three Postgres enums (workflowhoststatus, workflowrunstatus,
   workflowstep) that only those tables used.

Field disposition for fields that don't survive:

- ``verification_prompt`` — dropped. Nothing reads it; verification is
  by playbook health-checks.
- ``auto_reboot`` — dropped. Reboot now runs unconditionally inside the
  linux-upgrade action's playbook; per-action reboot toggles, if ever
  needed, belong on the action manifest as parameters.

The ``qemu-guest-agent`` PackageRule auto-add side-effect that the
legacy ``PUT /groups/{id}/workflow`` carried is gone with no
replacement (footgun: silently mutated package state when an unrelated
flag toggled).

The downgrade path recreates the three legacy tables and reverse-
migrates ``scheduled_actions`` rows whose shape matches the upgrade-
flow contract. It is **lossy** in two ways:

- Built-in pseudo-action rows (``_builtin.*``) didn't exist
  pre-migration and have no place in ``update_workflows``.
- ``update_workflows`` had ``UNIQUE(group_id)`` (one workflow row
  per group). After the migration a group can carry multiple
  ``scheduled_actions`` rows (one per action_key); on downgrade only
  the first one per group survives via ``ON CONFLICT DO NOTHING``.
  The selection is non-deterministic — there's no "primary" row to
  pick. Operators downgrading a fully-populated install should
  expect data loss they'll have to re-create from the upstream
  snapshot.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9a3e7b6c2f1"
down_revision: str | Sequence[str] | None = "c8d5f4a2e6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill + drop legacy workflow tables."""
    op.execute(
        """
        INSERT INTO scheduled_actions (
            target_kind, target_id, action_key, parameters,
            schedule_cron, enabled, snapshot_enabled, auto_rollback,
            verify_enabled, batch_size, created_at, updated_at
        )
        SELECT
            'group', uw.group_id, uw.action_key, uw.action_parameters,
            uw.schedule_cron, uw.enabled, uw.pre_update_snapshot,
            uw.auto_rollback, true, uw.batch_size,
            uw.created_at, uw.updated_at
        FROM update_workflows uw
        ON CONFLICT (target_kind, target_id, action_key) DO NOTHING
        """
    )

    # Drop in dependency order: workflow_host_runs → workflow_runs → update_workflows.
    op.drop_table("workflow_host_runs")
    op.drop_table("workflow_runs")
    op.drop_table("update_workflows")

    # Drop the three Postgres enums no longer referenced by any column.
    op.execute("DROP TYPE IF EXISTS workflowhoststatus")
    op.execute("DROP TYPE IF EXISTS workflowrunstatus")
    op.execute("DROP TYPE IF EXISTS workflowstep")


def downgrade() -> None:
    """Recreate legacy tables and best-effort reverse-migrate rows."""
    # Recreate the three Postgres enums.
    op.execute(
        "CREATE TYPE workflowrunstatus AS ENUM "
        "('pending','running','completed','failed','partial')"
    )
    op.execute(
        "CREATE TYPE workflowhoststatus AS ENUM "
        "('pending','running','success','failed','skipped')"
    )
    op.execute(
        "CREATE TYPE workflowstep AS ENUM "
        "('preflight','snapshot','update','reboot','verify','cleanup','rollback')"
    )

    op.create_table(
        "update_workflows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("batch_size", sa.Integer(), server_default="1", nullable=False),
        sa.Column("schedule_cron", sa.String(length=100), nullable=True),
        sa.Column(
            "pre_update_snapshot", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("auto_rollback", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("verification_prompt", sa.Text(), nullable=True),
        sa.Column("auto_reboot", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "action_key",
            sa.String(length=64),
            server_default="linux-upgrade",
            nullable=False,
        ),
        sa.Column(
            "action_parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["host_groups.id"],
            ondelete="CASCADE",
            name=op.f("fk_update_workflows_group_id_host_groups"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_update_workflows")),
        sa.UniqueConstraint("group_id", name=op.f("uq_update_workflows_group_id")),
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "running",
                "completed",
                "failed",
                "partial",
                name="workflowrunstatus",
                create_type=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["update_workflows.id"],
            ondelete="CASCADE",
            name=op.f("fk_workflow_runs_workflow_id_update_workflows"),
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by"],
            ["users.id"],
            ondelete="SET NULL",
            name=op.f("fk_workflow_runs_triggered_by_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_runs")),
    )

    op.create_table(
        "workflow_host_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("host_id", sa.Integer(), nullable=False),
        sa.Column(
            "step",
            postgresql.ENUM(
                "preflight",
                "snapshot",
                "update",
                "reboot",
                "verify",
                "cleanup",
                "rollback",
                name="workflowstep",
                create_type=False,
            ),
            server_default="preflight",
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "running",
                "success",
                "failed",
                "skipped",
                name="workflowhoststatus",
                create_type=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("snapshot_name", sa.String(length=200), nullable=True),
        sa.Column(
            "step_output",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
            ondelete="CASCADE",
            name=op.f("fk_workflow_host_runs_run_id_workflow_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["hosts.id"],
            ondelete="CASCADE",
            name=op.f("fk_workflow_host_runs_host_id_hosts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_host_runs")),
    )

    # Reverse-migrate scheduled_actions rows whose shape matches the legacy
    # upgrade contract. Built-in pseudo-action rows are dropped on the
    # downgrade — they didn't exist in the legacy tables.
    op.execute(
        """
        INSERT INTO update_workflows (
            group_id, batch_size, schedule_cron, pre_update_snapshot,
            auto_rollback, auto_reboot, action_key, action_parameters,
            enabled, created_at, updated_at
        )
        SELECT
            sa.target_id, sa.batch_size, sa.schedule_cron, sa.snapshot_enabled,
            sa.auto_rollback, true, sa.action_key, sa.parameters,
            sa.enabled, sa.created_at, sa.updated_at
        FROM scheduled_actions sa
        WHERE sa.target_kind = 'group'
          AND sa.action_key NOT LIKE '\\_builtin.%' ESCAPE '\\'
        ON CONFLICT (group_id) DO NOTHING
        """
    )
