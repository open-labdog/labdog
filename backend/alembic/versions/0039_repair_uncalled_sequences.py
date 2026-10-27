"""Repair the id sequences 0018 skipped: never called, at a single seeded row.

``0018_repair_id_sequences`` advances a sequence when ``max(id)`` exceeds
its ``last_value``. That misses the state a fresh serial sequence is in:
``last_value = 1, is_called = false``, where ``nextval`` hands out 1
itself. ``0001`` seeds exactly one row at id 1 into ``git_repositories``
and ``action_packs``, so on an install from before a371fe0 (2026-05-19)
both tables compared ``1 > 1``, were skipped, and their first new row
collided with the seed:

    duplicate key value violates unique constraint "pk_git_repositories"

The failed insert consumes id 1, so the retry succeeds, which is why this
cost one opaque 500 per table rather than a broken feature (BUG-84).
``app_settings`` was repaired by 0018 only because it is seeded with nine
rows.

This compares ``max(id)`` with the value ``nextval`` will actually return
next: ``last_value`` while the sequence has never been called, and
``last_value + 1`` once it has (serial sequences step by 1). Like 0018 it
covers every sequence-backed table, only ever advances, and re-running it
changes nothing.

0018 is left as it is: every affected database has already run it.

Revision ID: 0039_repair_uncalled_sequences
Revises: 0038_drop_is_system_columns
"""

from __future__ import annotations

from alembic import op

revision = "0039_repair_uncalled_sequences"
down_revision = "0038_drop_is_system_columns"
branch_labels = None
depends_on = None


# `setval(seq, max_id)` marks the sequence as called, so nextval then
# returns max_id + 1. It only runs when the value nextval would return is
# already taken, so it can only move a sequence forward.
REPAIR = """
DO $$
DECLARE
    rec RECORD;
    seq TEXT;
    max_id BIGINT;
    last_val BIGINT;
    called BOOLEAN;
    next_val BIGINT;
BEGIN
    FOR rec IN
        -- Only tables with an `id` column: pg_get_serial_sequence raises on
        -- a column that does not exist (see 0018).
        SELECT c.relname AS table_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a
          ON a.attrelid = c.oid
         AND a.attname = 'id'
         AND a.attnum > 0
         AND NOT a.attisdropped
        WHERE c.relkind = 'r' AND n.nspname = 'public'
    LOOP
        seq := pg_get_serial_sequence('public.' || quote_ident(rec.table_name), 'id');
        -- NULL means the column exists but is not sequence-backed.
        CONTINUE WHEN seq IS NULL;

        EXECUTE format('SELECT COALESCE(MAX(id), 0) FROM public.%I', rec.table_name)
            INTO max_id;
        EXECUTE format('SELECT last_value, is_called FROM %s', seq) INTO last_val, called;
        next_val := CASE WHEN called THEN last_val + 1 ELSE last_val END;

        IF max_id >= next_val THEN
            PERFORM setval(seq, max_id);
            RAISE NOTICE 'Advanced % from next value % to %', seq, next_val, max_id + 1;
        END IF;
    END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(REPAIR)


def downgrade() -> None:
    # Nothing to undo. Rewinding a sequence would hand out ids that are
    # already in use, which is the failure this migration exists to clear.
    pass
