from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enum_utils import enum_str
from app.merge_utils import OwnedRow, first_wins, load_owned_rows
from app.models.host import HostGroupMembership
from app.packages.models import PackageRepository, PackageRule
from app.packages.schemas import EffectivePackageResponse, PackageRepositoryResponse


def _to_response(owned: OwnedRow[PackageRule]) -> EffectivePackageResponse:
    rule = owned.row
    return EffectivePackageResponse(
        package_name=rule.package_name,
        version=rule.version,
        state=enum_str(rule.state),
        package_manager=enum_str(rule.package_manager),
        priority=rule.priority,
        hold=rule.hold,
        source=owned.source,
        source_id=owned.source_id,
        source_name=owned.source_name,
    )


async def get_effective_packages(host_id: int, db: AsyncSession) -> list[EffectivePackageResponse]:
    """Merge group-level package rules + host-level overrides into an effective list.

    Merge key: ``package_name``. Host overrides replace a group entry
    entirely; among rows at the same level the highest priority wins.
    Both levels carry a unique constraint on the merge key, so the
    same-level contest cannot actually arise here — the ordering is
    kept anyway so the module behaves like its six siblings.
    """
    owned = await load_owned_rows(db, host_id, PackageRule)
    winners = first_wins(owned, key=lambda r: r.package_name)
    return sorted((_to_response(o) for o in winners), key=lambda pkg: pkg.package_name)


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
            enum_str(repo.repo_type),
            repo.distribution,
        )
        seen[key] = PackageRepositoryResponse(
            id=repo.id,
            group_id=repo.group_id,
            name=repo.name,
            url=repo.url,
            key_url=repo.key_url,
            repo_type=enum_str(repo.repo_type),
            distribution=repo.distribution,
            components=repo.components,
            state=enum_str(repo.state),
        )

    return sorted(seen.values(), key=lambda r: r.name)
