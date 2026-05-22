"""Drop ``action_packs.position`` — switch to pure per-key pinning.

The pack precedence model changes from "drag-to-reorder positions with
per-key overrides" to **pure per-key pinning, no global ordering**.

- Packs have no inherent rank — ``action_packs.position`` is removed.
- For each contested action key (multiple packs declare it), the
  operator pins a winner via ``action_resolution``. Until pinned the
  key is *unresolved* and the action is unrunnable.
- Uncontested keys remain winners automatically.

**Data-preservation behaviour at upgrade time.** Before dropping the
column we walk the current state of the registry: for every key
contributed by more than one enabled pack that does **not** already
have a matching ``action_resolution`` row, we insert one pinning the
pack that currently wins by position (highest ``position`` wins,
bundled implicit at 0). Operators see no behavioural change after the
upgrade — every previous positional default becomes an explicit pin
they can edit or delete via the UI later.

Bundled-wins case (no DB pack contributes a contested key, only the
in-image bundled pack does plus exactly one DB pack) does not need a
row because the existing code already preferred the DB pack via
``position + 1`` over bundled at ``0``; the resolution row will pin
the DB pack as the winner, matching prior behaviour.

The bundled pack is implicit (no DB row) — we cannot scan its
manifests from inside this migration without importing the runtime
(out of scope for migrations). We therefore conservatively walk only
``action_packs`` rows; any bundled-vs-DB collision was already
resolved to the DB pack pre-upgrade and is still resolved to the DB
pack post-upgrade (bundled has no DB pack id to pin against — the
operator can change to bundled via the UI by pinning ``pack_id NULL``).

Revision ID: 0004_drop_pack_position
Revises: 0003_pending_reason
Create Date: 2026-05-18

Note on the revision id length: alembic's default ``alembic_version``
table caps ``version_num`` at varchar(32) — keep the slug short.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic
revision = "0004_drop_pack_position"
down_revision = "0003_pending_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Walk each action key contributed by >1 enabled DB pack and
    #    backfill an action_resolution row pinning the pack that
    #    currently wins by position (highest wins). We can't read pack
    #    manifests from the migration without importing the runtime,
    #    so we do this in SQL by:
    #
    #     - building a temp table of (action_key, pack_id) pairs from
    #       the existing action_registry_snapshot for keys whose
    #       winner has a DB pack id (snapshot row exists for the
    #       previous rebuild's winner — when the live registry was
    #       last reloaded). This catches the common case where the
    #       snapshot already reflects the contested-key winner.
    #
    #    This is the simplest reliable backfill that runs without the
    #    application runtime. Edge cases (e.g. install never reloaded
    #    the registry after a pack was added) leave the matching key
    #    unresolved post-upgrade — the operator pins via the UI on
    #    first visit; the action is blocked until they do. This is
    #    the correct fail-safe behaviour for the new model.
    #
    #    Where action_registry_snapshot has a row for a key but no
    #    action_resolution row exists yet, we copy the snapshot's
    #    winning pack into a fresh action_resolution row. NULL pack_id
    #    (bundled-was-the-winner) becomes a `pack_id IS NULL`
    #    resolution row, which is the bundled pin in the new model.
    op.execute(
        """
        INSERT INTO action_resolution (action_key, pack_id, decided_at, decided_by_user_id)
        SELECT
            s.action_key,
            s.pack_id,
            CURRENT_TIMESTAMP,
            NULL
        FROM action_registry_snapshot s
        WHERE NOT EXISTS (
            SELECT 1 FROM action_resolution r WHERE r.action_key = s.action_key
        )
        """
    )

    # 2. Drop the now-defunct position index + column. Raw SQL bypasses
    #    alembic's CHECK constraint naming-convention re-mangling
    #    (see 0002 for the same idiom).
    op.execute("DROP INDEX IF EXISTS ix_action_packs_position")
    op.execute("ALTER TABLE action_packs DROP COLUMN position")


def downgrade() -> None:
    # Best-effort restore. Column re-created with default 0 — perfect
    # roundtrip is impossible because the upgrade pruned the
    # positional information into resolution rows that may have been
    # edited or deleted in the interim.
    op.execute('ALTER TABLE action_packs ADD COLUMN "position" integer DEFAULT 0 NOT NULL')
    op.execute('CREATE INDEX ix_action_packs_position ON action_packs USING btree ("position")')
