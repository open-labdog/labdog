"""gitops schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    gitauthtype = postgresql.ENUM("ssh_key", "https_token", name="gitauthtype")
    gitauthtype.create(op.get_bind(), checkfirst=True)

    gitopsstatus = postgresql.ENUM(
        "disconnected", "synced", "error", "importing",
        name="gitopsstatus",
    )
    gitopsstatus.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "git_repositories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("branch", sa.String(100), nullable=False, server_default="main"),
        sa.Column(
            "auth_type",
            postgresql.ENUM("ssh_key", "https_token", name="gitauthtype", create_type=False),
            nullable=False,
        ),
        sa.Column("ssh_key_id", sa.Integer(), nullable=True),
        sa.Column("encrypted_https_token", sa.LargeBinary(), nullable=True),
        sa.Column("webhook_secret", sa.String(200), nullable=True),
        sa.Column("last_commit_sha", sa.String(64), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["ssh_key_id"], ["ssh_keys.id"],
            name=op.f("fk_git_repositories_ssh_key_id_ssh_keys"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_git_repositories")),
        sa.UniqueConstraint("name", name=op.f("uq_git_repositories_name")),
    )

    op.add_column("host_groups", sa.Column("git_repository_id", sa.Integer(), nullable=True))
    op.add_column("host_groups", sa.Column("gitops_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("host_groups", sa.Column("gitops_file_path", sa.String(500), nullable=True))
    op.add_column(
        "host_groups",
        sa.Column(
            "gitops_status",
            postgresql.ENUM("disconnected", "synced", "error", "importing", name="gitopsstatus", create_type=False),
            nullable=False,
            server_default="disconnected",
        ),
    )
    op.add_column("host_groups", sa.Column("gitops_error_message", sa.Text(), nullable=True))
    op.add_column("host_groups", sa.Column("gitops_last_import_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_host_groups_git_repository_id_git_repositories"),
        "host_groups",
        "git_repositories",
        ["git_repository_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_host_groups_git_repository_id_git_repositories"),
        "host_groups",
        type_="foreignkey",
    )
    op.drop_column("host_groups", "gitops_last_import_at")
    op.drop_column("host_groups", "gitops_error_message")
    op.drop_column("host_groups", "gitops_status")
    op.drop_column("host_groups", "gitops_file_path")
    op.drop_column("host_groups", "gitops_enabled")
    op.drop_column("host_groups", "git_repository_id")

    op.drop_table("git_repositories")

    postgresql.ENUM(name="gitopsstatus").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="gitauthtype").drop(op.get_bind(), checkfirst=True)
