"""Drop host_group_memberships.role and its CHECK constraint.

The per-membership ``role`` column existed only to feed cluster-mode
actions (``execution_mode: cluster``, currently just ``k8s-upgrade``).
Cluster-mode is being removed before v0.2.0 ships — every action will
dispatch through the per-host path with a flat ``all`` inventory and
no bespoke labdog support for inventory shape. The k8s-upgrade pack
is being re-implemented to self-discover topology in the playbook.

Revision ID: 0002_drop_membership_role
Revises: 0001_initial_schema
Create Date: 2026-05-14

Note on the revision id length: alembic's default ``alembic_version``
table caps ``version_num`` at varchar(32). Keep this slug short — the
file name can be longer, but the ``revision = "..."`` value cannot.
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic
revision = "0002_drop_membership_role"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


# Constraint name as it lives in the prod database. Alembic's CHECK
# naming convention (``ck_%(table_name)s_%(constraint_name)s``) was
# already applied once when the model was emitted in 0001 — the
# explicit ``name="ck_host_group_memberships_role_valid"`` got the
# table prefix added automatically. We use raw SQL here to bypass a
# *second* application of the convention by ``op.drop_constraint`` /
# ``op.create_check_constraint``, which would mangle the name into
# something like ``ck_host_group_memberships_ck_host_group_memberships_ck__<hash>``
# and miss the real constraint.
CHECK_CONSTRAINT_NAME = "ck_host_group_memberships_ck_host_group_memberships_role_valid"


def upgrade() -> None:
    op.execute(
        f'ALTER TABLE host_group_memberships DROP CONSTRAINT "{CHECK_CONSTRAINT_NAME}"'
    )
    op.execute("ALTER TABLE host_group_memberships DROP COLUMN role")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE host_group_memberships ADD COLUMN role VARCHAR(32)"
    )
    op.execute(
        f'ALTER TABLE host_group_memberships ADD CONSTRAINT "{CHECK_CONSTRAINT_NAME}" '
        "CHECK (role IS NULL OR role IN ('control_plane', 'worker'))"
    )
