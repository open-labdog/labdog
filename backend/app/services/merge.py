from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.merge_utils import ordered_groups_for_host
from app.services.models import ServiceRule
from app.services.schemas import EffectiveServiceResponse


async def get_effective_services(host_id: int, db: AsyncSession) -> list[EffectiveServiceResponse]:
    """
    Merge group-level service rules + host-level overrides into an effective list.

    Priority resolution:
    - Groups ordered by priority DESC (highest first), ties by group id ASC.
      First occurrence of a service_name wins among groups.
    - Host-level overrides replace group entries entirely (full record, not field merge).
    """

    # 1. Query host's group memberships with priority, ordered DESC
    memberships = await db.execute(ordered_groups_for_host(host_id))
    groups = memberships.all()

    # 2. For each group (highest priority first), collect rules keyed by service_name.
    #    First occurrence wins — higher priority group takes precedence.
    merged: dict[str, EffectiveServiceResponse] = {}

    for group_id, group_name, _priority in groups:
        result = await db.execute(
            select(ServiceRule)
            .where(ServiceRule.group_id == group_id)
            .order_by(ServiceRule.priority.desc(), ServiceRule.id.asc())
        )
        for rule in result.scalars().all():
            if rule.service_name not in merged:
                merged[rule.service_name] = EffectiveServiceResponse(
                    service_name=rule.service_name,
                    state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
                    enabled=rule.enabled,
                    unit_content=rule.unit_content,
                    deploy_mode=rule.deploy_mode.value
                    if hasattr(rule.deploy_mode, "value")
                    else str(rule.deploy_mode),
                    source="group",
                    source_id=group_id,
                    source_name=group_name,
                )

    # 3. Query host-level overrides
    host_result = await db.execute(
        select(ServiceRule)
        .where(ServiceRule.host_id == host_id)
        .order_by(ServiceRule.priority.desc(), ServiceRule.id.asc())
    )
    host_overrides = host_result.scalars().all()

    # 4. Host overrides REPLACE group entries entirely
    # Host overrides replace whatever a group contributed, and among
    # themselves the highest priority wins — LabDog settles a clash by
    # priority at every level (BUG-57). The read is ordered
    # ``priority DESC, id ASC``, so first-wins is that rule; the previous
    # unconditional assignment made the *last* row win, which after
    # BUG-69 made the order deterministic and the winner the lowest
    # priority.
    host_keys: set = set()
    for rule in host_overrides:
        if rule.service_name in host_keys:
            continue
        host_keys.add(rule.service_name)
        merged[rule.service_name] = EffectiveServiceResponse(
            service_name=rule.service_name,
            state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
            enabled=rule.enabled,
            unit_content=rule.unit_content,
            deploy_mode=rule.deploy_mode.value
            if hasattr(rule.deploy_mode, "value")
            else str(rule.deploy_mode),
            source="host",
            source_id=host_id,
            source_name="host override",
        )

    # 5. Return as sorted list (by service name for deterministic output)
    return sorted(merged.values(), key=lambda s: s.service_name)
