from sqlalchemy.ext.asyncio import AsyncSession

from app.cron.models import CronJob
from app.cron.schemas import EffectiveCronJobResponse
from app.enum_utils import enum_str
from app.merge_utils import OwnedRow, first_wins, load_owned_rows


def _to_response(owned: OwnedRow[CronJob]) -> EffectiveCronJobResponse:
    rule = owned.row
    return EffectiveCronJobResponse(
        name=rule.name,
        user=rule.user,
        schedule=rule.schedule,
        command=rule.command,
        environment=rule.environment or {},
        state=enum_str(rule.state),
        priority=rule.priority,
        comment=rule.comment,
        source=owned.source,
        source_id=owned.source_id,
        source_name=owned.source_name,
    )


async def get_effective_cron_jobs(host_id: int, db: AsyncSession) -> list[EffectiveCronJobResponse]:
    """Merge group-level CronJob rules + host-level overrides.

    Merge key: the ``(name, user)`` pair — the same job name under two
    users is two jobs. Host override = full replacement; among rows at
    the same level the highest priority wins.
    """
    owned = await load_owned_rows(db, host_id, CronJob)
    winners = first_wins(owned, key=lambda r: (r.name, r.user))
    return sorted((_to_response(o) for o in winners), key=lambda j: (j.name, j.user))
