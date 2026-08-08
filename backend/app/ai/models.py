"""ORM models for the AI subsystem.

``AIProvider`` follows the established integration-credential pattern
(:class:`app.grafana.models.GrafanaInstance`,
:class:`app.proxmox.models.ProxmoxNode`): an AES-256-GCM secret column
handled exclusively in the API/task layers, never here.

The remaining tables record one agentic run each: ``AISession`` is the
run, ``AIMessage`` its transcript (and the context fed back to the
model), ``AIToolCall`` every capability invocation with its safety
verdict, and ``AIUsageDay`` a rolled-up spend ledger that outlives the
sessions it was derived from.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Provider backends. "claude_cli" shells out to the locally installed
# Claude Code CLI and therefore carries no API key of its own.
PROVIDER_TYPES = ("openai_compat", "anthropic", "claude_cli")

# How much the model is allowed to do without a human in the loop.
AUTONOMY_LEVELS = ("read_only", "approval", "full_auto")

SESSION_MODES = ("chat", "scheduled", "verify", "alert_investigation")

SESSION_STATUSES = (
    "queued",
    "running",
    "waiting_approval",
    "succeeded",
    "failed",
    "cancelled",
)


class AIProvider(Base):
    """A configured LLM endpoint.

    ``provider_type`` selects the wire protocol, not the vendor: any
    OpenAI-compatible server (Ollama, vLLM, LM Studio, OpenRouter,
    OpenAI itself) uses ``openai_compat``.

    Pricing is operator-entered because OpenAI-compatible endpoints have
    no way to report their own rates. Leaving it at 0 is correct for
    self-hosted models and makes the money budgets a no-op for them —
    the token and iteration caps still apply.
    """

    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Base URL of the API. NULL for claude_cli (no HTTP endpoint), and
    # optional for anthropic (defaults to the public API).
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    # AES-256-GCM, AAD "ai_provider:{id}". NULL for claude_cli and for
    # unauthenticated local servers.
    encrypted_api_key: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True, default=None
    )
    verify_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Plaintext PEM CA certificate (NOT encrypted — CA certs are public).
    ca_cert_pem: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Money per million tokens, operator-entered, in whatever currency
    # ai.currency names. LabDog never converts, so the unit is simply
    # whatever the operator typed. 0 == free/self-hosted.
    input_cost_per_mtok: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    output_cost_per_mtok: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Optional per-provider monthly ceiling, same unit as the rates
    # above; 0 == unlimited. Useful
    # when a free local provider and a paid cloud one are both configured.
    monthly_budget: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class AISession(Base):
    """One agentic run: a chat conversation, a scheduled mission, a
    verify judgement, or an alert investigation.

    When driven by the ``_builtin.ai_task`` pseudo-action, ``action_run_id``
    links back to the owning :class:`app.models.action_run.ActionRun` so the
    run inherits scheduling, history, and cancellation from the action
    subsystem. Ad-hoc chat sessions leave it NULL.

    ``resume_state`` holds the loop cursor while the session is parked on
    an approval, so the Celery worker can return instead of blocking for
    however long the operator takes to decide.
    """

    __tablename__ = "ai_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="chat")
    title: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)
    mission: Mapped[str] = mapped_column(Text, nullable=False)
    autonomy_level: Mapped[str] = mapped_column(String(16), nullable=False, default="read_only")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    # Host allowlist — the model may not touch anything outside it.
    target_host_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=None)
    # Tool allowlist. NULL means every registered tool. Naming a subset
    # bounds both spend and blast radius with one control: a scheduled log
    # sweep restricted to query_loki cannot open an SSH session at all, and
    # cannot spend what an unbounded journalctl would.
    allowed_tools: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=None)
    # Action keys this session may invoke as remediation. Empty or NULL
    # means it may not change anything through the action system.
    allowed_action_keys: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=None)
    action_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("action_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Set in phase 4; the FK is added with the alert_events table.
    alert_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # True when the backend could not report token usage, so cost is a
    # floor rather than an estimate.
    cost_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    command_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    report_markdown: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    resume_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class AIMessage(Base):
    """One transcript entry — both the UI's view of the conversation and
    the context replayed to the provider on the next turn.

    ``tool_calls`` stores the provider-normalised call list (see
    ``app.ai.providers.base``) so a session can survive being switched
    between backends.
    """

    __tablename__ = "ai_messages"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_ai_messages_session_id_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("ai_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # "system" | "user" | "assistant" | "tool"
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=None)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class AIToolCall(Base):
    """Record of one capability invocation and the safety verdict it got.

    Written for every attempt — including blocked and rejected ones — so
    the transcript shows what the model *wanted* to do, not just what it
    was allowed to do. Command executions additionally write an
    :class:`app.models.audit_log.AuditLog` row.
    """

    __tablename__ = "ai_tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("ai_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_seq: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    # "read_only" | "mutating" | "denied" | "unknown"
    classification: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    target_host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True, default=None
    )
    # FK added in phase 3 alongside ai_approval_requests.
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # "proposed" | "approved" | "rejected" | "executed" | "blocked" | "error"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Size of the result fed back to the model. Recorded exactly, in
    # characters, rather than as an estimated token count: characters are
    # what we actually know, and the ratio is near-constant, so this is
    # enough to compare what a tool costs. It is the only way to answer
    # "is reading logs via Loki cheaper than via SSH" with data instead of
    # intuition — the answer depends on the query and is worth measuring.
    result_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Proxmox snapshot taken before this call, when it was mutating.
    snapshot_name: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class AIUsageDay(Base):
    """Daily spend ledger, one row per (day, provider).

    Kept separate from ``AISession`` so budget checks are a single cheap
    aggregate rather than a scan over transcripts, and so accounting
    survives session deletion or retention pruning. Incremented with an
    upsert after every provider turn.
    """

    __tablename__ = "ai_usage_days"
    __table_args__ = (
        UniqueConstraint("usage_date", "provider_id", name="uq_ai_usage_days_usage_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
