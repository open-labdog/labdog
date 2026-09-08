from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.merge_utils import ordered_groups_for_host
from app.models.host import HostGroupMembership
from app.packages.models import PackageRepository, PackageRule
from app.packages.schemas import EffectivePackageResponse, PackageRepositoryResponse


async def get_effective_packages(host_id: int, db: AsyncSession) -> list[EffectivePackageResponse]:
    """
    Merge group-level package rules + host-level overrides into an effective list.

    Priority resolution:
    - Groups ordered by priority DESC (highest first), ties by group id ASC.
      First occurrence of a package_name wins among groups.
    - Host-level overrides replace group entries entirely (full record, not field merge).
    """

    memberships = await db.execute(ordered_groups_for_host(host_id))
    groups = memberships.all()

    merged: dict[str, EffectivePackageResponse] = {}

    for group_id, group_name, _priority in groups:
        result = await db.execute(
            select(PackageRule)
            .where(PackageRule.group_id == group_id)
            .order_by(PackageRule.priority.desc(), PackageRule.id.asc())
        )
        for rule in result.scalars().all():
            if rule.package_name not in merged:
                merged[rule.package_name] = EffectivePackageResponse(
                    package_name=rule.package_name,
                    version=rule.version,
                    state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
                    package_manager=rule.package_manager.value
                    if hasattr(rule.package_manager, "value")
                    else str(rule.package_manager),
                    priority=rule.priority,
                    hold=rule.hold if hasattr(rule, "hold") else False,
                    source="group",
                    source_id=group_id,
                    source_name=group_name,
                )

    host_result = await db.execute(
        select(PackageRule)
        .where(PackageRule.host_id == host_id)
        .order_by(PackageRule.priority.desc(), PackageRule.id.asc())
    )
    # Host overrides replace whatever a group contributed, and among
    # themselves the highest priority wins — LabDog settles a clash by
    # priority at every level (BUG-57). The read is ordered
    # ``priority DESC, id ASC``, so first-wins is that rule; the previous
    # unconditional assignment made the *last* row win, which after
    # BUG-69 made the order deterministic and the winner the lowest
    # priority.
    host_keys: set = set()
    for rule in host_result.scalars().all():
        if rule.package_name in host_keys:
            continue
        host_keys.add(rule.package_name)
        merged[rule.package_name] = EffectivePackageResponse(
            package_name=rule.package_name,
            version=rule.version,
            state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
            package_manager=rule.package_manager.value
            if hasattr(rule.package_manager, "value")
            else str(rule.package_manager),
            priority=rule.priority,
            hold=rule.hold if hasattr(rule, "hold") else False,
            source="host",
            source_id=host_id,
            source_name="host override",
        )

    return sorted(merged.values(), key=lambda p: p.package_name)


async def get_effective_repos(host_id: int, db: AsyncSession) -> list[PackageRepositoryResponse]:
    """
    Collect repos from all groups the host belongs to.

    Repos are additive — no priority conflict, deduplicated by
    (url, repo_type, distribution) not just url.
    """
    memberships = await db.execute(
        select(HostGroupMembership.c.group_id).where(HostGroupMembership.c.host_id == host_id)
    )
    group_ids = [row[0] for row in memberships.all()]

    if not group_ids:
        return []

    result = await db.execute(
        select(PackageRepository)
        .where(PackageRepository.group_id.in_(group_ids))
        .order_by(PackageRepository.id.asc())
    )
    repos = result.scalars().all()

    seen: dict[tuple, PackageRepositoryResponse] = {}
    for repo in repos:
        key = (
            repo.url,
            repo.repo_type.value if hasattr(repo.repo_type, "value") else str(repo.repo_type),
            repo.distribution,
        )
        seen[key] = PackageRepositoryResponse(
            id=repo.id,
            group_id=repo.group_id,
            name=repo.name,
            url=repo.url,
            key_url=repo.key_url,
            repo_type=repo.repo_type.value
            if hasattr(repo.repo_type, "value")
            else str(repo.repo_type),
            distribution=repo.distribution,
            components=repo.components,
            state=repo.state.value if hasattr(repo.state, "value") else str(repo.state),
        )

    return sorted(seen.values(), key=lambda r: r.name)
