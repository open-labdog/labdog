"""add package tables

Revision ID: 263ff4c0e96c
Revises: 0007
Create Date: 2026-03-18 22:31:58.020103

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "263ff4c0e96c"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

packagestate = postgresql.ENUM(
    "present", "absent", "latest", name="packagestate", create_type=False
)
packagemanager = postgresql.ENUM(
    "apt", "dnf", "yum", "auto", name="packagemanager", create_type=False
)
repotype = postgresql.ENUM("apt", "yum", name="repotype", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    packagestate.create(op.get_bind(), checkfirst=True)
    packagemanager.create(op.get_bind(), checkfirst=True)
    repotype.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "package_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("host_id", sa.Integer(), nullable=True),
        sa.Column("package_name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=True),
        sa.Column("state", packagestate, nullable=False),
        sa.Column("package_manager", packagemanager, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL)"
            " OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_package_rules_scope",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["host_groups.id"],
            name=op.f("fk_package_rules_group_id_host_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["hosts.id"],
            name=op.f("fk_package_rules_host_id_hosts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package_rules")),
        sa.UniqueConstraint("group_id", "package_name", name="uq_package_rules_group_pkg"),
        sa.UniqueConstraint("host_id", "package_name", name="uq_package_rules_host_pkg"),
    )

    op.create_table(
        "package_repositories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("key_url", sa.String(length=500), nullable=True),
        sa.Column("repo_type", repotype, nullable=False),
        sa.Column("distribution", sa.String(length=100), nullable=True),
        sa.Column("components", sa.String(length=200), nullable=True),
        sa.Column("state", packagestate, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["host_groups.id"],
            name=op.f("fk_package_repositories_group_id_host_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package_repositories")),
        sa.UniqueConstraint("group_id", "name", name="uq_package_repos_group_name"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("package_repositories")
    op.drop_table("package_rules")
    packagestate.drop(op.get_bind(), checkfirst=True)
    packagemanager.drop(op.get_bind(), checkfirst=True)
    repotype.drop(op.get_bind(), checkfirst=True)
