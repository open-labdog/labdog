"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Enum types ---
    firewallbackend = postgresql.ENUM(
        "nftables", "firewalld", "ufw", "unknown",
        name="firewallbackend",
    )
    firewallbackend.create(op.get_bind(), checkfirst=True)

    syncstatus = postgresql.ENUM(
        "pending", "in_sync", "out_of_sync", "unknown", "error",
        name="syncstatus",
    )
    syncstatus.create(op.get_bind(), checkfirst=True)

    ruleaction = postgresql.ENUM(
        "allow", "deny", "reject",
        name="ruleaction",
    )
    ruleaction.create(op.get_bind(), checkfirst=True)

    ruleprotocol = postgresql.ENUM(
        "tcp", "udp", "icmp", "any",
        name="ruleprotocol",
    )
    ruleprotocol.create(op.get_bind(), checkfirst=True)

    ruledirection = postgresql.ENUM(
        "input", "output",
        name="ruledirection",
    )
    ruledirection.create(op.get_bind(), checkfirst=True)

    jobstatus = postgresql.ENUM(
        "pending", "running", "success", "failed", "cancelled",
        name="jobstatus",
    )
    jobstatus.create(op.get_bind(), checkfirst=True)

    grouprole = postgresql.ENUM(
        "admin", "editor", "viewer",
        name="grouprole",
    )
    grouprole.create(op.get_bind(), checkfirst=True)

    # 1. users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    # 2. ssh_keys
    op.create_table(
        "ssh_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("encrypted_private_key", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ssh_keys")),
        sa.UniqueConstraint("name", name=op.f("uq_ssh_keys_name")),
    )

    # 3. host_groups
    op.create_table(
        "host_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_host_groups")),
        sa.UniqueConstraint("name", name=op.f("uq_host_groups_name")),
        sa.UniqueConstraint("priority", name=op.f("uq_host_groups_priority")),
    )
    op.create_index(op.f("ix_host_groups_name"), "host_groups", ["name"], unique=True)

    # 4. hosts (FK → ssh_keys)
    op.create_table(
        "hosts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("ip_address", sa.String(50), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default=sa.text("22")),
        sa.Column(
            "firewall_backend",
            postgresql.ENUM("nftables", "firewalld", "ufw", "unknown", name="firewallbackend", create_type=False),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("ssh_key_id", sa.Integer(), nullable=True),
        sa.Column(
            "sync_status",
            postgresql.ENUM("pending", "in_sync", "out_of_sync", "unknown", "error", name="syncstatus", create_type=False),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("drift_check_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_drift_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ssh_key_id"], ["ssh_keys.id"],
            name=op.f("fk_hosts_ssh_key_id_ssh_keys"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hosts")),
        sa.UniqueConstraint("hostname", name=op.f("uq_hosts_hostname")),
    )
    op.create_index(op.f("ix_hosts_hostname"), "hosts", ["hostname"], unique=True)

    # 5. host_group_memberships (FK → hosts, host_groups)
    op.create_table(
        "host_group_memberships",
        sa.Column("host_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["host_id"], ["hosts.id"],
            name=op.f("fk_host_group_memberships_host_id_hosts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["host_groups.id"],
            name=op.f("fk_host_group_memberships_group_id_host_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("host_id", "group_id", name=op.f("pk_host_group_memberships")),
    )

    # 6. user_group_permissions (FK → users, host_groups)
    op.create_table(
        "user_group_permissions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM("admin", "editor", "viewer", name="grouprole", create_type=False),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_user_group_permissions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["host_groups.id"],
            name=op.f("fk_user_group_permissions_group_id_host_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "group_id", name=op.f("pk_user_group_permissions")),
    )

    # 7. firewall_rules (FK → host_groups)
    op.create_table(
        "firewall_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column(
            "action",
            postgresql.ENUM("allow", "deny", "reject", name="ruleaction", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "protocol",
            postgresql.ENUM("tcp", "udp", "icmp", "any", name="ruleprotocol", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "direction",
            postgresql.ENUM("input", "output", name="ruledirection", create_type=False),
            nullable=False,
        ),
        sa.Column("source_cidr", sa.String(50), nullable=True),
        sa.Column("destination_cidr", sa.String(50), nullable=True),
        sa.Column("port_start", sa.Integer(), nullable=True),
        sa.Column("port_end", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"], ["host_groups.id"],
            name=op.f("fk_firewall_rules_group_id_host_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_firewall_rules")),
    )

    # 8. sync_jobs (FK → hosts, host_groups, users)
    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("host_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("pending", "running", "success", "failed", "cancelled", name="jobstatus", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ansible_output", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("triggered_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["host_id"], ["hosts.id"],
            name=op.f("fk_sync_jobs_host_id_hosts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["host_groups.id"],
            name=op.f("fk_sync_jobs_group_id_host_groups"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by_user_id"], ["users.id"],
            name=op.f("fk_sync_jobs_triggered_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_jobs")),
    )

    # 9. audit_log (FK → users)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_audit_log_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        comment="Append-only audit log. No updates or deletes.",
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("sync_jobs")
    op.drop_table("firewall_rules")
    op.drop_table("user_group_permissions")
    op.drop_table("host_group_memberships")
    op.drop_table("hosts")
    op.drop_table("host_groups")
    op.drop_table("ssh_keys")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS grouprole")
    op.execute("DROP TYPE IF EXISTS jobstatus")
    op.execute("DROP TYPE IF EXISTS ruledirection")
    op.execute("DROP TYPE IF EXISTS ruleprotocol")
    op.execute("DROP TYPE IF EXISTS ruleaction")
    op.execute("DROP TYPE IF EXISTS syncstatus")
    op.execute("DROP TYPE IF EXISTS firewallbackend")
