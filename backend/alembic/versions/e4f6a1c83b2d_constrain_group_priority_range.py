"""constrain group priority range to 1-1000

Revision ID: e4f6a1c83b2d
Revises: c3e5f9b20d1a
Create Date: 2026-04-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4f6a1c83b2d'
down_revision: Union[str, Sequence[str], None] = 'c3e5f9b20d1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Clamp existing priorities to 1-1000, resolve collisions, add CHECK."""
    conn = op.get_bind()

    # 1. Clamp out-of-range values
    conn.execute(sa.text(
        "UPDATE host_groups SET priority = LEAST(GREATEST(priority, 1), 1000) "
        "WHERE priority < 1 OR priority > 1000"
    ))

    # 2. Resolve collisions created by clamping — reassign duplicates
    # to the nearest available slot within 1-1000
    conn.execute(sa.text("""
        WITH duplicates AS (
            SELECT id, priority,
                   ROW_NUMBER() OVER (PARTITION BY priority ORDER BY id) AS rn
            FROM host_groups
        ),
        used AS (
            SELECT DISTINCT priority FROM host_groups
        ),
        available AS (
            SELECT g AS slot
            FROM generate_series(1, 1000) g
            WHERE g NOT IN (SELECT priority FROM used)
            ORDER BY g
        ),
        to_fix AS (
            SELECT id,
                   ROW_NUMBER() OVER (ORDER BY id) AS fix_rn
            FROM duplicates
            WHERE rn > 1
        ),
        slots AS (
            SELECT slot,
                   ROW_NUMBER() OVER (ORDER BY slot) AS slot_rn
            FROM available
        )
        UPDATE host_groups
        SET priority = s.slot
        FROM to_fix t
        JOIN slots s ON s.slot_rn = t.fix_rn
        WHERE host_groups.id = t.id
    """))

    # 3. Add CHECK constraint
    op.create_check_constraint(
        "ck_host_groups_priority_range",
        "host_groups",
        "priority >= 1 AND priority <= 1000",
    )


def downgrade() -> None:
    """Remove priority range CHECK constraint."""
    op.drop_constraint("ck_host_groups_priority_range", "host_groups", type_="check")
