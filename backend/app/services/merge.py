from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.host import HostGroupMembership
from app.models.host_group import HostGroup
from app.services.models import ServiceRule
from app.services.schemas import EffectiveServiceResponse


async def get_effective_services(
    host_id: int, db: AsyncSession
) -> list[EffectiveServiceResponse]:
    """
    Merge group-level service rules + host-level overrides into an effective list.

    Priority resolution:
    - Groups ordered by priority DESC (highest first). First occurrence of a
      service_name wins among groups.
    - Host-level overrides replace group entries entirely (full record, not field merge).
    """

    # 1. Query host's group memberships with priority, ordered DESC
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

    # 2. For each group (highest priority first), collect rules keyed by service_name.
    #    First occurrence wins — higher priority group takes precedence.
    merged: dict[str, EffectiveServiceResponse] = {}

    for group_id, group_name, _priority in groups:
        result = await db.execute(
            select(ServiceRule).where(ServiceRule.group_id == group_id)
        )
        for rule in result.scalars().all():
            if rule.service_name not in merged:
                merged[rule.service_name] = EffectiveServiceResponse(
                    service_name=rule.service_name,
                    state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
                    enabled=rule.enabled,
                    source="group",
                    source_id=group_id,
                    source_name=group_name,
                )

    # 3. Query host-level overrides
    host_result = await db.execute(
        select(ServiceRule).where(ServiceRule.host_id == host_id)
    )
    host_overrides = host_result.scalars().all()

    # 4. Host overrides REPLACE group entries entirely
    for rule in host_overrides:
        merged[rule.service_name] = EffectiveServiceResponse(
            service_name=rule.service_name,
            state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
            enabled=rule.enabled,
            source="host",
            source_id=host_id,
            source_name="host override",
        )

    # 5. Return as sorted list (by service name for deterministic output)
    return sorted(merged.values(), key=lambda s: s.service_name)
