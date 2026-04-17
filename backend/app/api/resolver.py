from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.db import get_db
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.user import User
from app.resolver.merge import get_effective_resolver
from app.resolver.models import ResolverConfig
from app.resolver.renderer import render_config
from app.resolver.schemas import (
    EffectiveResolverResponse,
    ResolverConfigCreate,
    ResolverConfigResponse,
)

router = APIRouter(tags=["resolver"])


# ---------------------------------------------------------------------------
# Group-level resolver config
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/resolver", response_model=ResolverConfigResponse)
async def get_group_resolver(group_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ResolverConfig).where(ResolverConfig.group_id == group_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="No resolver config for this group")
    return config


@router.put("/groups/{group_id}/resolver", response_model=ResolverConfigResponse)
async def upsert_group_resolver(
    group_id: int,
    payload: ResolverConfigCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_active_user),
):
    grp = await db.scalar(select(HostGroup).where(HostGroup.id == group_id))
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")

    result = await db.execute(select(ResolverConfig).where(ResolverConfig.group_id == group_id))
    config = result.scalar_one_or_none()

    if config:
        old_data = {
            "nameservers": config.nameservers,
            "resolver_type": str(config.resolver_type),
        }
        for key, value in payload.model_dump().items():
            setattr(config, key, value)
        action = "update"
    else:
        old_data = None
        config = ResolverConfig(group_id=group_id, **payload.model_dump())
        db.add(config)
        action = "create"

    await db.flush()

    await log_action(
        db=db,
        action=f"resolver.{action}",
        entity_type="resolver_config",
        entity_id=config.id,
        user_id=user.id,
        before_state=old_data,
        after_state={
            "nameservers": config.nameservers,
            "resolver_type": str(config.resolver_type),
        },
    )
    await db.commit()
    await db.refresh(config)
    return config


@router.delete("/groups/{group_id}/resolver", status_code=204)
async def delete_group_resolver(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_active_user),
):
    result = await db.execute(select(ResolverConfig).where(ResolverConfig.group_id == group_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="No resolver config for this group")

    config_id = config.id
    await log_action(
        db=db,
        action="resolver.delete",
        entity_type="resolver_config",
        entity_id=config_id,
        user_id=user.id,
        before_state={
            "nameservers": config.nameservers,
            "resolver_type": str(config.resolver_type),
        },
    )
    await db.delete(config)
    await db.commit()


# ---------------------------------------------------------------------------
# Host-level resolver override
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/resolver", response_model=ResolverConfigResponse)
async def get_host_resolver(host_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ResolverConfig).where(ResolverConfig.host_id == host_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="No resolver override for this host")
    return config


@router.put("/hosts/{host_id}/resolver", response_model=ResolverConfigResponse)
async def upsert_host_resolver(
    host_id: int,
    payload: ResolverConfigCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_active_user),
):
    host = await db.scalar(select(Host).where(Host.id == host_id))
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    result = await db.execute(select(ResolverConfig).where(ResolverConfig.host_id == host_id))
    config = result.scalar_one_or_none()

    if config:
        old_data = {
            "nameservers": config.nameservers,
            "resolver_type": str(config.resolver_type),
        }
        for key, value in payload.model_dump().items():
            setattr(config, key, value)
        action = "update"
    else:
        old_data = None
        config = ResolverConfig(host_id=host_id, **payload.model_dump())
        db.add(config)
        action = "create"

    await db.flush()

    await log_action(
        db=db,
        action=f"resolver.host_{action}",
        entity_type="resolver_config",
        entity_id=config.id,
        user_id=user.id,
        before_state=old_data,
        after_state={
            "nameservers": config.nameservers,
            "resolver_type": str(config.resolver_type),
        },
    )
    await db.commit()
    await db.refresh(config)
    return config


@router.delete("/hosts/{host_id}/resolver", status_code=204)
async def delete_host_resolver(
    host_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_active_user),
):
    result = await db.execute(select(ResolverConfig).where(ResolverConfig.host_id == host_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="No resolver override for this host")

    config_id = config.id
    await log_action(
        db=db,
        action="resolver.host_delete",
        entity_type="resolver_config",
        entity_id=config_id,
        user_id=user.id,
        before_state={
            "nameservers": config.nameservers,
            "resolver_type": str(config.resolver_type),
        },
    )
    await db.delete(config)
    await db.commit()


# ---------------------------------------------------------------------------
# Effective + Preview
# ---------------------------------------------------------------------------


@router.get(
    "/hosts/{host_id}/effective-resolver",
    response_model=EffectiveResolverResponse,
)
async def effective_resolver(host_id: int, db: AsyncSession = Depends(get_db)):
    config = await get_effective_resolver(host_id, db)
    if not config:
        raise HTTPException(status_code=404, detail="No resolver config applies to this host")
    return config


@router.get("/hosts/{host_id}/resolver-preview", response_class=PlainTextResponse)
async def resolver_preview(host_id: int, db: AsyncSession = Depends(get_db)):
    config = await get_effective_resolver(host_id, db)
    if not config:
        raise HTTPException(status_code=404, detail="No resolver config applies to this host")
    return render_config(config)
