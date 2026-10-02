"""Queue notifications, and say what each event's message is.

:func:`notify` is the only way in. It writes outbox rows inside the
caller's transaction — inside a savepoint, so a failure here can never
roll back the alert or approval that caused it — and returns. Sending is
``app.tasks.notifications``' job.

Every value that came from outside LabDog's own code — alert labels and
annotations, commands the model proposed, its stated reasons, report text
— goes through :func:`clean` before it lands in a message. An email is
stored in places LabDog does not control, so it gets the same credential
redaction the AI transcript does, and none of it can carry a control
character into a mail client.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.redaction import redact
from app.notifications.email import SMTPConfig
from app.notifications.events import EVENT_KEYS
from app.notifications.models import (
    SMTP_SETTINGS_ID,
    Notification,
    NotificationSubscription,
    SMTPSettings,
)

logger = logging.getLogger(__name__)

SUBJECT_PREFIX = "[LabDog]"

# C0 control characters except tab and newline, and DEL.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(value: Any, limit: int = 500) -> str:
    """Make one externally-sourced value safe to put in a message."""
    text = _CONTROL.sub("", redact(str(value)))
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def one_line(value: Any, limit: int = 120) -> str:
    return " ".join(clean(value, limit * 2).split())[:limit]


# ---------------------------------------------------------------------------
# SMTP settings
# ---------------------------------------------------------------------------


async def load_smtp(db: AsyncSession) -> SMTPSettings | None:
    return await db.get(SMTPSettings, SMTP_SETTINGS_ID)


def is_usable(row: SMTPSettings | None) -> bool:
    """Switched on and complete enough to try a send."""
    return bool(row and row.enabled and row.host and row.from_address)


def smtp_config(row: SMTPSettings, *, password: str | None = None) -> SMTPConfig:
    """The transport settings for ``row``, decrypting its password.

    ``password`` overrides the stored one — the test button sends with a
    draft the operator has not saved yet.
    """
    if password is None and row.encrypted_password:
        from app.crypto import decrypt_ssh_key, get_master_key

        password = decrypt_ssh_key(row.encrypted_password, get_master_key())
    return SMTPConfig(
        host=row.host,
        port=row.port,
        tls_mode=row.tls_mode,
        username=row.username or None,
        password=password,
        from_address=row.from_address,
    )


async def public_url(db: AsyncSession) -> str:
    from app.settings_service import get_setting_typed

    return str(await get_setting_typed("notifications.public_url", db)).rstrip("/")


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------


async def notify(
    db: AsyncSession,
    event_type: str,
    *,
    subject: str,
    body: str,
    link_path: str | None = None,
    dedupe_key: str | None = None,
) -> int:
    """Queue one message per subscriber to ``event_type``. Returns how many.

    Does nothing while email is off: switching it on later must not
    deliver a backlog of alerts that stopped mattering hours ago.

    Never raises for anything that can go wrong at run time. The callers
    are alert intake, the approval gate and the session runner, and none
    of them should fail because a notification could not be queued — the
    savepoint keeps a failure here out of their transaction, and the log
    says what went wrong. An unknown ``event_type`` does raise: that is a
    typo in LabDog's code, and a test should catch it, not a log line.
    """
    if event_type not in EVENT_KEYS:
        raise ValueError(f"unknown notification event {event_type!r}")
    try:
        async with db.begin_nested():
            if not is_usable(await load_smtp(db)):
                return 0
            from app.models.user import User

            recipients = (
                await db.execute(
                    select(User.id, User.email)
                    .join(NotificationSubscription, NotificationSubscription.user_id == User.id)
                    .where(
                        NotificationSubscription.event_type == event_type,
                        NotificationSubscription.channel == "email",
                        User.is_active.is_(True),
                    )
                )
            ).all()
            if not recipients:
                return 0

            now = datetime.now(UTC)
            full_subject = f"{SUBJECT_PREFIX} {subject}"
            await db.execute(
                pg_insert(Notification)
                .values(
                    [
                        {
                            "event_type": event_type,
                            "channel": "email",
                            "user_id": user_id,
                            "recipient": email,
                            "subject": full_subject[:300],
                            "body": body,
                            "link_path": link_path,
                            "dedupe_key": dedupe_key,
                            "status": "pending",
                            "attempts": 0,
                            "next_attempt_at": now,
                            "created_at": now,
                        }
                        for user_id, email in recipients
                    ]
                )
                .on_conflict_do_nothing(constraint="uq_notifications_user_dedupe")
            )
            return len(recipients)
    except Exception:
        logger.exception("notifications: could not queue %s", event_type)
        return 0


# ---------------------------------------------------------------------------
# What each event says
# ---------------------------------------------------------------------------


async def _hostname(db: AsyncSession, host_id: int | None) -> str | None:
    if host_id is None:
        return None
    from app.models.host import Host

    return (await db.execute(select(Host.hostname).where(Host.id == host_id))).scalar_one_or_none()


def _when(moment: datetime | None) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if moment else "unknown"


def _pairs(mapping: dict | None, *, skip: tuple[str, ...] = ()) -> str:
    rows = [
        f"  {one_line(key, 60)}: {one_line(value, 200)}"
        for key, value in sorted((mapping or {}).items())
        if key not in skip
    ]
    return "\n".join(rows) or "  (none)"


async def notify_alert_fired(db: AsyncSession, event: Any) -> int:
    """A new firing alert was recorded."""
    host = await _hostname(db, event.host_id)
    severity = one_line(event.severity, 32) if event.severity else "no severity"
    where = f" on {host}" if host else ""
    annotations = event.annotations or {}

    lines = [
        "An alert is firing.",
        "",
        f"Alert:     {one_line(event.alertname)}",
        f"Severity:  {severity}",
        f"Host:      {host or 'not a host LabDog manages'}",
        f"Started:   {_when(event.starts_at)}",
    ]
    for key in ("summary", "description"):
        if annotations.get(key):
            lines += ["", f"{key.capitalize()}:", clean(annotations[key], 1000)]
    lines += ["", "Labels:", _pairs(event.labels, skip=("alertname",))]
    lines += [
        "",
        "The labels and annotations above come from the monitoring system, not from LabDog.",
    ]
    return await notify(
        db,
        "alert_fired",
        subject=f"Firing: {one_line(event.alertname, 80)} ({severity}){where}",
        body="\n".join(lines),
        link_path="/alerts",
        dedupe_key=f"alert_fired:{event.id}",
    )


async def _approval_lines(db: AsyncSession, approval: Any) -> tuple[list[str], str, str]:
    """The shared description of one request: lines, host label, command."""
    from app.ai.models import AISession

    session = await db.get(AISession, approval.session_id)
    host = await _hostname(db, approval.target_host_id) or "no host"
    command = clean(approval.command_preview, 1000)
    lines = [
        f"Session:   {one_line(session.title if session else '', 120) or 'untitled'} "
        f"(#{approval.session_id})",
        f"Host:      {host}",
        f"Command:   {command}",
    ]
    if approval.summary:
        lines.append(f"Why:       {clean(approval.summary, 600)}")
    if approval.reason:
        lines.append(f"Change because: {clean(approval.reason, 300)}")
    return lines, host, command


_DECIDE_IN_UI = (
    "Approve or reject it in LabDog, signed in. Nothing in this email can approve it: "
    "a link that ran a root command would make this inbox a credential."
)


async def notify_approval_requested(db: AsyncSession, approval: Any) -> int:
    lines, host, command = await _approval_lines(db, approval)
    body = "\n".join(
        [
            "The assistant wants to change a host and is waiting for a decision.",
            "",
            *lines,
            f"Expires:   {_when(approval.expires_at)}",
            "",
            _DECIDE_IN_UI,
        ]
    )
    return await notify(
        db,
        "approval_requested",
        subject=f"Approval needed on {host}: {one_line(command, 80)}",
        body=body,
        link_path=f"/assistant?session={approval.session_id}",
        dedupe_key=f"approval_requested:{approval.id}",
    )


async def notify_approval_expiring(db: AsyncSession, approval: Any) -> int:
    lines, host, command = await _approval_lines(db, approval)
    body = "\n".join(
        [
            f"This change is still waiting for a decision, and the request expires at "
            f"{_when(approval.expires_at)}. After that the session finishes without it.",
            "",
            *lines,
            "",
            _DECIDE_IN_UI,
        ]
    )
    return await notify(
        db,
        "approval_expiring",
        subject=f"Approval expires soon on {host}: {one_line(command, 80)}",
        body=body,
        link_path=f"/assistant?session={approval.session_id}",
        dedupe_key=f"approval_expiring:{approval.id}",
    )


async def notify_approval_expired(db: AsyncSession, approval: Any) -> int:
    lines, host, command = await _approval_lines(db, approval)
    body = "\n".join(
        [
            "Nobody decided on this change in time, so the request expired. The session "
            "continues without it and writes up what it found.",
            "",
            *lines,
        ]
    )
    return await notify(
        db,
        "approval_expired",
        subject=f"Approval expired on {host}: {one_line(command, 80)}",
        body=body,
        link_path=f"/assistant?session={approval.session_id}",
        dedupe_key=f"approval_expired:{approval.id}",
    )


async def scan_expiring_approvals(db: AsyncSession, now: datetime | None = None) -> int:
    """Queue a warning for each pending request inside the warning window.

    Run on every delivery tick. The dedupe key makes repeats free, so the
    scan does not need to remember what it already warned about.
    """
    from app.ai.models import AIApprovalRequest
    from app.settings_service import get_setting_typed

    hours = int(await get_setting_typed("notifications.approval_expiry_warning_hours", db))
    if hours <= 0:
        return 0
    now = now or datetime.now(UTC)
    due = (
        (
            await db.execute(
                select(AIApprovalRequest).where(
                    AIApprovalRequest.status == "pending",
                    AIApprovalRequest.expires_at.is_not(None),
                    AIApprovalRequest.expires_at > now,
                    AIApprovalRequest.expires_at <= now + timedelta(hours=hours),
                )
            )
        )
        .scalars()
        .all()
    )
    queued = 0
    for approval in due:
        queued += await notify_approval_expiring(db, approval)
    return queued


async def notify_remediation(db: AsyncSession, session: Any) -> int:
    """A full-auto alert session finished, having changed its host.

    Sent whatever the outcome, because the change happened whatever the
    outcome: a session that restarted a service and then hit its time
    limit still restarted the service. Nothing is sent for one that
    changed nothing — the alert email already said the alert fired.
    """
    from app.ai.models import AIToolCall, AlertEvent

    changes = (
        (
            await db.execute(
                select(AIToolCall)
                .where(
                    AIToolCall.session_id == session.id,
                    AIToolCall.classification == "mutating",
                    AIToolCall.status.in_(("executed", "error")),
                )
                .order_by(AIToolCall.id)
            )
        )
        .scalars()
        .all()
    )
    if not changes:
        return 0

    event = await db.get(AlertEvent, session.alert_event_id) if session.alert_event_id else None
    alertname = one_line(event.alertname, 80) if event else "an alert"
    host = await _hostname(db, (session.target_host_ids or [None])[0]) or "its host"

    lines = [
        f"A full-auto investigation of {alertname} changed {host} with nobody approving it.",
        "",
        f"Session:   #{session.id}, {session.status}"
        + (
            f" (stopped: {one_line(session.stopped_reason, 100)})" if session.stopped_reason else ""
        ),
        "",
        "What ran:",
    ]
    for call in changes:
        command = clean((call.arguments or {}).get("command", call.tool_name), 400)
        outcome = "ok" if call.status == "executed" else "failed"
        lines.append(f"  - {command}  [{outcome}: {one_line(call.result_summary or '', 120)}]")
        lines.append(
            f"    snapshot before it: {call.snapshot_name}"
            if call.snapshot_name
            else "    no snapshot was taken before it"
        )
    if session.report_markdown:
        lines += ["", "Its report opens:", clean(session.report_markdown, 1200)]
    elif session.error_message:
        lines += ["", f"It failed: {clean(session.error_message, 600)}"]

    return await notify(
        db,
        "alert_remediation",
        subject=f"Automatic fix on {host}: {alertname}",
        body="\n".join(lines),
        link_path=f"/assistant?session={session.id}",
        dedupe_key=f"alert_remediation:{session.id}:{len(changes)}",
    )
