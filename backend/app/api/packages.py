from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.api._gitops_lock import check_gitops_lock
from app.api._scoped_rules import (
    RuleAudit,
    apply_fields,
    commit_created,
    commit_updated,
    delete_and_commit,
    flush_or_409,
    require_group,
    require_host,
    scoped_row_or_404,
)
from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.db import get_db
from app.db_after_commit import run_after_commit
from app.models.host import Host
from app.models.user import User
from app.packages.merge import get_effective_packages, get_effective_repos
from app.packages.models import PackageRepository, PackageRule
from app.packages.schemas import (
    EffectivePackageResponse,
    PackageRepositoryCreate,
    PackageRepositoryResponse,
    PackageRepositoryUpdate,
    PackageRuleCreate,
    PackageRuleResponse,
    PackageRuleUpdate,
)

router = APIRouter(tags=["packages"])

_PACKAGE_NOT_FOUND = "Package rule not found"
_REPO_NOT_FOUND = "Package repository not found"


def _package_state(rule: PackageRule) -> dict:
    return {
        "package_name": rule.package_name,
        "state": str(rule.state),
        "version": rule.version,
    }


def _repo_state(repo: PackageRepository) -> dict:
    return {"name": repo.name, "url": repo.url, "state": str(repo.state)}


_PACKAGE_AUDIT = RuleAudit("package_rule", _package_state)
_REPO_AUDIT = RuleAudit("package_repo", _repo_state)


# ---------------------------------------------------------------------------
# Group-level package CRUD
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/packages", response_model=list[PackageRuleResponse])
async def list_group_packages(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_group(db, group_id)

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
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    await require_group(db, group_id)

    rule = PackageRule(group_id=group_id, **body.model_dump())
    db.add(rule)
    await flush_or_409(db, lambda: f"Package '{body.package_name}' already exists in this group")
    return await commit_created(db, rule, _PACKAGE_AUDIT, user)


@router.put(
    "/groups/{group_id}/packages/{rule_id}",
    response_model=PackageRuleResponse,
)
async def update_group_package(
    group_id: int,
    rule_id: int,
    body: PackageRuleUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, PackageRule, rule_id, PackageRule.group_id == group_id, detail=_PACKAGE_NOT_FOUND
    )
    before = _package_state(rule)
    apply_fields(rule, body.model_dump(exclude_unset=True))
    await flush_or_409(db, lambda: f"Package '{rule.package_name}' already exists in this group")
    return await commit_updated(db, rule, _PACKAGE_AUDIT, user, before)


@router.delete("/groups/{group_id}/packages/{rule_id}")
async def delete_group_package(
    group_id: int,
    rule_id: int,
    uninstall: bool = False,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, PackageRule, rule_id, PackageRule.group_id == group_id, detail=_PACKAGE_NOT_FOUND
    )
    if not uninstall:
        await delete_and_commit(db, rule, _PACKAGE_AUDIT, user)
        return Response(status_code=204)

    before = _package_state(rule)

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
        entity_type=_PACKAGE_AUDIT.entity_type,
        entity_id=rule.id,
        user_id=user.id,
        before_state=before,
        after_state=_package_state(rule),
    )
    await db.flush()

    memberships = await db.execute(
        select(HostGroupMembership.c.host_id).where(HostGroupMembership.c.group_id == group_id)
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

        host_result = await db.execute(select(Host).where(Host.id == hid))
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
        # BUG-137: dispatch after the commit below, or the worker may
        # start before the row exists.
        run_after_commit(
            db,
            lambda job_id=job.id, host_id=hid: run_package_sync.delay(
                job_id=job_id, host_id=host_id
            ),
        )
        sync_jobs += 1

    await db.commit()
    return {"sync_jobs": sync_jobs}


# ---------------------------------------------------------------------------
# Host-level package overrides
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/packages", response_model=list[PackageRuleResponse])
async def list_host_packages(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

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
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    rule = PackageRule(host_id=host_id, **body.model_dump())
    db.add(rule)
    await flush_or_409(db, lambda: f"Package '{body.package_name}' already exists on this host")
    return await commit_created(db, rule, _PACKAGE_AUDIT, user)


@router.put(
    "/hosts/{host_id}/packages/{rule_id}",
    response_model=PackageRuleResponse,
)
async def update_host_package(
    host_id: int,
    rule_id: int,
    body: PackageRuleUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, PackageRule, rule_id, PackageRule.host_id == host_id, detail=_PACKAGE_NOT_FOUND
    )
    before = _package_state(rule)
    apply_fields(rule, body.model_dump(exclude_unset=True))
    await flush_or_409(db, lambda: f"Package '{rule.package_name}' already exists on this host")
    return await commit_updated(db, rule, _PACKAGE_AUDIT, user, before)


@router.delete("/hosts/{host_id}/packages/{rule_id}", status_code=204)
async def delete_host_package(
    host_id: int,
    rule_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, PackageRule, rule_id, PackageRule.host_id == host_id, detail=_PACKAGE_NOT_FOUND
    )
    await delete_and_commit(db, rule, _PACKAGE_AUDIT, user)
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
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    return await get_effective_packages(host_id, db)


@router.get(
    "/hosts/{host_id}/effective-repos",
    response_model=list[PackageRepositoryResponse],
)
async def effective_repos(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

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
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_group(db, group_id)

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
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    await require_group(db, group_id)

    repo = PackageRepository(group_id=group_id, **body.model_dump())
    db.add(repo)
    await flush_or_409(db, lambda: f"Repository '{body.name}' already exists in this group")
    return await commit_created(db, repo, _REPO_AUDIT, user)


@router.put(
    "/groups/{group_id}/package-repos/{repo_id}",
    response_model=PackageRepositoryResponse,
)
async def update_group_repo(
    group_id: int,
    repo_id: int,
    body: PackageRepositoryUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    repo = await scoped_row_or_404(
        db,
        PackageRepository,
        repo_id,
        PackageRepository.group_id == group_id,
        detail=_REPO_NOT_FOUND,
    )
    before = _repo_state(repo)
    apply_fields(repo, body.model_dump(exclude_unset=True))
    await flush_or_409(db, lambda: f"Repository '{repo.name}' already exists in this group")
    return await commit_updated(db, repo, _REPO_AUDIT, user, before)


@router.delete("/groups/{group_id}/package-repos/{repo_id}", status_code=204)
async def delete_group_repo(
    group_id: int,
    repo_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    repo = await scoped_row_or_404(
        db,
        PackageRepository,
        repo_id,
        PackageRepository.group_id == group_id,
        detail=_REPO_NOT_FOUND,
    )
    await delete_and_commit(db, repo, _REPO_AUDIT, user)
    return Response(status_code=204)
