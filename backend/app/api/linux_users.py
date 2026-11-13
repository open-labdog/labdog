from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._gitops_lock import check_gitops_lock
from app.api._scoped_rules import (
    RuleAudit,
    apply_fields,
    commit_created,
    commit_updated,
    delete_and_commit,
    require_group,
    require_host,
    scoped_row_or_404,
)
from app.auth.permissions import require
from app.db import get_db
from app.models.user import User
from app.user_mgmt.merge import get_effective_users
from app.user_mgmt.models import LinuxUser
from app.user_mgmt.schemas import (
    EffectiveLinuxUserResponse,
    LinuxUserCreate,
    LinuxUserResponse,
    LinuxUserUpdate,
)

router = APIRouter(tags=["linux-users"])

_NOT_FOUND = "Linux user rule not found"


def _audit_state(rule: LinuxUser) -> dict:
    return {"username": rule.username, "state": str(rule.state)}


_AUDIT = RuleAudit("linux_user", _audit_state)


# ---------------------------------------------------------------------------
# Group-level CRUD
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/linux-users", response_model=list[LinuxUserResponse])
async def list_group_linux_users(
    group_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_group(db, group_id)

    result = await db.execute(
        select(LinuxUser)
        .where(LinuxUser.group_id == group_id)
        .order_by(LinuxUser.priority.desc(), LinuxUser.id)
    )
    return result.scalars().all()


@router.post(
    "/groups/{group_id}/linux-users",
    response_model=LinuxUserResponse,
    status_code=201,
)
async def create_group_linux_user(
    group_id: int,
    body: LinuxUserCreate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    await require_group(db, group_id)

    rule = LinuxUser(group_id=group_id, **body.model_dump())
    db.add(rule)
    await db.flush()
    return await commit_created(db, rule, _AUDIT, user)


@router.put(
    "/groups/{group_id}/linux-users/{rule_id}",
    response_model=LinuxUserResponse,
)
async def update_group_linux_user(
    group_id: int,
    rule_id: int,
    body: LinuxUserUpdate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, LinuxUser, rule_id, LinuxUser.group_id == group_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    apply_fields(rule, body.model_dump(exclude_unset=True))
    await db.flush()
    return await commit_updated(db, rule, _AUDIT, user, before)


@router.delete("/groups/{group_id}/linux-users/{rule_id}", status_code=204)
async def delete_group_linux_user(
    group_id: int,
    rule_id: int,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, LinuxUser, rule_id, LinuxUser.group_id == group_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)


# ---------------------------------------------------------------------------
# Host-level overrides
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/linux-users", response_model=list[LinuxUserResponse])
async def list_host_linux_users(
    host_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    result = await db.execute(
        select(LinuxUser)
        .where(LinuxUser.host_id == host_id)
        .order_by(LinuxUser.priority.desc(), LinuxUser.id)
    )
    return result.scalars().all()


@router.post(
    "/hosts/{host_id}/linux-users",
    response_model=LinuxUserResponse,
    status_code=201,
)
async def create_host_linux_user(
    host_id: int,
    body: LinuxUserCreate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    rule = LinuxUser(host_id=host_id, **body.model_dump())
    db.add(rule)
    await db.flush()
    return await commit_created(db, rule, _AUDIT, user)


@router.put(
    "/hosts/{host_id}/linux-users/{rule_id}",
    response_model=LinuxUserResponse,
)
async def update_host_linux_user(
    host_id: int,
    rule_id: int,
    body: LinuxUserUpdate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, LinuxUser, rule_id, LinuxUser.host_id == host_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    apply_fields(rule, body.model_dump(exclude_unset=True))
    await db.flush()
    return await commit_updated(db, rule, _AUDIT, user, before)


@router.delete("/hosts/{host_id}/linux-users/{rule_id}", status_code=204)
async def delete_host_linux_user(
    host_id: int,
    rule_id: int,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, LinuxUser, rule_id, LinuxUser.host_id == host_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)


# ---------------------------------------------------------------------------
# Effective config (merged group + host overrides)
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-users",
    response_model=list[EffectiveLinuxUserResponse],
)
async def effective_users(
    host_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    return await get_effective_users(host_id, db)
