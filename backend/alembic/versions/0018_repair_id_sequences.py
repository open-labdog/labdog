"""Repair id sequences left behind by the seed data in 0001.

``0001_initial_schema`` seeds rows with explicit ids — nine into
``app_settings``, one each into ``git_repositories`` and ``action_packs``
— which does not advance the owning sequence. It has carried
compensating ``setval`` calls since a371fe0 (2026-05-19), so installs
created after that date are fine.

Editing a migration does nothing for a database that already ran it,
though. Any instance installed before that date still has its sequences
at 1, and the first insert of a *new* row collides with a seeded id:

    duplicate key value violates unique constraint "pk_app_settings"

``app_settings`` is where this bites, because it is the only table
seeded with more than one row. Its sequence hands out 2 while ids 2..9
are taken, so saving a setting that has no row yet fails — eight times
over, until the counter finally climbs past 9. Settings that *do* have a
seeded row update in place and never reveal the problem, which is why
this stayed hidden until the Settings page began exposing keys outside
the seeded nine.

The repair is written against every sequence-backed table rather than
the three seeded ones. A sequence behind ``max(id)`` is a bug wherever it
occurs, this catches any case seeded later by the same pattern, and
``GREATEST`` means a healthy sequence is left exactly as it is. Re-running
changes nothing.
"""

from __future__ import annotations

from alembic import op

revision = "0018_repair_id_sequences"
down_revision = "0017_currency_neutral_costs"
branch_labels = None
depends_on = None


# Only ever advances: `GREATEST(current, max(id))` cannot rewind a
# sequence that is already ahead, so a table whose rows have since been
# deleted does not get its counter pulled back into reusing live ids.
#
# `setval(..., n)` marks the sequence as called, so the next value is
# n + 1. Seeding max(id) is therefore correct, not off by one.
REPAIR = """
DO $$
DECLARE
    rec RECORD;
    seq TEXT;
    max_id BIGINT;
    cur BIGINT;
BEGIN
    FOR rec IN
        -- The join on pg_attribute restricts this to tables that actually
        -- have an `id` column. pg_get_serial_sequence raises rather than
        -- returning NULL when asked about a column that does not exist, so
        -- an unfiltered loop dies on the first such table — alembic_version
        -- being the one every database has.
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
        -- pg_get_serial_sequence already returns a quoted, qualified name,
        -- so the sequence can be read directly rather than matched by name
        -- against pg_sequences.
        EXECUTE format('SELECT last_value FROM %s', seq) INTO cur;

        IF max_id > COALESCE(cur, 0) THEN
            PERFORM setval(seq, max_id);
            RAISE NOTICE 'Advanced % from % to %', seq, cur, max_id;
        END IF;
    END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(REPAIR)


def downgrade() -> None:
    # Nothing to undo. Rewinding a sequence would hand out ids that are
    # already in use, which is the very failure this migration exists to
    # clear.
    pass
