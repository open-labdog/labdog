"""Drain the outbox: coalesce per recipient, send, retry.

Runs once a minute from ``app.tasks.notifications``. Everything due for
one recipient goes out as one email, so an alert storm becomes one
message per minute rather than one per alert.

**At least once, not exactly once.** Rows are locked with ``FOR UPDATE
SKIP LOCKED`` for the whole drain, so two overlapping drains cannot both
send them; but a worker killed after the server accepted a message and
before the commit will send it again next minute. A duplicate email is
the right failure to have over a lost one.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.notifications import email as smtp
from app.notifications.events import EVENT_TYPES
from app.notifications.models import Notification
from app.notifications.service import SUBJECT_PREFIX, is_usable, load_smtp, public_url, smtp_config

logger = logging.getLogger(__name__)

#: After this many failed attempts a row is given up on.
MAX_ATTEMPTS = 6

#: Rows taken per drain. A backlog bigger than this drains over several
#: minutes rather than in one enormous transaction.
BATCH_LIMIT = 500

#: In a digest, items past this many are listed by subject only. Twenty
#: alerts in full is already more than anyone reads in one email.
FULL_ITEMS = 20

_LABEL = {e.key: e.label.lower() for e in EVENT_TYPES}

NO_URL_ERROR = (
    "notifications.public_url is not set, so this message's link cannot be built. "
    "LabDog will not guess its own address from a request; set it on the Email page."
)


def backoff(attempts: int) -> timedelta:
    """1, 2, 4, 8, 16 minutes, capped at an hour."""
    return timedelta(minutes=min(2 ** max(attempts - 1, 0), 60))


def _link(base: str, path: str | None) -> str:
    return f"{base}{path}" if path else ""


def compose(rows: list[Notification], base_url: str) -> smtp.OutgoingEmail:
    """One email for everything due to one recipient."""
    recipient = rows[0].recipient
    footer = "\n\n--\nYou get these because you subscribed to them in LabDog." + (
        f"\nChange that at {base_url}/notifications" if base_url else ""
    )

    if len(rows) == 1:
        row = rows[0]
        link = _link(base_url, row.link_path)
        body = row.body + (f"\n\nOpen in LabDog: {link}" if link else "") + footer
        return smtp.OutgoingEmail(to=recipient, subject=row.subject, body=body)

    counts = Counter(_LABEL.get(r.event_type, r.event_type) for r in rows)
    summary = ", ".join(f"{n} × {label}" for label, n in counts.most_common())
    parts = [f"LabDog has {len(rows)} notifications for you: {summary}."]
    for index, row in enumerate(rows):
        title = row.subject.removeprefix(SUBJECT_PREFIX).strip()
        link = _link(base_url, row.link_path)
        if index < FULL_ITEMS:
            parts.append(f"{'=' * 60}\n{title}\n{'=' * 60}\n{row.body}")
            if link:
                parts.append(f"Open in LabDog: {link}")
        else:
            if index == FULL_ITEMS:
                parts.append(f"{'=' * 60}\nAnd {len(rows) - FULL_ITEMS} more:")
            parts.append(f"  - {title}" + (f"  {link}" if link else ""))
    return smtp.OutgoingEmail(
        to=recipient,
        subject=f"{SUBJECT_PREFIX} {len(rows)} notifications: {summary}",
        body="\n\n".join(parts) + footer,
    )


def _sent(rows: list[Notification], now: datetime) -> None:
    for row in rows:
        row.status = "sent"
        row.sent_at = now
        row.last_attempt_at = now
        row.attempts += 1
        row.last_error = None


def _retry(rows: list[Notification], error: str, now: datetime) -> None:
    for row in rows:
        row.attempts += 1
        row.last_attempt_at = now
        row.last_error = error[:2000]
        if row.attempts >= MAX_ATTEMPTS:
            row.status = "failed"
        else:
            row.next_attempt_at = now + backoff(row.attempts)


def _fail(rows: list[Notification], error: str, now: datetime) -> None:
    for row in rows:
        row.status = "failed"
        row.last_attempt_at = now
        row.last_error = error


async def drain(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    send: Callable[[smtp.SMTPConfig, list[smtp.OutgoingEmail]], list[str | None]] = smtp.send,
) -> dict[str, int]:
    """Send everything due. The caller commits.

    ``send`` is injectable so tests can stand in for the mail server.
    """
    now = now or datetime.now(UTC)
    settings_row = await load_smtp(db)

    if not is_usable(settings_row):
        # Switched off with mail still queued. Holding it would deliver a
        # backlog of stale alerts the moment email came back on, which is
        # the thing "off" exists to prevent.
        result = await db.execute(
            update(Notification)
            .where(Notification.status == "pending")
            .values(
                status="failed",
                last_error="Email was switched off before this was sent.",
                last_attempt_at=now,
            )
        )
        return {"sent": 0, "failed": result.rowcount or 0, "retrying": 0, "emails": 0}

    rows = (
        (
            await db.execute(
                select(Notification)
                .where(
                    Notification.status == "pending",
                    Notification.channel == "email",
                    Notification.next_attempt_at <= now,
                )
                .order_by(Notification.created_at, Notification.id)
                .limit(BATCH_LIMIT)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return {"sent": 0, "failed": 0, "retrying": 0, "emails": 0}

    base_url = await public_url(db)
    stats = {"sent": 0, "failed": 0, "retrying": 0, "emails": 0}

    sendable: list[Notification] = []
    for row in rows:
        if row.link_path and not base_url:
            _fail([row], NO_URL_ERROR, now)
            stats["failed"] += 1
        else:
            sendable.append(row)

    by_recipient: dict[str, list[Notification]] = defaultdict(list)
    for row in sendable:
        by_recipient[row.recipient].append(row)
    if not by_recipient:
        return stats

    groups = list(by_recipient.values())
    emails = [compose(group, base_url) for group in groups]
    config = smtp_config(settings_row)

    try:
        results = await asyncio.to_thread(send, config, emails)
    except smtp.SMTPSendError as exc:
        results = [str(exc)] * len(groups)

    for group, error in zip(groups, results, strict=True):
        if error is None:
            _sent(group, now)
            stats["sent"] += len(group)
            stats["emails"] += 1
        else:
            _retry(group, error, now)
            for row in group:
                stats["failed" if row.status == "failed" else "retrying"] += 1

    if stats["failed"] or stats["retrying"]:
        logger.warning(
            "notifications: %d sent, %d retrying, %d given up",
            stats["sent"],
            stats["retrying"],
            stats["failed"],
        )
    return stats
