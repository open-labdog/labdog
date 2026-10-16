"""Email settings, the signed-in user's subscriptions, and the delivery log.

Any signed-in user may change the mail server, like every other
integration — the privilege model is flat. What any user may *not* do is
read the SMTP password back: it is write-only, encrypted at rest, and the
response says only whether one is set.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.db import get_db
from app.models.user import User
from app.notifications import email as smtp
from app.notifications.events import EVENT_KEYS, EVENT_TYPES
from app.notifications.models import (
    SMTP_SETTINGS_ID,
    Notification,
    NotificationSubscription,
    SMTPSettings,
)
from app.notifications.schemas import (
    EmailSettingsResponse,
    EmailSettingsUpdate,
    EmailTestRequest,
    EmailTestResponse,
    EventTypeResponse,
    NotificationResponse,
    SubscriptionsBody,
)
from app.notifications.service import (
    is_usable,
    load_smtp,
    public_url,
    smtp_config,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _response(db: AsyncSession, row: SMTPSettings | None) -> EmailSettingsResponse:
    url = await public_url(db)
    if row is None:
        return EmailSettingsResponse(
            enabled=False,
            host="",
            port=587,
            tls_mode="starttls",
            username=None,
            password_set=False,
            from_address="",
            updated_at=None,
            public_url=url,
            ready=False,
        )
    return EmailSettingsResponse(
        enabled=row.enabled,
        host=row.host,
        port=row.port,
        tls_mode=row.tls_mode,  # type: ignore[arg-type]
        username=row.username,
        password_set=row.encrypted_password is not None,
        from_address=row.from_address,
        updated_at=row.updated_at,
        public_url=url,
        ready=is_usable(row) and bool(url),
    )


@router.get("/email", response_model=EmailSettingsResponse)
async def get_email_settings(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    return await _response(db, await load_smtp(db))


@router.put("/email", response_model=EmailSettingsResponse)
async def update_email_settings(
    body: EmailSettingsUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    from app.crypto import encrypt_ssh_key, get_master_key

    row = await load_smtp(db)
    if row is None:
        row = SMTPSettings(id=SMTP_SETTINGS_ID)
        db.add(row)

    row.enabled = body.enabled
    row.host = body.host.strip()
    row.port = body.port
    row.tls_mode = body.tls_mode
    row.username = (body.username or "").strip() or None
    row.from_address = body.from_address
    row.updated_by_user_id = user.id

    password_changed = False
    if body.clear_password:
        row.encrypted_password = None
        password_changed = True
    elif body.password:
        row.encrypted_password = encrypt_ssh_key(body.password, get_master_key())
        password_changed = True

    await log_action(
        db,
        action="email_settings_updated",
        entity_type="smtp_settings",
        entity_id=SMTP_SETTINGS_ID,
        user_id=user.id,
        after_state={
            "enabled": row.enabled,
            "host": row.host,
            "port": row.port,
            "tls_mode": row.tls_mode,
            "username": row.username,
            "from_address": row.from_address,
            # Whether, never what.
            "password_changed": password_changed,
        },
    )
    await db.commit()
    await db.refresh(row)
    return await _response(db, row)


@router.post("/email/test", response_model=EmailTestResponse)
async def send_test_email(
    body: EmailTestRequest,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Send one email with the settings in the form, saved or not.

    Inline, unlike every real notification: the point is to show the
    operator what the server says, and that has to come back in this
    response. A blank password uses the stored one, so editing the host
    of a working setup does not mean retyping it.
    """
    stored = await load_smtp(db)
    draft = SMTPSettings(
        id=SMTP_SETTINGS_ID,
        enabled=True,
        host=body.host.strip(),
        port=body.port,
        tls_mode=body.tls_mode,
        username=(body.username or "").strip() or None,
        from_address=body.from_address,
        encrypted_password=None
        if body.clear_password or stored is None
        else stored.encrypted_password,
    )
    try:
        config = smtp_config(draft, password=body.password or None)
    except Exception:
        return EmailTestResponse(
            success=False,
            message="The stored password could not be decrypted. Enter it again.",
        )

    url = await public_url(db)
    to = body.to or user.email
    message = smtp.OutgoingEmail(
        to=to,
        subject="[LabDog] Test email",
        body=(
            f"This is a test from LabDog, sent by {user.email}.\n\n"
            + (
                f"Links in notifications will point at {url}"
                if url
                else "notifications.public_url is not set, so real notifications will not "
                "be sent yet: every one links back to LabDog."
            )
        ),
    )
    try:
        [error] = await asyncio.to_thread(smtp.send, config, [message])
    except smtp.SMTPSendError as exc:
        return EmailTestResponse(success=False, message=str(exc))
    if error:
        return EmailTestResponse(success=False, message=error)
    note = "" if url else " Set LabDog's address below before relying on it."
    return EmailTestResponse(success=True, message=f"Sent to {to}.{note}")


@router.get("/events", response_model=list[EventTypeResponse])
async def list_event_types(_: User = Depends(current_active_user)):
    return [
        EventTypeResponse(key=e.key, label=e.label, description=e.description) for e in EVENT_TYPES
    ]


@router.get("/subscriptions", response_model=SubscriptionsBody)
async def get_my_subscriptions(
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(NotificationSubscription.event_type).where(
            NotificationSubscription.user_id == user.id,
            NotificationSubscription.channel == "email",
        )
    )
    return SubscriptionsBody(event_types=sorted(rows.scalars().all()))


@router.put("/subscriptions", response_model=SubscriptionsBody)
async def set_my_subscriptions(
    body: SubscriptionsBody,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace the signed-in user's email subscriptions.

    Only one's own. The privilege model is flat, so anyone may subscribe
    to anything — but signing someone else up for mail is not the same
    thing as being allowed to see what the mail says.
    """
    unknown = sorted(set(body.event_types) - EVENT_KEYS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown event type(s): {', '.join(unknown)}")
    wanted = sorted(set(body.event_types))

    await db.execute(
        delete(NotificationSubscription).where(
            NotificationSubscription.user_id == user.id,
            NotificationSubscription.channel == "email",
        )
    )
    for key in wanted:
        db.add(NotificationSubscription(user_id=user.id, event_type=key, channel="email"))
    await db.commit()
    return SubscriptionsBody(event_types=wanted)


@router.get("/deliveries", response_model=list[NotificationResponse])
async def list_deliveries(
    limit: int = 50,
    status: str | None = None,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Recent notifications, newest first: what was sent, to whom, and
    what went wrong. Bodies are left out — this answers "why didn't I get
    an email?", and the subject is enough to tell which one it was."""
    stmt = select(Notification).order_by(Notification.id.desc()).limit(max(1, min(limit, 200)))
    if status in ("pending", "sent", "failed"):
        stmt = stmt.where(Notification.status == status)
    return list((await db.execute(stmt)).scalars().all())
