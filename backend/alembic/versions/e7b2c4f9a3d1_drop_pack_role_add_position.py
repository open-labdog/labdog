"""drop ActionPack.role; add position; create action_resolution + snapshot tables

Revision ID: e7b2c4f9a3d1
Revises: d9a3e7b6c2f1
Create Date: 2026-05-06 10:00:00.000000

Replaces the operator-chosen ``role`` (default / override) with a
single linear ``position`` integer ordering across all packs. Higher
position wins on action-key collisions; the bundled pack is implicit
at position 0 (no DB row).

Adds two new bookkeeping tables for the freeze-on-fresh-conflict
behaviour:

- ``action_resolution`` — explicit "this pack wins for this key"
  decisions. Fed from two sources:
    1. Wizard onboarding (operator picks per-key when adding a pack
       that conflicts with existing packs).
    2. Auto-created at registry rebuild when a sync introduces a
       previously-uncontested key into a new pack — pinned to the
       previous winner so behaviour doesn't silently flip.
  ``pack_id NULL`` means "use bundled."
  ``ON DELETE CASCADE`` on pack — a deleted pack drops its
  resolution rows; position-based default takes over.

- ``action_registry_snapshot`` — last-known winner per key.
  Bookkeeping only; the rebuild logic compares against this to
  detect "previously uncontested key just became contested."

Backfill: existing local packs go to the top of the position
ordering, then ``role=override`` packs, then ``role=default`` packs.
Behaviour preserved for current installs. **Local-implicit-top is
gone** — the operator can demote a local pack below other packs
post-migration. No resolution rows are backfilled; position-based
defaults handle the existing winners exactly as today.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7b2c4f9a3d1"
down_revision: str | Sequence[str] | None = "d9a3e7b6c2f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- ActionPack: position replaces role -------------------------------
    op.add_column(
        "action_packs",
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )

    # Backfill: stable ordering preserving today's effective priority.
    op.execute(
        """
        WITH ordered AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    ORDER BY
                        CASE
                            WHEN source_type = 'local' THEN 3
                            WHEN role = 'override' THEN 2
                            WHEN role = 'default' THEN 1
                            ELSE 0
                        END,
                        id
                ) AS pos
            FROM action_packs
        )
        UPDATE action_packs
        SET position = ordered.pos
        FROM ordered
        WHERE action_packs.id = ordered.id
        """
    )

    op.create_index(
        "ix_action_packs_position",
        "action_packs",
        ["position"],
    )

    op.drop_column("action_packs", "role")
    op.execute("DROP TYPE IF EXISTS packrole")

    # ---- action_resolution -------------------------------------------------
    op.create_table(
        "action_resolution",
        sa.Column("action_key", sa.String(length=64), nullable=False),
        sa.Column("pack_id", sa.Integer(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["pack_id"],
            ["action_packs.id"],
            ondelete="CASCADE",
            name=op.f("fk_action_resolution_pack_id_action_packs"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name=op.f("fk_action_resolution_decided_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("action_key", name=op.f("pk_action_resolution")),
    )

    # ---- action_registry_snapshot -----------------------------------------
    op.create_table(
        "action_registry_snapshot",
        sa.Column("action_key", sa.String(length=64), nullable=False),
        sa.Column("pack_id", sa.Integer(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["pack_id"],
            ["action_packs.id"],
            ondelete="CASCADE",
            name=op.f("fk_action_registry_snapshot_pack_id_action_packs"),
        ),
        sa.PrimaryKeyConstraint(
            "action_key", name=op.f("pk_action_registry_snapshot")
        ),
    )


def downgrade() -> None:
    op.drop_table("action_registry_snapshot")
    op.drop_table("action_resolution")

    # Recreate role enum.
    op.execute("CREATE TYPE packrole AS ENUM ('default', 'override')")
    op.add_column(
        "action_packs",
        sa.Column(
            "role",
            sa.Enum("default", "override", name="packrole", create_type=False),
            server_default="override",
            nullable=False,
        ),
    )
    # Best-effort reverse: lower-positioned packs become 'default',
    # higher-positioned become 'override'. Operators with a
    # finely-ordered list will find this lossy.
    op.execute(
        """
        UPDATE action_packs
        SET role = CASE
            WHEN position <= (SELECT COUNT(*) FROM action_packs) / 2 THEN 'default'
            ELSE 'override'
        END
        """
    )

    op.drop_index("ix_action_packs_position", table_name="action_packs")
    op.drop_column("action_packs", "position")
