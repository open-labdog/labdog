from sqlalchemy.ext.asyncio import AsyncSession

from app.enum_utils import enum_str
from app.merge_utils import OwnedRow, first_wins, load_owned_rows
from app.services.models import ServiceRule
from app.services.schemas import EffectiveServiceResponse


def _to_response(owned: OwnedRow[ServiceRule]) -> EffectiveServiceResponse:
    rule = owned.row
    return EffectiveServiceResponse(
        service_name=rule.service_name,
        state=enum_str(rule.state),
        enabled=rule.enabled,
        unit_content=rule.unit_content,
        deploy_mode=enum_str(rule.deploy_mode),
        source=owned.source,
        source_id=owned.source_id,
        source_name=owned.source_name,
    )


async def get_effective_services(host_id: int, db: AsyncSession) -> list[EffectiveServiceResponse]:
    """Merge group-level service rules + host-level overrides into an effective list.

    Merge key: ``service_name``. Host overrides replace a group entry
    entirely (full record, not a field merge); among rows at the same
    level the highest priority wins, ties by id ascending.
    """
    owned = await load_owned_rows(db, host_id, ServiceRule)
    winners = first_wins(owned, key=lambda r: r.service_name)
    return sorted((_to_response(o) for o in winners), key=lambda s: s.service_name)
