"""Drop the per-provider cloud-egress flag in favour of the global setting.

Cloud egress was gated twice: ``ai_providers.allow_cloud_egress`` on the
row, and the ``ai.allow_cloud_providers`` app setting globally. Both had
to be on, and both defaulted to off.

In practice the same operator set both, in the same sitting, and the
second gate only ever announced itself as a refusal at session time —
long after the checkbox had been ticked and the provider saved. The
granularity it bought was thin, too: once any provider is permitted to
egress, host data is leaving the network, so blocking a second one does
not protect anything the first has not already exposed.

The global setting is the one worth keeping. It cannot be reconstructed
from per-provider flags, and it still covers providers added later — a
new provider cannot egress until someone makes a deliberate,
instance-wide policy decision.

The AI feature has never been released (0014 exists only on dev and
feature branches, never on main), so the column can go rather than
linger deprecated.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0019_drop_provider_egress_flag"
down_revision = "0018_repair_id_sequences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("ai_providers", "allow_cloud_egress")


def downgrade() -> None:
    # Restored as false, matching 0014's server_default. The prior
    # per-row values are not recoverable, and false is the safe direction:
    # it denies egress rather than silently re-granting it to a provider
    # that may never have been approved for it.
    op.add_column(
        "ai_providers",
        sa.Column(
            "allow_cloud_egress",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
