"""simplify firewall backends: remove firewalld and ufw

Revision ID: 0009
Revises: 6738a0f7215e
Create Date: 2026-03-28

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "6738a0f7215e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Migrate rows first while the old enum is still in place
    op.execute("UPDATE hosts SET firewall_backend = 'nftables' WHERE firewall_backend = 'firewalld'")
    op.execute("UPDATE hosts SET firewall_backend = 'unknown' WHERE firewall_backend = 'ufw'")

    # Recreate enum: rename old, create new, swap column type, drop old
    op.execute("ALTER TABLE hosts ALTER COLUMN firewall_backend DROP DEFAULT")
    op.execute("ALTER TYPE firewallbackend RENAME TO firewallbackend_old")
    op.execute("CREATE TYPE firewallbackend AS ENUM ('nftables', 'iptables', 'unknown')")
    op.execute(
        "ALTER TABLE hosts "
        "ALTER COLUMN firewall_backend TYPE firewallbackend "
        "USING firewall_backend::text::firewallbackend"
    )
    op.execute("ALTER TABLE hosts ALTER COLUMN firewall_backend SET DEFAULT 'unknown'::firewallbackend")
    op.execute("DROP TYPE firewallbackend_old")


def downgrade() -> None:
    op.execute("UPDATE hosts SET firewall_backend = 'unknown' WHERE firewall_backend = 'iptables'")

    op.execute("ALTER TABLE hosts ALTER COLUMN firewall_backend DROP DEFAULT")
    op.execute("ALTER TYPE firewallbackend RENAME TO firewallbackend_old")
    op.execute("CREATE TYPE firewallbackend AS ENUM ('nftables', 'firewalld', 'ufw', 'unknown')")
    op.execute(
        "ALTER TABLE hosts "
        "ALTER COLUMN firewall_backend TYPE firewallbackend "
        "USING firewall_backend::text::firewallbackend"
    )
    op.execute("ALTER TABLE hosts ALTER COLUMN firewall_backend SET DEFAULT 'unknown'::firewallbackend")
    op.execute("DROP TYPE firewallbackend_old")
