from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.host import HostGroupMembership
from app.models.host_group import HostGroup
from app.resolver.models import ResolverConfig
from app.resolver.schemas import EffectiveResolverResponse


async def get_effective_resolver(
    host_id: int, db: AsyncSession
) -> EffectiveResolverResponse | None:
    """
    Get the effective resolver config for a host.

    Priority: host-level override > highest-priority group config.
    Returns None if no config found (DNS is unmanaged).
    """
    host_result = await db.execute(select(ResolverConfig).where(ResolverConfig.host_id == host_id))
    host_config = host_result.scalar_one_or_none()
    if host_config:
        return EffectiveResolverResponse(
            nameservers=host_config.nameservers,
            search_domains=host_config.search_domains,
            options=host_config.options,
            resolver_type=host_config.resolver_type.value
            if hasattr(host_config.resolver_type, "value")
            else str(host_config.resolver_type),
            dns_over_tls=host_config.dns_over_tls,
            source="host",
            source_id=host_id,
            source_name="host override",
        )

    memberships = await db.execute(
        select(
            HostGroupMembership.c.group_id,
            HostGroup.name,
            HostGroup.priority,
        )
        .join(HostGroup, HostGroup.id == HostGroupMembership.c.group_id)
        .where(HostGroupMembership.c.host_id == host_id)
        .order_by(HostGroup.priority.desc())
    )
    groups = memberships.all()

    for group_id, group_name, _priority in groups:
        result = await db.execute(select(ResolverConfig).where(ResolverConfig.group_id == group_id))
        config = result.scalar_one_or_none()
        if config:
            return EffectiveResolverResponse(
                nameservers=config.nameservers,
                search_domains=config.search_domains,
                options=config.options,
                resolver_type=config.resolver_type.value
                if hasattr(config.resolver_type, "value")
                else str(config.resolver_type),
                dns_over_tls=config.dns_over_tls,
                source="group",
                source_id=group_id,
                source_name=group_name,
            )

    return None
