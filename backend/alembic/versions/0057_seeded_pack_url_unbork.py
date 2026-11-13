"""Point the seeded playbooks pack at its new GitHub home.

The repositories moved from the ``open-labdog`` organization to
``open-unbork`` (``labdog-playbooks`` became ``unbork-playbooks``). GitHub
redirects the old URL, but only for as long as nobody re-creates a repo at
the old path, so installs should not depend on the redirect.

Only a row that still holds the exact seeded URL is rewritten. A row the
operator pointed somewhere else, such as an internal mirror, is left alone.

Revision ID: 0057_seeded_pack_url_unbork
Revises: 0056_ai_session_subject
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0057_seeded_pack_url_unbork"
down_revision = "0056_ai_session_subject"
branch_labels = None
depends_on = None

_OLD_URLS = (
    "https://github.com/open-labdog/labdog-playbooks",
    "https://github.com/open-labdog/labdog-playbooks.git",
)
_NEW_URL = "https://github.com/open-unbork/unbork-playbooks"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE git_repositories SET url = :new WHERE url IN :old").bindparams(
            sa.bindparam("old", expanding=True)
        ),
        {"new": _NEW_URL, "old": list(_OLD_URLS)},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE git_repositories SET url = :old WHERE url = :new"),
        {"old": _OLD_URLS[0], "new": _NEW_URL},
    )
