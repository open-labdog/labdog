from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.host import Host
from app.models.ssh_key import SSHKey
from app.models.user import User
from app.auth.users import current_active_user, current_superuser
from app.crypto import encrypt_ssh_key, get_master_key
from app.schemas.ssh_keys import SSHKeyCreate, SSHKeyResponse

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
    _: User = Depends(current_superuser),
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
        is_default=body.is_default,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return key


@router.delete("/{key_id}", status_code=204)
async def delete_ssh_key(
    key_id: int,
    _: User = Depends(current_superuser),
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

    await db.delete(key)
    await db.commit()
