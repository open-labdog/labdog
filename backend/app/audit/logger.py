from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.user import User


async def log_action(
    db: AsyncSession,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    user_id: int | None = None,
    before_state: dict | None = None,
    after_state: dict | None = None,
    ip_address: str | None = None,
) -> None:
    """Create an audit log entry. Append-only — never update or delete."""
    # A background task logs for whoever started it, minutes later. If that
    # account was deleted meanwhile the row would break the foreign key and
    # roll back the caller's whole transaction -- a sync finalising that way
    # stays "running" until the sweeper fails it.
    if user_id is not None and await db.get(User, user_id) is None:
        user_id = None
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=before_state,
        after_state=after_state,
        ip_address=ip_address,
        created_at=datetime.now(UTC),
    )
    db.add(entry)
    # Don't commit here — let the caller's transaction handle it
