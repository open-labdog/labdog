from sqlalchemy.ext.asyncio import AsyncSession

from app.enum_utils import enum_str
from app.merge_utils import load_owned_rows
from app.resolver.models import ResolverConfig
from app.resolver.schemas import EffectiveResolverResponse


async def get_effective_resolver(
    host_id: int, db: AsyncSession
) -> EffectiveResolverResponse | None:
    """Get the effective resolver config for a host.

    There is no merge key here: a host has one resolver config or none, so
    the first row in precedence order is the answer. Priority: host-level
    override > highest-priority group config. Returns None when nothing
    declares one (DNS is unmanaged).
    """
    owned = await load_owned_rows(db, host_id, ResolverConfig)
    if not owned:
        return None

    winner = owned[0]
    config = winner.row
    return EffectiveResolverResponse(
        nameservers=config.nameservers,
        search_domains=config.search_domains,
        options=config.options,
        resolver_type=enum_str(config.resolver_type),
        dns_over_tls=config.dns_over_tls,
        source=winner.source,
        source_id=winner.source_id,
        source_name=winner.source_name,
    )
