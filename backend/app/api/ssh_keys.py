from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.crypto import encrypt_ssh_key, get_master_key
from app.db import get_db
from app.models.host import Host
from app.models.scan_config import ScanConfig
from app.models.ssh_key import SSHKey
from app.models.user import User
from app.schemas.ssh_keys import SSHKeyCreate, SSHKeyResponse, SSHKeyUpdate

router = APIRouter(prefix="/ssh-keys", tags=["ssh-keys"])


@router.get("", response_model=list[SSHKeyResponse])
async def list_ssh_keys(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SSHKey).order_by(SSHKey.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=SSHKeyResponse, status_code=201)
async def create_ssh_key(
    body: SSHKeyCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    # Check unique name
    existing = await db.execute(select(SSHKey).where(SSHKey.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="SSH key name already exists")

    # Encrypt private key
    master_key = get_master_key()
    encrypted = encrypt_ssh_key(body.private_key, master_key)

    # Serialize concurrent default-key creation with an advisory lock so that
    # only one request can unset existing defaults and insert the new key at a time.
    if body.is_default:
        await db.execute(text("SELECT pg_advisory_xact_lock(1)"))
        await db.execute(update(SSHKey).values(is_default=False))

    key = SSHKey(
        name=body.name,
        encrypted_private_key=encrypted,
        ssh_user=body.ssh_user,
        is_default=body.is_default,
    )
    db.add(key)
    await db.flush()

    await log_action(
        db=db,
        action="create",
        entity_type="ssh_key",
        entity_id=key.id,
        user_id=user.id,
        after_state={"name": key.name, "ssh_user": key.ssh_user, "is_default": key.is_default},
    )
    await db.commit()
    await db.refresh(key)
    return key


@router.put("/{key_id}", response_model=SSHKeyResponse)
async def update_ssh_key(
    key_id: int,
    body: SSHKeyUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SSHKey).where(SSHKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="SSH key not found")

    before = {"name": key.name, "ssh_user": key.ssh_user, "is_default": key.is_default}

    if body.name is not None and body.name != key.name:
        existing = await db.execute(select(SSHKey).where(SSHKey.name == body.name))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="SSH key name already exists")
        key.name = body.name

    if body.ssh_user is not None:
        key.ssh_user = body.ssh_user

    if body.is_default is not None and body.is_default and not key.is_default:
        await db.execute(text("SELECT pg_advisory_xact_lock(1)"))
        await db.execute(update(SSHKey).values(is_default=False))
        key.is_default = True
    elif body.is_default is not None and not body.is_default:
        key.is_default = False

    await log_action(
        db=db,
        action="update",
        entity_type="ssh_key",
        entity_id=key.id,
        user_id=user.id,
        before_state=before,
        after_state={"name": key.name, "ssh_user": key.ssh_user, "is_default": key.is_default},
    )
    await db.commit()
    await db.refresh(key)
    return key


@router.delete("/{key_id}", status_code=204)
async def delete_ssh_key(
    key_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SSHKey).where(SSHKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="SSH key not found")

    # Check no hosts reference this key
    hosts = await db.execute(select(Host.id).where(Host.ssh_key_id == key_id).limit(1))
    if hosts.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Cannot delete key referenced by hosts")

    # Check no scan configs reference this key (T6 rate-limit safety guard)
    scan_configs = await db.execute(
        select(ScanConfig.id).where(ScanConfig.ssh_key_id == key_id).limit(1)
    )
    if scan_configs.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete key referenced by scan configs",
        )

    await log_action(
        db=db,
        action="delete",
        entity_type="ssh_key",
        entity_id=key.id,
        user_id=user.id,
        before_state={"name": key.name, "ssh_user": key.ssh_user, "is_default": key.is_default},
    )
    await db.delete(key)
    await db.commit()
