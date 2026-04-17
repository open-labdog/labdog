from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cron.models import CronJob
from app.cron.schemas import EffectiveCronJobResponse
from app.models.host import HostGroupMembership
from app.models.host_group import HostGroup


async def get_effective_cron_jobs(host_id: int, db: AsyncSession) -> list[EffectiveCronJobResponse]:
    """Merge group-level CronJob rules + host-level overrides.

    Merge key: (name, user) composite. Higher priority group wins.
    Host override = full replacement.
    """
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

    merged: dict[tuple[str, str], EffectiveCronJobResponse] = {}

    for group_id, group_name, _priority in groups:
        result = await db.execute(select(CronJob).where(CronJob.group_id == group_id))
        for rule in result.scalars().all():
            key = (rule.name, rule.user)
            if key not in merged:
                merged[key] = EffectiveCronJobResponse(
                    name=rule.name,
                    user=rule.user,
                    schedule=rule.schedule,
                    command=rule.command,
                    environment=rule.environment or {},
                    state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
                    priority=rule.priority,
                    comment=rule.comment,
                    source="group",
                    source_id=group_id,
                    source_name=group_name,
                )

    host_result = await db.execute(select(CronJob).where(CronJob.host_id == host_id))
    for rule in host_result.scalars().all():
        key = (rule.name, rule.user)
        merged[key] = EffectiveCronJobResponse(
            name=rule.name,
            user=rule.user,
            schedule=rule.schedule,
            command=rule.command,
            environment=rule.environment or {},
            state=rule.state.value if hasattr(rule.state, "value") else str(rule.state),
            priority=rule.priority,
            comment=rule.comment,
            source="host",
            source_id=host_id,
            source_name="host override",
        )

    return sorted(merged.values(), key=lambda j: (j.name, j.user))
