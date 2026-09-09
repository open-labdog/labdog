"""Merge CA cert rules from groups and host overrides into an effective set.

Unlike the packages module (priority-based conflict resolution), CA certs
merge as a pure union by fingerprint. A host-level rule with the same
fingerprint as a group-inherited cert overrides it (used for
``state=absent`` to opt out of an inherited cert).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.ca_certs.models import CACertRule
from app.ca_certs.schemas import EffectiveCACertResponse
from app.enum_utils import enum_str
from app.merge_utils import OwnedRow, first_wins, load_owned_rows


def _to_response(owned: OwnedRow[CACertRule]) -> EffectiveCACertResponse:
    rule = owned.row
    return EffectiveCACertResponse(
        name=rule.name,
        fingerprint_sha256=rule.fingerprint_sha256,
        subject=rule.subject,
        issuer=rule.issuer,
        not_before=rule.not_before,
        not_after=rule.not_after,
        state=enum_str(rule.state),
        pem_content=rule.pem_content,
        source=owned.source,
        source_id=owned.source_id,
        source_name=owned.source_name,
    )


async def get_effective_ca_certs(host_id: int, db: AsyncSession) -> list[EffectiveCACertResponse]:
    """Return the effective CA cert set for a host.

    Merge key: ``fingerprint_sha256``. Group certs union — duplicates
    across groups collapse to one entry attributed to the highest-priority
    group that declared it — and a host rule overrides any matching
    fingerprint. ``CACertRule`` carries no priority column, so within one
    level the ordering is by id; both levels are unique on
    (owner, fingerprint), so no two rows can contest a key anyway.
    """
    owned = await load_owned_rows(db, host_id, CACertRule)
    winners = first_wins(owned, key=lambda r: r.fingerprint_sha256)
    return sorted((_to_response(o) for o in winners), key=lambda c: (c.name, c.fingerprint_sha256))
