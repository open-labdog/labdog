"""SEC-27: record the git server's SSH host key so syncs can be verified.

Pack and GitOps syncs both ran with ``StrictHostKeyChecking=accept-new``
*and* ``UserKnownHostsFile=/dev/null``. That combination never verifies
anything: each invocation starts from an empty known-hosts file, so
there is never a first use and therefore never a mismatch. Anyone able
to intercept the connection could serve arbitrary playbooks, which
LabDog then runs against the fleet as root.

This column holds the ``known_hosts`` line learned on the next SSH sync.
It is left NULL here rather than backfilled — there is nothing truthful
to backfill it with, and a fabricated entry would pin every repository
to a key nobody ever saw. The first sync after upgrading records what
the server presents (unchanged trust posture, one time), and every sync
after that is verified against it.

Revision ID: 0029_git_repo_host_key
Revises: 0028_hosts_module_type
"""

import sqlalchemy as sa

from alembic import op

revision = "0029_git_repo_host_key"
down_revision = "0028_hosts_module_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "git_repositories",
        sa.Column("ssh_host_key_entry", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("git_repositories", "ssh_host_key_entry")
