"""Local accounts: a role per user, display name, last login, forced change.

Revision ID: 0058_user_roles
Revises: 0057_seeded_pack_url_unbork

Additive only: every existing column and row survives, so the revision is
safe on any release line that carries 0057.

Backfill keeps what everyone could do before. Superusers become admins.
Every other account becomes an operator, because before roles existed
every signed-in user could do everything except manage users, which is
exactly the operator role; administrators narrow it from the Users page.
"""

import sqlalchemy as sa

from alembic import op

revision = "0058_user_roles"
down_revision = "0057_seeded_pack_url_unbork"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE users SET role = CASE WHEN is_superuser THEN 'admin' ELSE 'operator' END")
    op.create_check_constraint(
        "ck_users_role", "users", "role IS NULL OR role IN ('admin', 'operator', 'viewer')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "display_name")
    op.drop_column("users", "role")
