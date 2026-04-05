from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.db import get_db
from app.auth.users import current_superuser
from app.models.user import User
from app.models.host_group import HostGroup
from app.models.host import Host
from app.packages.models import PackageRule, PackageRepository
from app.packages.schemas import (
    PackageRuleCreate,
    PackageRuleUpdate,
    PackageRuleResponse,
    EffectivePackageResponse,
    PackageRepositoryCreate,
    PackageRepositoryUpdate,
    PackageRepositoryResponse,
)
from app.packages.merge import get_effective_packages, get_effective_repos
from app.audit.logger import log_action

router = APIRouter(tags=["packages"])


# ---------------------------------------------------------------------------
# Group-level package CRUD
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/packages", response_model=list[PackageRuleResponse])
async def list_group_packages(
    group_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    result = await db.execute(
        select(PackageRule)
        .where(PackageRule.group_id == group_id)
        .order_by(PackageRule.priority.desc(), PackageRule.id)
    )
    return result.scalars().all()


@router.post(
    "/groups/{group_id}/packages",
    response_model=PackageRuleResponse,
    status_code=201,
)
async def create_group_package(
    group_id: int,
    body: PackageRuleCreate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    rule = PackageRule(group_id=group_id, **body.model_dump())
    db.add(rule)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Package '{body.package_name}' already exists in this group",
        )

    await log_action(
        db=db,
        action="create",
        entity_type="package_rule",
        entity_id=rule.id,
        user_id=user.id,
        after_state={
            "package_name": rule.package_name,
            "state": str(rule.state),
            "version": rule.version,
        },
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put(
    "/groups/{group_id}/packages/{rule_id}",
    response_model=PackageRuleResponse,
)
async def update_group_package(
    group_id: int,
    rule_id: int,
    body: PackageRuleUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PackageRule).where(
            PackageRule.id == rule_id,
            PackageRule.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Package rule not found")

    before = {
        "package_name": rule.package_name,
        "state": str(rule.state),
        "version": rule.version,
    }

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Package '{rule.package_name}' already exists in this group",
        )

    await log_action(
        db=db,
        action="update",
        entity_type="package_rule",
        entity_id=rule.id,
        user_id=user.id,
        before_state=before,
        after_state={
            "package_name": rule.package_name,
            "state": str(rule.state),
            "version": rule.version,
        },
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/groups/{group_id}/packages/{rule_id}")
async def delete_group_package(
    group_id: int,
    rule_id: int,
    uninstall: bool = False,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PackageRule).where(
            PackageRule.id == rule_id,
            PackageRule.group_id == group_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Package rule not found")

    before = {
        "package_name": rule.package_name,
        "state": str(rule.state),
        "version": rule.version,
    }

    if uninstall:
        # Change state to "absent" so the normal sync pipeline removes the
        # package.  The rule stays in the DB — drift detection will flag any
        # host that still has the package installed, and the user can retry
        # the sync if it fails.
        from app.models.host import HostGroupMembership
        from app.models.sync_job import SyncJob
        from app.packages.models import PackageState
        from app.tasks.package_sync import run_package_sync

        rule.state = PackageState.absent

        await log_action(
            db=db,
            action="update",
            entity_type="package_rule",
            entity_id=rule.id,
            user_id=user.id,
            before_state=before,
            after_state={
                "package_name": rule.package_name,
                "state": str(rule.state),
                "version": rule.version,
            },
        )
        await db.flush()

        memberships = await db.execute(
            select(HostGroupMembership.c.host_id).where(
                HostGroupMembership.c.group_id == group_id
            )
        )
        host_ids = [r[0] for r in memberships.all()]

        sync_jobs = 0
        for hid in host_ids:
            # Skip hosts that already have a pending/running package sync
            running = await db.execute(
                select(SyncJob).where(
                    SyncJob.host_id == hid,
                    SyncJob.module_type == "package",
                    SyncJob.status.in_(["pending", "running"]),
                )
            )
            if running.scalar_one_or_none():
                continue

            host_result = await db.execute(
                select(Host).where(Host.id == hid)
            )
            host = host_result.scalar_one_or_none()
            if not host or not host.ssh_key_id:
                continue

            job = SyncJob(
                host_id=hid,
                group_id=group_id,
                module_type="package",
                status="pending",
                triggered_by_user_id=user.id,
            )
            db.add(job)
            await db.flush()
            run_package_sync.delay(job_id=job.id, host_id=hid)
            sync_jobs += 1

        await db.commit()
        return {"sync_jobs": sync_jobs}
    else:
        await db.delete(rule)
        await db.flush()

        await log_action(
            db=db,
            action="delete",
            entity_type="package_rule",
            entity_id=rule.id,
            user_id=user.id,
            before_state=before,
        )
        await db.commit()
        return Response(status_code=204)


# ---------------------------------------------------------------------------
# Host-level package overrides
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/packages", response_model=list[PackageRuleResponse])
async def list_host_packages(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    result = await db.execute(
        select(PackageRule)
        .where(PackageRule.host_id == host_id)
        .order_by(PackageRule.priority.desc(), PackageRule.id)
    )
    return result.scalars().all()


@router.post(
    "/hosts/{host_id}/packages",
    response_model=PackageRuleResponse,
    status_code=201,
)
async def create_host_package(
    host_id: int,
    body: PackageRuleCreate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    rule = PackageRule(host_id=host_id, **body.model_dump())
    db.add(rule)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Package '{body.package_name}' already exists on this host",
        )

    await log_action(
        db=db,
        action="create",
        entity_type="package_rule",
        entity_id=rule.id,
        user_id=user.id,
        after_state={
            "package_name": rule.package_name,
            "state": str(rule.state),
            "version": rule.version,
        },
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put(
    "/hosts/{host_id}/packages/{rule_id}",
    response_model=PackageRuleResponse,
)
async def update_host_package(
    host_id: int,
    rule_id: int,
    body: PackageRuleUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PackageRule).where(
            PackageRule.id == rule_id,
            PackageRule.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Package rule not found")

    before = {
        "package_name": rule.package_name,
        "state": str(rule.state),
        "version": rule.version,
    }

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Package '{rule.package_name}' already exists on this host",
        )

    await log_action(
        db=db,
        action="update",
        entity_type="package_rule",
        entity_id=rule.id,
        user_id=user.id,
        before_state=before,
        after_state={
            "package_name": rule.package_name,
            "state": str(rule.state),
            "version": rule.version,
        },
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/hosts/{host_id}/packages/{rule_id}", status_code=204)
async def delete_host_package(
    host_id: int,
    rule_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PackageRule).where(
            PackageRule.id == rule_id,
            PackageRule.host_id == host_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Package rule not found")

    before = {
        "package_name": rule.package_name,
        "state": str(rule.state),
        "version": rule.version,
    }
    rule_id_for_log = rule.id

    await db.delete(rule)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="package_rule",
        entity_id=rule_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Effective config (merged group + host overrides)
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-packages",
    response_model=list[EffectivePackageResponse],
)
async def effective_packages(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    return await get_effective_packages(host_id, db)


@router.get(
    "/hosts/{host_id}/effective-repos",
    response_model=list[PackageRepositoryResponse],
)
async def effective_repos(
    host_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    return await get_effective_repos(host_id, db)


# ---------------------------------------------------------------------------
# Group-level repository CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/groups/{group_id}/package-repos",
    response_model=list[PackageRepositoryResponse],
)
async def list_group_repos(
    group_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    result = await db.execute(
        select(PackageRepository)
        .where(PackageRepository.group_id == group_id)
        .order_by(PackageRepository.name)
    )
    return result.scalars().all()


@router.post(
    "/groups/{group_id}/package-repos",
    response_model=PackageRepositoryResponse,
    status_code=201,
)
async def create_group_repo(
    group_id: int,
    body: PackageRepositoryCreate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    repo = PackageRepository(group_id=group_id, **body.model_dump())
    db.add(repo)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Repository '{body.name}' already exists in this group",
        )

    await log_action(
        db=db,
        action="create",
        entity_type="package_repo",
        entity_id=repo.id,
        user_id=user.id,
        after_state={"name": repo.name, "url": repo.url, "state": str(repo.state)},
    )
    await db.commit()
    await db.refresh(repo)
    return repo


@router.put(
    "/groups/{group_id}/package-repos/{repo_id}",
    response_model=PackageRepositoryResponse,
)
async def update_group_repo(
    group_id: int,
    repo_id: int,
    body: PackageRepositoryUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PackageRepository).where(
            PackageRepository.id == repo_id,
            PackageRepository.group_id == group_id,
        )
    )
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Package repository not found")

    before = {"name": repo.name, "url": repo.url, "state": str(repo.state)}

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(repo, field, value)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Repository '{repo.name}' already exists in this group",
        )

    await log_action(
        db=db,
        action="update",
        entity_type="package_repo",
        entity_id=repo.id,
        user_id=user.id,
        before_state=before,
        after_state={"name": repo.name, "url": repo.url, "state": str(repo.state)},
    )
    await db.commit()
    await db.refresh(repo)
    return repo


@router.delete("/groups/{group_id}/package-repos/{repo_id}", status_code=204)
async def delete_group_repo(
    group_id: int,
    repo_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PackageRepository).where(
            PackageRepository.id == repo_id,
            PackageRepository.group_id == group_id,
        )
    )
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Package repository not found")

    before = {"name": repo.name, "url": repo.url, "state": str(repo.state)}
    repo_id_for_log = repo.id

    await db.delete(repo)
    await db.flush()

    await log_action(
        db=db,
        action="delete",
        entity_type="package_repo",
        entity_id=repo_id_for_log,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()
    return Response(status_code=204)
