"""action_packs: link to git_repositories, drop per-pack auth

Consolidates git repository configuration (URL, branch, credentials)
onto the existing GitRepository table. An ActionPack of source_type
``git`` now references a GitRepository row by id and names a subpath
within it; local packs keep their filesystem path under a dedicated
``local_path`` column.

Pre-release, destructive: the action_packs table is truncated before
the column swap. Admins reconfigure packs after applying.

Revision ID: d9e4b3a8c2f1
Revises: c6a1f8e2b9d4
Create Date: 2026-04-24 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9e4b3a8c2f1"
down_revision: str | Sequence[str] | None = "c6a1f8e2b9d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Wipe existing rows — none of the existing columns map cleanly to
    # the new shape (see "destructive" note in the docstring).
    op.execute("TRUNCATE action_packs RESTART IDENTITY CASCADE")

    # Drop check constraints that depend on columns we're about to
    # remove.
    op.drop_constraint(
        "ck_action_packs_auth_consistency",
        "action_packs",
        type_="check",
    )
    op.drop_constraint(
        "ck_action_packs_local_requires_no_auth",
        "action_packs",
        type_="check",
    )

    # New columns.
    op.add_column(
        "action_packs",
        sa.Column(
            "git_repository_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        op.f("fk_action_packs_git_repository_id_git_repositories"),
        "action_packs",
        "git_repositories",
        ["git_repository_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "action_packs",
        sa.Column(
            "path",
            sa.String(length=500),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "action_packs",
        sa.Column("local_path", sa.String(length=500), nullable=True),
    )

    # XOR constraint: a git pack references a repo and has no
    # local_path; a local pack sets local_path and no repo.
    op.create_check_constraint(
        "ck_action_packs_source_shape",
        "action_packs",
        "(source_type = 'git' AND git_repository_id IS NOT NULL AND local_path IS NULL)"
        " OR (source_type = 'local' AND git_repository_id IS NULL AND local_path IS NOT NULL)",
    )

    # Drop old per-pack columns. Order: credentials first, then the
    # URL-ish fields they were paired with.
    op.drop_column("action_packs", "encrypted_ssh_key")
    op.drop_column("action_packs", "ssh_known_hosts")
    op.drop_column("action_packs", "encrypted_token")
    op.drop_column("action_packs", "auth_type")
    op.drop_column("action_packs", "ref")
    op.drop_column("action_packs", "repo_url")

    # Drop enum types that no other table uses.
    sa.Enum(name="packauthtype").drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Re-add old columns with permissive defaults so the table shape
    # matches again; existing rows (if any) are re-truncated so we
    # don't need to invent stale data.
    op.execute("TRUNCATE action_packs RESTART IDENTITY CASCADE")

    auth_type_enum = sa.Enum(
        "none",
        "ssh",
        "https_token",
        name="packauthtype",
    )
    auth_type_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "action_packs",
        sa.Column(
            "repo_url",
            sa.String(length=500),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "action_packs",
        sa.Column(
            "ref",
            sa.String(length=200),
            nullable=False,
            server_default="main",
        ),
    )
    op.add_column(
        "action_packs",
        sa.Column(
            "auth_type",
            auth_type_enum,
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "action_packs",
        sa.Column("encrypted_token", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "action_packs",
        sa.Column("ssh_known_hosts", sa.Text(), nullable=True),
    )
    op.add_column(
        "action_packs",
        sa.Column("encrypted_ssh_key", sa.LargeBinary(), nullable=True),
    )

    op.drop_constraint(
        "ck_action_packs_source_shape",
        "action_packs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_action_packs_local_requires_no_auth",
        "action_packs",
        "source_type = 'git' OR auth_type = 'none'",
    )
    op.create_check_constraint(
        "ck_action_packs_auth_consistency",
        "action_packs",
        "(auth_type = 'none' AND encrypted_ssh_key IS NULL AND encrypted_token IS NULL)"
        " OR (auth_type = 'ssh' AND encrypted_ssh_key IS NOT NULL AND encrypted_token IS NULL)"
        " OR (auth_type = 'https_token' AND encrypted_token IS NOT NULL"
        " AND encrypted_ssh_key IS NULL)",
    )

    op.drop_constraint(
        op.f("fk_action_packs_git_repository_id_git_repositories"),
        "action_packs",
        type_="foreignkey",
    )
    op.drop_column("action_packs", "local_path")
    op.drop_column("action_packs", "path")
    op.drop_column("action_packs", "git_repository_id")
