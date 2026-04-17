"""add host refs to firewall rules and hosts entries

Revision ID: c5d8e2f49a1b
Revises: f1a2b3c4d5e6
Create Date: 2026-04-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5d8e2f49a1b"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # firewall_rules: add source/destination host FK columns
    op.add_column("firewall_rules", sa.Column("source_host_id", sa.Integer(), nullable=True))
    op.add_column("firewall_rules", sa.Column("destination_host_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_firewall_rules_source_host_id",
        "firewall_rules",
        "hosts",
        ["source_host_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_firewall_rules_destination_host_id",
        "firewall_rules",
        "hosts",
        ["destination_host_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_firewall_rules_source_host_id", "firewall_rules", ["source_host_id"])
    op.create_index(
        "ix_firewall_rules_destination_host_id", "firewall_rules", ["destination_host_id"]
    )
    op.alter_column("firewall_rules", "source_cidr", existing_type=sa.String(50), nullable=True)
    op.alter_column(
        "firewall_rules", "destination_cidr", existing_type=sa.String(50), nullable=True
    )
    op.create_check_constraint(
        "ck_firewall_rules_source_ref",
        "firewall_rules",
        "NOT (source_cidr IS NOT NULL AND source_host_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_firewall_rules_destination_ref",
        "firewall_rules",
        "NOT (destination_cidr IS NOT NULL AND destination_host_id IS NOT NULL)",
    )

    # hosts_entries: add host_ref_id and relax ip_address/hostname
    op.add_column("hosts_entries", sa.Column("host_ref_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_hosts_entries_host_ref_id",
        "hosts_entries",
        "hosts",
        ["host_ref_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_hosts_entries_host_ref_id", "hosts_entries", ["host_ref_id"])
    op.alter_column("hosts_entries", "ip_address", existing_type=sa.String(45), nullable=True)
    op.alter_column("hosts_entries", "hostname", existing_type=sa.String(253), nullable=True)
    op.create_check_constraint(
        "ck_hosts_entries_ref_or_literal",
        "hosts_entries",
        "host_ref_id IS NOT NULL OR (ip_address IS NOT NULL AND hostname IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_hosts_entries_ref_or_literal", "hosts_entries", type_="check")
    op.alter_column("hosts_entries", "hostname", existing_type=sa.String(253), nullable=False)
    op.alter_column("hosts_entries", "ip_address", existing_type=sa.String(45), nullable=False)
    op.drop_index("ix_hosts_entries_host_ref_id", "hosts_entries")
    op.drop_constraint("fk_hosts_entries_host_ref_id", "hosts_entries", type_="foreignkey")
    op.drop_column("hosts_entries", "host_ref_id")

    op.drop_constraint("ck_firewall_rules_destination_ref", "firewall_rules", type_="check")
    op.drop_constraint("ck_firewall_rules_source_ref", "firewall_rules", type_="check")
    op.alter_column(
        "firewall_rules", "destination_cidr", existing_type=sa.String(50), nullable=False
    )
    op.alter_column("firewall_rules", "source_cidr", existing_type=sa.String(50), nullable=False)
    op.drop_index("ix_firewall_rules_destination_host_id", "firewall_rules")
    op.drop_index("ix_firewall_rules_source_host_id", "firewall_rules")
    op.drop_constraint(
        "fk_firewall_rules_destination_host_id", "firewall_rules", type_="foreignkey"
    )
    op.drop_constraint("fk_firewall_rules_source_host_id", "firewall_rules", type_="foreignkey")
    op.drop_column("firewall_rules", "destination_host_id")
    op.drop_column("firewall_rules", "source_host_id")
