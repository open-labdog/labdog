from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user, current_superuser
from app.ca_certs.actions import auto_enqueue_for_new_membership
from app.db import get_db
from app.models.git_repository import GitOpsStatus, GitRepository
from app.models.host import Host, HostGroupMembership
from app.models.host_group import HostGroup
from app.models.user import User
from app.schemas.git_repos import GitOpsEnableRequest, GitOpsStatusResponse
from app.schemas.groups import GroupCreate, GroupResponse, GroupUpdate
from app.schemas.hosts import HostResponse


class BulkAddHostsRequest(BaseModel):
    host_ids: list[int]


MembershipRole = Literal["control_plane", "worker"]


class MembershipResponse(BaseModel):
    """One ``host_group_memberships`` row, used to surface per-member
    metadata (currently just ``role``) without forcing every existing
    HostResponse caller to grow new fields."""

    model_config = ConfigDict(from_attributes=True)

    host_id: int
    role: MembershipRole | None = None


class MembershipRoleUpdate(BaseModel):
    """Body for ``PUT /groups/{id}/hosts/{host_id}/role``. ``role=None``
    clears the assignment (the membership stays, the role goes back to
    NULL — the same shape as a fresh add)."""

    model_config = ConfigDict(extra="forbid")

    role: MembershipRole | None = None


router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("", response_model=list[GroupResponse])
async def list_groups(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).order_by(HostGroup.priority.desc()))
    return result.scalars().all()


@router.post("", response_model=GroupResponse, status_code=201)
async def create_group(
    body: GroupCreate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    # Check unique name
    existing = await db.execute(select(HostGroup).where(HostGroup.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Group name already exists")
    # Check unique priority
    existing_p = await db.execute(select(HostGroup).where(HostGroup.priority == body.priority))
    if existing_p.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Group priority already in use")
    group = HostGroup(**body.model_dump())
    db.add(group)
    await db.flush()
    await log_action(
        db=db,
        action="create",
        entity_type="host_group",
        entity_id=group.id,
        user_id=user.id,
        after_state={
            "name": group.name,
            "priority": group.priority,
            "category": group.category,
            "description": group.description,
        },
    )
    await db.commit()
    await db.refresh(group)
    return group


@router.get("/summary")
async def list_groups_summary(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all groups with per-module rule counts and host counts.

    Single endpoint for the groups list page — avoids N+1 queries.
    """
    from app.ca_certs.models import CACertRule
    from app.cron.models import CronJob
    from app.hosts_mgmt.models import HostsEntry
    from app.models.firewall_rule import FirewallRule
    from app.packages.models import PackageRule
    from app.resolver.models import ResolverConfig
    from app.services.models import ServiceRule
    from app.user_mgmt.models import LinuxGroup, LinuxUser

    groups_result = await db.execute(select(HostGroup).order_by(HostGroup.priority.desc()))
    groups = groups_result.scalars().all()
    group_ids = [g.id for g in groups]
    if not group_ids:
        return []

    async def _counts(model, col_name="group_id"):
        col = getattr(model, col_name)
        rows = await db.execute(select(col, func.count()).where(col.in_(group_ids)).group_by(col))
        return {r[0]: r[1] for r in rows}

    fw = await _counts(FirewallRule)
    he = await _counts(HostsEntry)
    svc = await _counts(ServiceRule)
    lu = await _counts(LinuxUser)
    lg = await _counts(LinuxGroup)
    cj = await _counts(CronJob)
    pkg = await _counts(PackageRule)
    res = await _counts(ResolverConfig)
    ca = await _counts(CACertRule)

    host_rows = await db.execute(
        select(HostGroupMembership.c.group_id, func.count())
        .where(HostGroupMembership.c.group_id.in_(group_ids))
        .group_by(HostGroupMembership.c.group_id)
    )
    host_counts = {r[0]: r[1] for r in host_rows}

    # Groups that share at least one host with another group
    shared_hosts = await db.execute(
        select(HostGroupMembership.c.group_id)
        .where(
            HostGroupMembership.c.host_id.in_(
                select(HostGroupMembership.c.host_id)
                .group_by(HostGroupMembership.c.host_id)
                .having(func.count(HostGroupMembership.c.group_id) > 1)
            )
        )
        .distinct()
    )
    conflict_group_ids = {r[0] for r in shared_hosts}

    result = []
    for g in groups:
        gid = g.id
        result.append(
            {
                "id": gid,
                "name": g.name,
                "description": g.description,
                "category": g.category,
                "priority": g.priority,
                "gitops_enabled": g.gitops_enabled,
                "gitops_status": g.gitops_status.value if g.gitops_status else None,
                "created_at": g.created_at.isoformat() if g.created_at else None,
                "updated_at": g.updated_at.isoformat() if g.updated_at else None,
                "host_count": host_counts.get(gid, 0),
                "has_shared_hosts": gid in conflict_group_ids,
                "module_counts": {
                    "firewall": fw.get(gid, 0),
                    "hosts_file": he.get(gid, 0),
                    "services": svc.get(gid, 0),
                    "users": lu.get(gid, 0) + lg.get(gid, 0),
                    "cron": cj.get(gid, 0),
                    "packages": pkg.get(gid, 0),
                    "resolver": res.get(gid, 0),
                    "ca_certs": ca.get(gid, 0),
                },
            }
        )
    return result


@router.get("/{group_id}", response_model=GroupResponse)
async def get_group(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@router.put("/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: int,
    body: GroupUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    # Check unique priority (skip if unchanged)
    if body.priority is not None and body.priority != group.priority:
        existing_p = await db.execute(
            select(HostGroup).where(HostGroup.priority == body.priority, HostGroup.id != group_id)
        )
        if existing_p.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Group priority already in use")
    # Check unique name (skip if unchanged)
    if body.name is not None and body.name != group.name:
        existing_n = await db.execute(
            select(HostGroup).where(HostGroup.name == body.name, HostGroup.id != group_id)
        )
        if existing_n.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Group name already exists")
    before_state = {
        "name": group.name,
        "priority": group.priority,
        "category": group.category,
        "description": group.description,
    }
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(group, field, value)
    await db.flush()
    await log_action(
        db=db,
        action="update",
        entity_type="host_group",
        entity_id=group.id,
        user_id=user.id,
        before_state=before_state,
        after_state={
            "name": group.name,
            "priority": group.priority,
            "category": group.category,
            "description": group.description,
        },
    )
    await db.commit()
    await db.refresh(group)
    return group


@router.delete("/{group_id}", status_code=204)
async def delete_group(
    group_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    from app.models.host import HostGroupMembership

    # Check no hosts assigned
    members = await db.execute(
        select(HostGroupMembership).where(HostGroupMembership.c.group_id == group_id)
    )
    if members.fetchone():
        raise HTTPException(status_code=400, detail="Cannot delete group with hosts assigned")
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    before_state = {
        "name": group.name,
        "priority": group.priority,
        "category": group.category,
        "description": group.description,
    }
    await db.delete(group)
    await db.flush()
    await log_action(
        db=db,
        action="delete",
        entity_type="host_group",
        entity_id=group_id,
        user_id=user.id,
        before_state=before_state,
    )
    await db.commit()


@router.get("/{group_id}/host-count")
async def get_group_host_count(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import func

    result = await db.execute(
        select(func.count())
        .select_from(HostGroupMembership)
        .where(HostGroupMembership.c.group_id == group_id)
    )
    return {"count": result.scalar()}


@router.get("/{group_id}/hosts", response_model=list[HostResponse])
async def list_group_hosts(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Group not found")

    hosts_result = await db.execute(
        select(Host).where(
            Host.id.in_(
                select(HostGroupMembership.c.host_id).where(
                    HostGroupMembership.c.group_id == group_id
                )
            )
        )
    )
    hosts = hosts_result.scalars().all()

    if hosts:
        host_ids = [h.id for h in hosts]
        memberships = await db.execute(
            select(HostGroupMembership.c.host_id, HostGroupMembership.c.group_id).where(
                HostGroupMembership.c.host_id.in_(host_ids)
            )
        )
        groups_by_host: dict[int, list[int]] = {}
        for host_id, gid in memberships.all():
            groups_by_host.setdefault(host_id, []).append(gid)
        for h in hosts:
            setattr(h, "group_ids", groups_by_host.get(h.id, []))

    return hosts


@router.get(
    "/{group_id}/memberships",
    response_model=list[MembershipResponse],
)
async def list_group_memberships(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return ``(host_id, role)`` pairs for every host in the group.

    Drives the role-picker UI and the action-run dialog's role-count
    preflight panel for cluster-mode actions. Per-host details still
    come from ``GET /groups/{id}/hosts``; this is the lightweight
    membership-only view.
    """
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Group not found")

    rows = await db.execute(
        select(HostGroupMembership.c.host_id, HostGroupMembership.c.role)
        .where(HostGroupMembership.c.group_id == group_id)
        .order_by(HostGroupMembership.c.host_id)
    )
    return [MembershipResponse(host_id=r.host_id, role=r.role) for r in rows.all()]


@router.put(
    "/{group_id}/hosts/{host_id}/role",
    response_model=MembershipResponse,
)
async def set_group_membership_role(
    group_id: int,
    host_id: int,
    body: MembershipRoleUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    """Set or clear the role on a single membership row.

    Superuser-only because role assignment is a precondition for
    cluster-mode actions (k8s-upgrade) and operators making bad role
    decisions can take down a Kubernetes cluster.
    """
    existing = (
        await db.execute(
            select(HostGroupMembership.c.host_id, HostGroupMembership.c.role).where(
                HostGroupMembership.c.host_id == host_id,
                HostGroupMembership.c.group_id == group_id,
            )
        )
    ).first()
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"host {host_id} is not a member of group {group_id}",
        )

    before_role = existing.role
    await db.execute(
        update(HostGroupMembership)
        .where(
            HostGroupMembership.c.host_id == host_id,
            HostGroupMembership.c.group_id == group_id,
        )
        .values(role=body.role)
    )
    await log_action(
        db=db,
        action="set_membership_role",
        entity_type="host_group",
        entity_id=group_id,
        user_id=user.id,
        before_state={"host_id": host_id, "role": before_role},
        after_state={"host_id": host_id, "role": body.role},
    )
    await db.commit()
    return MembershipResponse(host_id=host_id, role=body.role)


@router.post("/{group_id}/hosts", status_code=200)
async def add_hosts_to_group(
    group_id: int,
    body: BulkAddHostsRequest,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Add multiple hosts to this group (skips hosts already in the group)."""
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Group not found")

    # Find which hosts are already members
    existing = await db.execute(
        select(HostGroupMembership.c.host_id).where(
            HostGroupMembership.c.group_id == group_id,
            HostGroupMembership.c.host_id.in_(body.host_ids),
        )
    )
    already_member = {r[0] for r in existing.all()}
    to_add = [hid for hid in body.host_ids if hid not in already_member]

    if to_add:
        await db.execute(
            insert(HostGroupMembership),
            [{"host_id": hid, "group_id": group_id} for hid in to_add],
        )
        await db.flush()

        # Auto-enqueue CA cert deploy for newly-added hosts (no-op if the
        # group has no certs or the host has no SSH key).
        for hid in to_add:
            await auto_enqueue_for_new_membership(hid, group_id, db, triggered_by_user_id=user.id)

        await log_action(
            db=db,
            action="add_hosts",
            entity_type="host_group",
            entity_id=group_id,
            user_id=user.id,
            after_state={"added_host_ids": to_add},
        )
        await db.commit()

    return {"added": len(to_add), "already_member": len(already_member)}


@router.delete("/{group_id}/hosts", status_code=204)
async def remove_hosts_from_group(
    group_id: int,
    body: BulkAddHostsRequest,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove multiple hosts from this group."""
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Group not found")

    # Capture which host_ids actually had memberships so the audit
    # entry reflects what changed, not what the request asked for.
    actual_member_ids = (
        (
            await db.execute(
                select(HostGroupMembership.c.host_id).where(
                    HostGroupMembership.c.group_id == group_id,
                    HostGroupMembership.c.host_id.in_(body.host_ids),
                )
            )
        )
        .scalars()
        .all()
    )

    await db.execute(
        delete(HostGroupMembership).where(
            HostGroupMembership.c.group_id == group_id,
            HostGroupMembership.c.host_id.in_(body.host_ids),
        )
    )
    await db.flush()

    if actual_member_ids:
        await log_action(
            db=db,
            action="remove_hosts",
            entity_type="host_group",
            entity_id=group_id,
            user_id=user.id,
            before_state={"removed_host_ids": list(actual_member_ids)},
        )

    await db.commit()


@router.post("/{group_id}/gitops/enable", response_model=GitOpsStatusResponse)
async def enable_gitops(
    group_id: int,
    body: GitOpsEnableRequest,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.gitops_enabled:
        raise HTTPException(status_code=400, detail="GitOps already enabled for this group")

    repo_result = await db.execute(
        select(GitRepository).where(GitRepository.id == body.git_repository_id)
    )
    if not repo_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Git repository not found")

    group.gitops_enabled = True
    group.git_repository_id = body.git_repository_id
    group.gitops_file_path = body.file_path
    group.gitops_status = GitOpsStatus.disconnected
    group.gitops_error_message = None

    await db.commit()
    await db.refresh(group)

    return GitOpsStatusResponse(
        gitops_enabled=group.gitops_enabled,
        git_repository_id=group.git_repository_id,
        gitops_file_path=group.gitops_file_path,
        gitops_status=group.gitops_status.value,
        gitops_error_message=group.gitops_error_message,
        gitops_last_import_at=group.gitops_last_import_at,
    )


@router.post("/{group_id}/gitops/disable", response_model=GitOpsStatusResponse)
async def disable_gitops(
    group_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # Clear gitops fields — do NOT remove rules from DB
    group.gitops_enabled = False
    group.git_repository_id = None
    group.gitops_file_path = None
    group.gitops_status = GitOpsStatus.disconnected
    group.gitops_error_message = None

    await db.commit()
    await db.refresh(group)

    return GitOpsStatusResponse(
        gitops_enabled=group.gitops_enabled,
        git_repository_id=group.git_repository_id,
        gitops_file_path=group.gitops_file_path,
        gitops_status=group.gitops_status.value,
        gitops_error_message=group.gitops_error_message,
        gitops_last_import_at=group.gitops_last_import_at,
    )


@router.get("/{group_id}/gitops/status", response_model=GitOpsStatusResponse)
async def get_gitops_status(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(HostGroup).where(HostGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    return GitOpsStatusResponse(
        gitops_enabled=group.gitops_enabled,
        git_repository_id=group.git_repository_id,
        gitops_file_path=group.gitops_file_path,
        gitops_status=group.gitops_status.value,
        gitops_error_message=group.gitops_error_message,
        gitops_last_import_at=group.gitops_last_import_at,
    )
