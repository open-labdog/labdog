from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.auth.users import current_superuser
from app.models.user import User
from app.proxmox.models import ProxmoxNode
from app.proxmox.schemas import (
    ProxmoxNodeCreate,
    ProxmoxNodeUpdate,
    ProxmoxNodeResponse,
    ProxmoxTestResponse,
)
from app.proxmox.client import ProxmoxClient, ProxmoxError
from app.crypto import encrypt_ssh_key, decrypt_ssh_key, get_master_key
from app.audit.logger import log_action
from app.workflows.snapshot_cleanup import cleanup_orphaned_snapshots

router = APIRouter(prefix="/proxmox/nodes", tags=["proxmox"])


@router.get("", response_model=list[ProxmoxNodeResponse])
async def list_proxmox_nodes(
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ProxmoxNode).order_by(ProxmoxNode.name))
    return result.scalars().all()


@router.post("", response_model=ProxmoxNodeResponse, status_code=201)
async def create_proxmox_node(
    body: ProxmoxNodeCreate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(ProxmoxNode).where(ProxmoxNode.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Proxmox node name already exists")

    master_key = get_master_key()
    encrypted = encrypt_ssh_key(body.token_secret, master_key)

    node = ProxmoxNode(
        name=body.name,
        api_url=body.api_url,
        token_id=body.token_id,
        encrypted_token_secret=encrypted,
        verify_ssl=body.verify_ssl,
    )
    db.add(node)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="proxmox_node",
        entity_id=node.id,
        user_id=user.id,
        after_state={"name": node.name, "api_url": node.api_url, "token_id": node.token_id},
    )
    await db.commit()
    await db.refresh(node)
    return node


@router.post("/cleanup-snapshots")
async def trigger_snapshot_cleanup(
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Scan all Proxmox nodes for orphaned barricade snapshots and delete them."""
    return await cleanup_orphaned_snapshots(db)


@router.get("/{node_id}", response_model=ProxmoxNodeResponse)
async def get_proxmox_node(
    node_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProxmoxNode).where(ProxmoxNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Proxmox node not found")
    return node


@router.put("/{node_id}", response_model=ProxmoxNodeResponse)
async def update_proxmox_node(
    node_id: int,
    body: ProxmoxNodeUpdate,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProxmoxNode).where(ProxmoxNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Proxmox node not found")

    before = {"name": node.name, "api_url": node.api_url, "token_id": node.token_id}

    if body.name is not None and body.name != node.name:
        existing = await db.execute(
            select(ProxmoxNode).where(ProxmoxNode.name == body.name)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Proxmox node name already exists")
        node.name = body.name

    if body.api_url is not None:
        node.api_url = body.api_url

    if body.token_id is not None:
        node.token_id = body.token_id

    if body.token_secret is not None:
        master_key = get_master_key()
        node.encrypted_token_secret = encrypt_ssh_key(body.token_secret, master_key)

    if body.verify_ssl is not None:
        node.verify_ssl = body.verify_ssl

    await log_action(
        db=db,
        action="update",
        entity_type="proxmox_node",
        entity_id=node.id,
        user_id=user.id,
        before_state=before,
        after_state={"name": node.name, "api_url": node.api_url, "token_id": node.token_id},
    )
    await db.commit()
    await db.refresh(node)
    return node


@router.delete("/{node_id}", status_code=204)
async def delete_proxmox_node(
    node_id: int,
    user: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProxmoxNode).where(ProxmoxNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Proxmox node not found")

    await log_action(
        db=db,
        action="delete",
        entity_type="proxmox_node",
        entity_id=node.id,
        user_id=user.id,
        before_state={"name": node.name, "api_url": node.api_url, "token_id": node.token_id},
    )
    await db.delete(node)
    await db.commit()


@router.post("/{node_id}/test", response_model=ProxmoxTestResponse)
async def test_proxmox_node(
    node_id: int,
    _: User = Depends(current_superuser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProxmoxNode).where(ProxmoxNode.id == node_id)
    )
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Proxmox node not found")

    master_key = get_master_key()
    try:
        token_secret = decrypt_ssh_key(node.encrypted_token_secret, master_key)
    except Exception:
        return ProxmoxTestResponse(
            success=False,
            message="Failed to decrypt token secret",
        )

    client = ProxmoxClient(
        api_url=node.api_url,
        token_id=node.token_id,
        token_secret=token_secret,
        verify_ssl=node.verify_ssl,
    )
    try:
        data = await client.test_connection()
        version = data.get("version") if isinstance(data, dict) else None
        return ProxmoxTestResponse(
            success=True,
            message="Connection successful",
            version=str(version) if version else None,
        )
    except ProxmoxError as exc:
        return ProxmoxTestResponse(
            success=False,
            message=str(exc),
        )
    except Exception as exc:
        return ProxmoxTestResponse(
            success=False,
            message=f"Unexpected error: {exc}",
        )


