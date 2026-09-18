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

# Provider backends. The two Claude Code ones both authenticate with a
# subscription token rather than an API key, and differ in who runs the
# agent loop: "claude_cli" shells out once per prompt and cannot call
# tools, while "claude_agent" drives Claude Code through the Claude Agent
# SDK, which runs the loop itself and calls LabDog's tools over an
# in-process MCP server.
#
# Who owns the loop is also why they stream differently, which looks like
# an inconsistency and is not one. "anthropic" and "openai_compat" are
# LLMProviders: AgentLoop asks them for one turn at a time, so LabDog owns
# the token stream and forwards deltas as it reads them. "claude_agent"
# cannot be an LLMProvider at all — it is never asked for a turn — so what
# arrives is a whole AssistantMessage, and that is the unit the runner
# emits, counts usage against, persists, and checks the approval park on.
#
# Token-level streaming there is therefore not a missing feature but a
# different shape: the SDK's include_partial_messages would add a second
# event stream that still has to be reconciled with the message that
# follows it, on the runner's most safety-critical path. Deliberately not
# done. See `git log --grep "agentic sessions on a Claude subscription"`.
PROVIDER_TYPES = ("openai_compat", "anthropic", "claude_cli", "claude_agent")

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

APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired")


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
    # When the stored credential was last written. Only meaningful for the
    # subscription backends: `claude setup-token` mints a token that lasts
    # one year, and an expired one stops an unattended session dead with no
    # warning beforehand. `updated_at` cannot stand in for this — it moves
    # whenever any field is edited.
    credential_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
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
    # Opt out of the pre-change Proxmox snapshot for this session only.
    #
    # Composes with ``ai.snapshot_before_mutating`` by agreement, not
    # override: a snapshot is taken only when the instance setting is on
    # *and* this is false. Neither can force one against the other, so
    # turning snapshots off globally cannot be undone per session.
    skip_snapshots: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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
    # Why a run ended before the model was finished — "token budget
    # (10000)", "turn limit (40)", "cancelled by operator". Set only when
    # something cut the run short; a session that reached its own
    # conclusion leaves it NULL.
    #
    # Separate from error_message because a capped run is not an error. It
    # did what it was asked until the budget it was given ran out, and
    # filing that as a failure is as misleading as the green "succeeded"
    # badge it used to get on its own.
    stopped_reason: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)
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
    approval_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_approval_requests.id", ondelete="SET NULL"), nullable=True, default=None
    )
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
    # When the retention sweep removed that snapshot. The name is kept
    # afterwards: it is the record of what protected this change, and an
    # operator reading the transcript later should be able to tell "there
    # was a rollback point, it has since expired" from "there never was
    # one" — which is the difference that decides what they do next.
    snapshot_pruned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class AIApprovalRequest(Base):
    """One mutating call, paused until an operator decides.

    The row is the whole of the session's parked state that a human needs
    to see: what was asked for, on which host, and why the classifier
    thought it was a write. The session's own conversational state lives
    where it already lived — the transcript for ``AgentLoop``, the CLI's
    session file for ``AgentSDKRunner`` — so nothing here needs to
    reconstruct a loop.

    ``command_preview`` is stored rather than re-derived from
    ``arguments`` at render time. What the operator approved has to be
    exactly what runs, and a preview computed twice is a preview that can
    differ twice.
    """

    __tablename__ = "ai_approval_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("ai_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # The exact call to run on approval. Never rebuilt from the model's
    # later output: approval is for these arguments, not for the intent
    # behind them.
    arguments: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    target_host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True, default=None
    )
    # The model's own stated reason, from the tool's `purpose` argument.
    # Advisory: it explains intent, it never affects the classification.
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    command_preview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Why the classifier called this a write, verbatim from the Verdict.
    classification: Mapped[str] = mapped_column(String(16), nullable=False, default="mutating")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    decided_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, default=None
    )
    # The operator's own note, shown to the model on resume. A rejection
    # with a reason teaches; one without just blocks.
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    # An abandoned gate must not park a session forever. Reaped, not
    # merely displayed as stale — see app.tasks.ai_approvals.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
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


#: Where an alert came from. Both producers write the same rows — the
#: webhook is the fast path, the poller is the fallback for when Grafana
#: cannot reach LabDog — so the source is recorded rather than inferred.
ALERT_SOURCES = ("grafana_webhook", "alertmanager_poll")

#: Alertmanager's own vocabulary, kept verbatim rather than mapped onto
#: something LabDog-flavoured: an operator comparing LabDog against
#: Grafana should not have to translate.
ALERT_STATUSES = ("firing", "resolved")

#: What happened to the auto-investigation policy for one alert. Recorded
#: even when nothing ran, because "no session" has several causes and an
#: operator asking "why didn't it look at this?" needs the answer stored
#: rather than reconstructed from logs that may have rotated.
INVESTIGATION_OUTCOMES = (
    "started",
    "skipped_disabled",
    "skipped_severity",
    "skipped_resolved",
    "skipped_duplicate",
    "skipped_budget",
    "failed",
)


class AlertEvent(Base):
    """One alert as LabDog received it, deduplicated by fingerprint.

    **Dedup is on ``(fingerprint, starts_at)``, not fingerprint alone.**
    Alertmanager's fingerprint is a hash of the alert's label set, so the
    same rule firing for the same host produces the same fingerprint every
    time it fires — this month and next. Keying on it alone would collapse
    a recurrence into the original row and lose the history; keying on it
    with ``starts_at`` treats one continuous firing as one row and a
    genuinely new firing as a new one.

    That also makes the webhook and the poller idempotent against each
    other for free: whichever arrives second finds the row and increments
    ``dedup_count`` instead of creating a duplicate. They are deliberately
    both enabled — the webhook is immediate, the poller catches what a
    LabDog that was down or unreachable would otherwise never hear about.

    ``labels`` and ``annotations`` are stored whole. LabDog reads a few
    keys out of them (``alertname``, ``severity``, ``instance``), but an
    investigation is only as good as its context, and discarding the rest
    to fit a schema would throw away exactly the detail that makes one
    alert different from another.
    """

    __tablename__ = "alert_events"
    __table_args__ = (
        UniqueConstraint("fingerprint", "starts_at", name="uq_alert_events_fingerprint_starts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # Alertmanager's label-set hash. Indexed on its own as well as in the
    # unique constraint: "every firing of this rule" is the query an
    # operator runs when deciding whether something is flapping.
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    alertname: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Read from labels["severity"] when present. Free text rather than an
    # enum: Grafana lets you label an alert anything, and refusing to
    # store "sev1" because it is not "critical" would lose the alert.
    severity: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="firing", index=True)
    labels: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    annotations: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    # Alertmanager's own timestamps, not LabDog's. When the alert began
    # and ended according to the system that decided it was an alert.
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # How many times LabDog has been told about this same firing. 1 on
    # creation. A high count on a short-lived alert is what flapping
    # looks like from here.
    dedup_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # The host this alert is about, when LabDog can work it out from the
    # labels. NULL when it cannot — plenty of alerts are about a service
    # or a cluster rather than a machine LabDog manages.
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    investigation_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_sessions.id", ondelete="SET NULL"), nullable=True
    )
    # Why there is or is not a session. See INVESTIGATION_OUTCOMES.
    investigation_outcome: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )
    # Free text for the outcome, e.g. the budget message that stopped it.
    investigation_detail: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
