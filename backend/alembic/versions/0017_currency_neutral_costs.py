"""Drop the USD assumption from cost columns and settings.

LabDog never converts between currencies — the operator types a rate in
and reads a total back out in whatever unit they typed. Naming the columns
`_usd` asserted something the code does not enforce and was simply wrong
for anyone entering euro rates, which is most of the non-US audience.

Columns and setting keys become currency-neutral; a new `ai.currency`
setting says how to *format* the numbers. It is a display concern only:
changing it relabels existing figures rather than converting them, which
is the honest behaviour when no exchange rate was ever involved.

Revision ID: 0017_currency_neutral_costs
Revises: 0016_ai_scoping_and_cost
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017_currency_neutral_costs"
down_revision = "0016_ai_scoping_and_cost"
branch_labels = None
depends_on = None


_SETTING_RENAMES = (
    ("ai.budget_daily_usd", "ai.budget_daily"),
    ("ai.budget_monthly_usd", "ai.budget_monthly"),
)


def upgrade() -> None:
    op.alter_column("ai_providers", "monthly_budget_usd", new_column_name="monthly_budget")
    op.alter_column("ai_sessions", "estimated_cost_usd", new_column_name="estimated_cost")
    op.alter_column("ai_usage_days", "cost_usd", new_column_name="cost")

    # Operator-set values live in app_settings rows keyed by name, so the
    # rename has to carry them across or a configured budget silently
    # reverts to the default and stops enforcing.
    for old, new in _SETTING_RENAMES:
        op.execute(
            sa.text("UPDATE app_settings SET key = :new WHERE key = :old").bindparams(
                new=new, old=old
            )
        )


def downgrade() -> None:
    for old, new in _SETTING_RENAMES:
        op.execute(
            sa.text("UPDATE app_settings SET key = :old WHERE key = :new").bindparams(
                new=new, old=old
            )
        )
    op.alter_column("ai_usage_days", "cost", new_column_name="cost_usd")
    op.alter_column("ai_sessions", "estimated_cost", new_column_name="estimated_cost_usd")
    op.alter_column("ai_providers", "monthly_budget", new_column_name="monthly_budget_usd")
