"""Merge CA cert rules from groups and host overrides into an effective set.

Unlike the packages module (priority-based conflict resolution), CA certs
merge as a pure union by fingerprint. A host-level rule with the same
fingerprint as a group-inherited cert overrides it (used for
``state=absent`` to opt out of an inherited cert).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ca_certs.models import CACertRule
from app.ca_certs.schemas import EffectiveCACertResponse
from app.models.host import HostGroupMembership
from app.models.host_group import HostGroup


def _state_str(state) -> str:
    return state.value if hasattr(state, "value") else str(state)


async def get_effective_ca_certs(host_id: int, db: AsyncSession) -> list[EffectiveCACertResponse]:
    """Return the effective CA cert set for a host.

    Group certs are unioned by fingerprint (no priority — duplicates across
    groups collapse to one entry, with source set to the first encountered
    group). Host-level rules then override any matching fingerprint.
    """
    memberships = await db.execute(
        select(
            HostGroupMembership.c.group_id,
            HostGroup.name,
        )
        .join(HostGroup, HostGroup.id == HostGroupMembership.c.group_id)
        .where(HostGroupMembership.c.host_id == host_id)
        .order_by(HostGroup.priority.desc(), HostGroup.id.asc())
    )
    groups = memberships.all()

    merged: dict[str, EffectiveCACertResponse] = {}

    for group_id, group_name in groups:
        result = await db.execute(select(CACertRule).where(CACertRule.group_id == group_id))
        for rule in result.scalars().all():
            if rule.fingerprint_sha256 not in merged:
                merged[rule.fingerprint_sha256] = EffectiveCACertResponse(
                    name=rule.name,
                    fingerprint_sha256=rule.fingerprint_sha256,
                    subject=rule.subject,
                    issuer=rule.issuer,
                    not_before=rule.not_before,
                    not_after=rule.not_after,
                    state=_state_str(rule.state),
                    pem_content=rule.pem_content,
                    source="group",
                    source_id=group_id,
                    source_name=group_name,
                )

    host_result = await db.execute(select(CACertRule).where(CACertRule.host_id == host_id))
    for rule in host_result.scalars().all():
        merged[rule.fingerprint_sha256] = EffectiveCACertResponse(
            name=rule.name,
            fingerprint_sha256=rule.fingerprint_sha256,
            subject=rule.subject,
            issuer=rule.issuer,
            not_before=rule.not_before,
            not_after=rule.not_after,
            state=_state_str(rule.state),
            pem_content=rule.pem_content,
            source="host",
            source_id=host_id,
            source_name="host override",
        )

    return sorted(merged.values(), key=lambda c: (c.name, c.fingerprint_sha256))
