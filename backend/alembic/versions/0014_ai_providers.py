"""Add ``ai_providers`` table.

A configured LLM endpoint. ``provider_type`` selects the wire protocol
("openai_compat" covers Ollama/vLLM/OpenRouter/OpenAI, "anthropic" the
Messages API, "claude_cli" the locally installed Claude Code CLI).
``encrypted_api_key`` is AES-256-GCM with AAD ``ai_provider:{id}``;
``ca_cert_pem`` is plaintext (CA certs are public). Pricing is
operator-entered — OpenAI-compatible servers cannot report their own
rates — and 0 means free/self-hosted, which makes the USD budgets a
no-op for local models.

Revision ID: 0014_ai_providers
Revises: 0013_drift_samples
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014_ai_providers"
down_revision = "0013_drift_samples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("encrypted_api_key", sa.LargeBinary(), nullable=True),
        sa.Column("verify_ssl", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ca_cert_pem", sa.Text(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="4096"),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allow_cloud_egress", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("input_cost_per_mtok", sa.Float(), nullable=False, server_default="0"),
        sa.Column("output_cost_per_mtok", sa.Float(), nullable=False, server_default="0"),
        sa.Column("monthly_budget_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_providers_name", "ai_providers", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_ai_providers_name", table_name="ai_providers")
    op.drop_table("ai_providers")
