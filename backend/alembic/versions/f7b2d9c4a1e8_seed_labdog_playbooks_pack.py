"""Seed labdog-playbooks as a default override pack

Two changes:

1. Adds ``'none'`` to the ``gitauthtype`` postgres enum so public git
   repos can be configured without a placeholder credential.
2. Seeds one ``git_repositories`` row pointing at the canonical
   ``labdog-playbooks`` repository (auth_type=none) and one
   ``action_packs`` row referencing it (role=override). Operators that
   want to point at a fork or remove the default delete either row
   from the UI; the migration only inserts when the rows are missing,
   so a delete sticks across upgrades.

Revision ID: f7b2d9c4a1e8
Revises: d9e4b3a8c2f1
Create Date: 2026-04-26 14:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7b2d9c4a1e8"
down_revision: str | Sequence[str] | None = "d9e4b3a8c2f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PACK_REPO_URL = "https://github.com/open-labdog/labdog-playbooks"
PACK_REPO_NAME = "labdog-playbooks"
PACK_NAME = "labdog-playbooks"


def upgrade() -> None:
    # Postgres requires ALTER TYPE ... ADD VALUE outside of a
    # transaction. Alembic runs migrations inside one by default, so we
    # commit before the ALTER and let alembic open a fresh transaction
    # for the seed inserts that follow.
    op.execute("COMMIT")
    op.execute("ALTER TYPE gitauthtype ADD VALUE IF NOT EXISTS 'none'")

    # Seed git_repositories row (only if no row already targets the
    # same URL — operators who deleted it stay deleted).
    op.execute(
        f"""
        INSERT INTO git_repositories (
            name, url, branch, auth_type, ssh_key_id,
            encrypted_https_token, webhook_secret,
            last_commit_sha, last_sync_at, created_at, updated_at
        )
        SELECT
            '{PACK_REPO_NAME}', '{PACK_REPO_URL}', 'main', 'none', NULL,
            NULL, NULL,
            NULL, NULL, NOW(), NOW()
        WHERE NOT EXISTS (
            SELECT 1 FROM git_repositories WHERE url = '{PACK_REPO_URL}'
        )
        """
    )

    # Seed action_packs row referencing the just-inserted repository.
    # If the repo row was already present (e.g. operator added it
    # manually before this migration) we still link the pack to it.
    op.execute(
        f"""
        INSERT INTO action_packs (
            name, source_type, git_repository_id, path, local_path,
            role, enabled,
            last_synced_at, last_sync_status, last_sync_error,
            current_sha, created_at, updated_at
        )
        SELECT
            '{PACK_NAME}', 'git',
            (SELECT id FROM git_repositories WHERE url = '{PACK_REPO_URL}' LIMIT 1),
            '', NULL, 'override', TRUE,
            NULL, NULL, NULL,
            NULL, NOW(), NOW()
        WHERE EXISTS (
            SELECT 1 FROM git_repositories WHERE url = '{PACK_REPO_URL}'
        )
        AND NOT EXISTS (
            SELECT 1 FROM action_packs WHERE name = '{PACK_NAME}'
        )
        """
    )


def downgrade() -> None:
    # Best-effort removal of the seeded rows. The enum value stays —
    # postgres has no DROP VALUE for enums and removing it would
    # require recreating the type. Harmless to leave behind.
    op.execute(
        f"DELETE FROM action_packs WHERE name = '{PACK_NAME}'"
    )
    op.execute(
        f"DELETE FROM git_repositories WHERE url = '{PACK_REPO_URL}'"
    )
