"""Make ``action_host_runs.output`` default to an empty string.

The model declared ``server_default="''"``. SQLAlchemy quotes a string
server default itself, so the column's default became the two characters
``''`` rather than an empty string; ``0001`` carries the same default.
A host run that finishes without writing output, such as a state
collection with nothing to report, keeps that default, and the host page
shows it as a warning after every "collect all" (BUG-90).

This sets the default to ``''`` and empties the rows already written
with it. A real transcript is never exactly two quote characters, so the
rewrite cannot touch genuine output.

The downgrade restores the old default but leaves the rows alone:
putting the quotes back would only restore the bug.

Revision ID: 0040_host_run_output_default
Revises: 0039_repair_uncalled_sequences
"""

from __future__ import annotations

from alembic import op

revision = "0040_host_run_output_default"
down_revision = "0039_repair_uncalled_sequences"
branch_labels = None
depends_on = None


SET_DEFAULT = "ALTER TABLE action_host_runs ALTER COLUMN output SET DEFAULT ''"

# chr(39) is a single quote; spelled this way so the literal is unambiguous.
REWRITE = "UPDATE action_host_runs SET output = '' WHERE output = repeat(chr(39), 2)"


def upgrade() -> None:
    op.execute(SET_DEFAULT)
    op.execute(REWRITE)


def downgrade() -> None:
    op.execute("ALTER TABLE action_host_runs ALTER COLUMN output SET DEFAULT ''''''")
