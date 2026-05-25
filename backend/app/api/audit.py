from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.db import get_db
from app.models.audit_log import AuditLog
from app.models.ssh_session_transcript import SSHSessionTranscript
from app.models.user import User

router = APIRouter(prefix="/audit-log", tags=["audit"])


class AuditLogResponse(BaseModel):
    id: int
    user_id: int | None
    user_email: str | None  # joined from `users` for display
    action: str
    entity_type: str
    entity_id: int | None
    before_state: dict | None
    after_state: dict | None
    ip_address: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class TranscriptRowResponse(BaseModel):
    id: int
    session_id: str
    host_id: int | None
    user_id: int | None
    command_text: str
    recorded_at: datetime
    model_config = {"from_attributes": True}


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_logs(
    entity_type: str | None = None,
    entity_id: int | None = None,
    action: str | None = None,
    user_id: int | None = None,
    limit: int = Query(default=50, le=200),
    cursor: int | None = None,  # cursor-based: pass last seen id
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List audit log entries. Cursor-based pagination (pass id of last seen entry).

    Each row carries a `user_email` joined from the `users` table so the UI
    can render "test@test.se" instead of the integer `user_id`. The original
    `user_id` is preserved (still useful for filtering / linking to the user
    detail page); `user_email` is `None` for system-driven events
    (`triggered_by=None`) or when the user has been deleted.
    """
    q = (
        select(AuditLog, User.email)
        .outerjoin(User, AuditLog.user_id == User.id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    )
    if cursor:
        q = q.where(AuditLog.id < cursor)
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    if action:
        q = q.where(AuditLog.action == action)
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
    result = await db.execute(q)
    rows = result.all()
    return [
        AuditLogResponse(
            id=entry.id,
            user_id=entry.user_id,
            user_email=email,
            action=entry.action,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            before_state=entry.before_state,
            after_state=entry.after_state,
            ip_address=entry.ip_address,
            created_at=entry.created_at,
        )
        for entry, email in rows
    ]


@router.get(
    "/ssh-sessions/{session_id}/transcript",
    response_model=list[TranscriptRowResponse],
    tags=["audit"],
)
async def get_ssh_session_transcript(
    session_id: str,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[TranscriptRowResponse]:
    """Return the ordered transcript rows for a single SSH session.

    Rows are returned in ``recorded_at`` ascending order (the order they were
    captured).  Returns 404 if no transcript rows exist for the given
    ``session_id`` (the session never had keystrokes, or the session ID is
    unknown).

    Accessible to any authenticated user (audit-log posture: readable by
    ``current_active_user``).  Superuser-only restriction can be added later
    if access-control requirements tighten.
    """
    q = (
        select(SSHSessionTranscript)
        .where(SSHSessionTranscript.session_id == session_id)
        .order_by(SSHSessionTranscript.recorded_at.asc())
    )
    result = await db.execute(q)
    rows = result.scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="No transcript found for this session")
    return [TranscriptRowResponse.model_validate(row) for row in rows]


# NOTE: No PUT, PATCH, or DELETE endpoints -- audit log is append-only
