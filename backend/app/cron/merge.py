from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cron.models import CronJob
from app.cron.schemas import EffectiveCronJobResponse
from app.merge_utils import ordered_groups_for_host


async def get_effective_cron_jobs(host_id: int, db: AsyncSession) -> list[EffectiveCronJobResponse]:
    """Merge group-level CronJob rules + host-level overrides.

    Merge key: (name, user) composite. Higher priority group wins.
    Host override = full replacement.
    """
    memberships = await db.execute(ordered_groups_for_host(host_id))
    groups = memberships.all()

    merged: dict[tuple[str, str], EffectiveCronJobResponse] = {}

    for group_id, group_name, _priority in groups:
        result = await db.execute(
            select(CronJob)
            .where(CronJob.group_id == group_id)
            .order_by(CronJob.priority.desc(), CronJob.id.asc())
        )
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

    host_result = await db.execute(
        select(CronJob)
        .where(CronJob.host_id == host_id)
        .order_by(CronJob.priority.desc(), CronJob.id.asc())
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
        key = (rule.name, rule.user)
        if key in host_keys:
            continue
        host_keys.add(key)
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
