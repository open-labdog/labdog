from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from app.db import get_db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.auth.users import current_active_user

router = APIRouter(prefix="/audit-log", tags=["audit"])


class AuditLogResponse(BaseModel):
    id: int
    user_id: int | None
    action: str
    entity_type: str
    entity_id: int | None
    before_state: dict | None
    after_state: dict | None
    ip_address: str | None
    created_at: datetime
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
    """List audit log entries. Cursor-based pagination (pass id of last seen entry)."""
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
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
    return result.scalars().all()


# NOTE: No PUT, PATCH, or DELETE endpoints — audit log is append-only
