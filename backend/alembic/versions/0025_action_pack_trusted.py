"""Mark which packs may ship code that runs on the LabDog host.

An action pack is a git repository someone points LabDog at, and
ansible-core gives a repository several ways to execute code on the
*controller* rather than on a managed host: plugin directories it imports
Python from, and plays that target ``localhost`` or set
``connection: local``. Nothing inspected any of that, so registering a
pack was equivalent to handing over code execution as the labdog user.

``trusted`` gates that content. It is **not** a privilege boundary — under
LabDog's flat model any authenticated user can set it — it makes loading
controller-side code a deliberate, audited act rather than a side effect
of adding a repository.

Existing packs are backfilled ``true``. A migration cannot audit pack
content, and silently refusing to load a pack that has been working is a
worse failure than preserving the status quo behind a flag the operator
can now see and revoke. New packs default ``false``, so the safer value is
the one that applies to everything added from here on.

Revision ID: 0025_action_pack_trusted
Revises: 0024_ai_session_stopped_reason
"""

import sqlalchemy as sa

from alembic import op

revision = "0025_action_pack_trusted"
down_revision = "0024_ai_session_stopped_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default false so rows inserted outside the ORM are safe too;
    # the backfill below is what keeps existing packs working.
    op.add_column(
        "action_packs",
        sa.Column(
            "trusted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute("UPDATE action_packs SET trusted = true")


def downgrade() -> None:
    op.drop_column("action_packs", "trusted")
