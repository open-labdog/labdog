"""Steps shared by the group- and host-scoped rule CRUD endpoints.

Services, Linux users and groups, cron jobs, hosts entries, packages,
package repositories, CA certificates and firewall rules each expose the
same create, update and delete endpoints twice: once on a group, once on a
host. These are the steps they all repeated, each kept as the endpoints
wrote it: the scope lookups and their 404s, the scoped row lookup, the
flush that turns a unique-constraint violation into a 409, and the
audit-and-commit tail of a create, an update and a delete.

What differs between modules stays in the endpoint: the GitOps lock, how a
row is built and validated, and which fields an update reads from the body.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.user import User


@dataclass(frozen=True)
class RuleAudit:
    """How one kind of rule appears in the audit log."""

    entity_type: str
    #: The row's audited fields, logged as the before and after state.
    state: Callable[[Any], dict]
    #: The before state of a delete, when it differs from ``state``.
    deleted_state: Callable[[Any], dict] | None = None


async def require_group(db: AsyncSession, group_id: int) -> HostGroup:
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


async def require_host(db: AsyncSession, host_id: int) -> Host:
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    return host


async def scoped_row_or_404[RowT](
    db: AsyncSession,
    model: type[RowT],
    row_id: int,
    scope: ColumnElement[bool],
    *,
    detail: str,
) -> RowT:
    """The ``model`` row ``row_id``, if ``scope`` (its group or host) matches.

    A row that exists under another group or host is a 404 like a missing
    one, and so is a row on a host that does not exist: the host is never
    looked up on its own.
    """
    result = await db.execute(select(model).where(model.id == row_id, scope))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=detail)
    return row


async def flush_or_409(db: AsyncSession, conflict_detail: Callable[[], str]) -> None:
    """Flush; a constraint violation rolls the session back and becomes a 409.

    The detail is built after the rollback, as the endpoints always built
    it. An update that names the row's own attributes there reads expired
    attributes (BUG-143).
    """
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=conflict_detail())


def apply_fields(row: object, values: dict[str, Any]) -> None:
    for field, value in values.items():
        setattr(row, field, value)


async def commit_created[RowT](db: AsyncSession, row: RowT, audit: RuleAudit, user: User) -> RowT:
    """Audit a flushed new row, commit, and return it refreshed."""
    await log_action(
        db=db,
        action="create",
        entity_type=audit.entity_type,
        entity_id=row.id,
        user_id=user.id,
        after_state=audit.state(row),
    )
    await db.commit()
    await db.refresh(row)
    return row


async def commit_updated[RowT](
    db: AsyncSession, row: RowT, audit: RuleAudit, user: User, before_state: dict
) -> RowT:
    """Audit a flushed change to a row, commit, and return it refreshed."""
    await log_action(
        db=db,
        action="update",
        entity_type=audit.entity_type,
        entity_id=row.id,
        user_id=user.id,
        before_state=before_state,
        after_state=audit.state(row),
    )
    await db.commit()
    await db.refresh(row)
    return row


async def delete_and_commit(db: AsyncSession, row: Any, audit: RuleAudit, user: User) -> None:
    """Delete a row, audit it under the id it had, and commit."""
    before = (audit.deleted_state or audit.state)(row)
    row_id_for_log = row.id

    await db.delete(row)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type=audit.entity_type,
        entity_id=row_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()
