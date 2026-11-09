"""CRUD endpoints for CA certificate rules at group and host level.

CA certs are managed as a one-time *Action* rather than a declarative
module — there is no drift detection, no periodic check, and changes
do not affect the host's ``sync_status`` badge. The deploy/remove
execution is handled by the Actions surface (see ``api/actions.py``)
plus the ``ca_cert_action`` Celery task.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.api._scoped_rules import (
    RuleAudit,
    apply_fields,
    commit_created,
    commit_updated,
    delete_and_commit,
    flush_or_409,
    require_group,
    require_host,
    scoped_row_or_404,
)
from app.auth.users import current_active_user
from app.ca_certs.merge import get_effective_ca_certs
from app.ca_certs.models import CACertRule
from app.ca_certs.pem_utils import parse_pem_certificate
from app.ca_certs.schemas import (
    CACertRuleCreate,
    CACertRuleResponse,
    CACertRuleUpdate,
    EffectiveCACertResponse,
)
from app.db import get_db
from app.models.user import User

router = APIRouter(tags=["ca-certs"])

_NOT_FOUND = "CA certificate rule not found"


def _build_rule_from_create(
    body: CACertRuleCreate,
    *,
    group_id: int | None = None,
    host_id: int | None = None,
) -> CACertRule:
    """Parse the PEM, extract metadata, and build a CACertRule.

    Pydantic has already validated the PEM via the schema's field validator,
    so this re-parse is cheap and guaranteed to succeed.
    """
    meta = parse_pem_certificate(body.pem_content)
    return CACertRule(
        group_id=group_id,
        host_id=host_id,
        name=body.name,
        pem_content=body.pem_content,
        fingerprint_sha256=meta.fingerprint_sha256,
        subject=meta.subject,
        issuer=meta.issuer,
        not_before=meta.not_before,
        not_after=meta.not_after,
        state=body.state,
        comment=body.comment,
    )


def _audit_state(rule: CACertRule) -> dict:
    return {
        "name": rule.name,
        "fingerprint_sha256": rule.fingerprint_sha256,
        "state": str(rule.state),
    }


_AUDIT = RuleAudit("ca_cert_rule", _audit_state)


# ---------------------------------------------------------------------------
# Group-level CA cert CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/groups/{group_id}/ca-certs",
    response_model=list[CACertRuleResponse],
)
async def list_group_ca_certs(
    group_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_group(db, group_id)

    result = await db.execute(
        select(CACertRule)
        .where(CACertRule.group_id == group_id)
        .order_by(CACertRule.name, CACertRule.id)
    )
    return result.scalars().all()


@router.post(
    "/groups/{group_id}/ca-certs",
    response_model=CACertRuleResponse,
    status_code=201,
)
async def create_group_ca_cert(
    group_id: int,
    body: CACertRuleCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_group(db, group_id)

    rule = _build_rule_from_create(body, group_id=group_id)
    db.add(rule)
    await flush_or_409(
        db,
        lambda: (
            f"CA certificate with fingerprint {rule.fingerprint_sha256} "
            "already exists in this group"
        ),
    )
    return await commit_created(db, rule, _AUDIT, user)


@router.put(
    "/groups/{group_id}/ca-certs/{rule_id}",
    response_model=CACertRuleResponse,
)
async def update_group_ca_cert(
    group_id: int,
    rule_id: int,
    body: CACertRuleUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, CACertRule, rule_id, CACertRule.group_id == group_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    apply_fields(rule, body.model_dump(exclude_unset=True))
    await db.flush()
    return await commit_updated(db, rule, _AUDIT, user, before)


@router.delete("/groups/{group_id}/ca-certs/{rule_id}", status_code=204)
async def delete_group_ca_cert(
    group_id: int,
    rule_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, CACertRule, rule_id, CACertRule.group_id == group_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Host-level CA cert overrides
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/ca-certs",
    response_model=list[CACertRuleResponse],
)
async def list_host_ca_certs(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    result = await db.execute(
        select(CACertRule)
        .where(CACertRule.host_id == host_id)
        .order_by(CACertRule.name, CACertRule.id)
    )
    return result.scalars().all()


@router.post(
    "/hosts/{host_id}/ca-certs",
    response_model=CACertRuleResponse,
    status_code=201,
)
async def create_host_ca_cert(
    host_id: int,
    body: CACertRuleCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    rule = _build_rule_from_create(body, host_id=host_id)
    db.add(rule)
    await flush_or_409(
        db,
        lambda: (
            f"CA certificate with fingerprint {rule.fingerprint_sha256} already exists on this host"
        ),
    )
    return await commit_created(db, rule, _AUDIT, user)


@router.put(
    "/hosts/{host_id}/ca-certs/{rule_id}",
    response_model=CACertRuleResponse,
)
async def update_host_ca_cert(
    host_id: int,
    rule_id: int,
    body: CACertRuleUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, CACertRule, rule_id, CACertRule.host_id == host_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    apply_fields(rule, body.model_dump(exclude_unset=True))
    await db.flush()
    return await commit_updated(db, rule, _AUDIT, user, before)


@router.delete("/hosts/{host_id}/ca-certs/{rule_id}", status_code=204)
async def delete_host_ca_cert(
    host_id: int,
    rule_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    rule = await scoped_row_or_404(
        db, CACertRule, rule_id, CACertRule.host_id == host_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Effective merged view
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-ca-certs",
    response_model=list[EffectiveCACertResponse],
)
async def effective_ca_certs(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await require_host(db, host_id)

    return await get_effective_ca_certs(host_id, db)
