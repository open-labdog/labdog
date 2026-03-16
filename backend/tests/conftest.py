"""
Shared pytest fixtures and factory helpers for Barricade integration tests.
"""

import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

if TYPE_CHECKING:
    from app.models.firewall_rule import FirewallRule
    from app.models.host import Host
    from app.models.host_group import HostGroup
    from app.models.ssh_key import SSHKey


@pytest.fixture(scope="session")
def pg_url():
    import os
    import subprocess
    import sys
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        sync_url = pg.get_connection_url()
        async_url = (
            sync_url
            .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
            .replace("postgresql://", "postgresql+asyncpg://")
        )
        from app.config import settings
        from app.crypto.key_management import generate_master_key
        settings.DATABASE_URL = async_url
        settings.ENCRYPTION_KEY = generate_master_key()
        env = os.environ.copy()
        env["DATABASE_URL"] = async_url
        alembic_path = str(Path(sys.executable).parent / "alembic")
        result = subprocess.run(
            [alembic_path, "upgrade", "head"],
            capture_output=True, text=True, env=env,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, f"Alembic failed: {result.stderr}"
        yield async_url


@pytest.fixture(scope="session")
def app(pg_url):
    from app.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
async def db(pg_url, app):
    from app.db import get_db
    engine = create_async_engine(pg_url)
    conn = await engine.connect()
    await conn.begin()
    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    async def override_get_db():
        yield session
    app.dependency_overrides[get_db] = override_get_db
    yield session
    await session.close()
    await conn.rollback()
    await conn.close()
    await engine.dispose()
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def client(app, db):
    import httpx
    from httpx import ASGITransport
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True) as c:
        yield c


async def _make_superuser(app, db):
    import httpx
    from httpx import ASGITransport
    email = f"superuser_{uuid.uuid4().hex[:8]}@test.com"
    password = "TestPass1!"
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True)
    resp = await c.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, f"Register failed: {resp.text}"
    await db.execute(
        text("UPDATE users SET is_superuser = TRUE, is_verified = TRUE WHERE email = :email"),
        {"email": email},
    )
    await db.flush()
    resp = await c.post("/auth/jwt/login", data={"username": email, "password": password})
    assert resp.status_code in (200, 204), f"Login failed: {resp.text}"
    return c


@pytest.fixture
async def superuser_client(app, db):
    c = await _make_superuser(app, db)
    yield c
    await c.aclose()


@pytest.fixture
async def viewer_client(app, db, superuser_client):
    import httpx
    from httpx import ASGITransport
    from app.models.user_group_permission import GroupRole, UserGroupPermission
    email = f"viewer_{uuid.uuid4().hex[:8]}@test.com"
    password = "TestPass1!"
    resp = await superuser_client.post("/api/groups", json={"name": f"vg-{uuid.uuid4().hex[:6]}", "priority": 500})
    assert resp.status_code == 201
    group_id = resp.json()["id"]
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True)
    resp = await c.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201
    user_id = resp.json()["id"]
    await db.execute(text("UPDATE users SET is_verified = TRUE WHERE email = :email"), {"email": email})
    db.add(UserGroupPermission(user_id=user_id, group_id=group_id, role=GroupRole.viewer))
    await db.flush()
    resp = await c.post("/auth/jwt/login", data={"username": email, "password": password})
    assert resp.status_code in (200, 204)
    yield c
    await c.aclose()


@pytest.fixture
async def editor_client(app, db, superuser_client):
    import httpx
    from httpx import ASGITransport
    from app.models.user_group_permission import GroupRole, UserGroupPermission
    email = f"editor_{uuid.uuid4().hex[:8]}@test.com"
    password = "TestPass1!"
    resp = await superuser_client.post("/api/groups", json={"name": f"eg-{uuid.uuid4().hex[:6]}", "priority": 501})
    assert resp.status_code == 201
    group_id = resp.json()["id"]
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True)
    resp = await c.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201
    user_id = resp.json()["id"]
    await db.execute(text("UPDATE users SET is_verified = TRUE WHERE email = :email"), {"email": email})
    db.add(UserGroupPermission(user_id=user_id, group_id=group_id, role=GroupRole.editor))
    await db.flush()
    resp = await c.post("/auth/jwt/login", data={"username": email, "password": password})
    assert resp.status_code in (200, 204)
    yield c
    await c.aclose()


@pytest.fixture
def mock_celery_tasks():
    mock = MagicMock()
    with patch("app.tasks.sync.run_sync_playbook.delay", mock):
        yield mock


async def create_group(db, name=None, priority=None, description=None):
    from app.models.host_group import HostGroup
    group = HostGroup(
        name=name or f"group-{uuid.uuid4().hex[:8]}",
        priority=priority if priority is not None else int(uuid.uuid4().int % 10000),
        description=description,
    )
    db.add(group)
    await db.flush()
    return group


async def create_ssh_key(db, name=None):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from app.crypto.encryption import encrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.models.ssh_key import SSHKey
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode()
    encrypted = encrypt_ssh_key(pem, get_master_key())
    ssh_key = SSHKey(name=name or f"key-{uuid.uuid4().hex[:8]}", public_key=pub, encrypted_private_key=encrypted)
    db.add(ssh_key)
    await db.flush()
    return ssh_key


async def create_host(db, hostname=None, ip="10.0.0.1", ssh_key_id=None, group_ids=None):
    from sqlalchemy import insert as sa_insert
    from app.models.host import Host, HostGroupMembership
    host = Host(hostname=hostname or f"host-{uuid.uuid4().hex[:8]}.test", ip_address=ip, ssh_key_id=ssh_key_id)
    db.add(host)
    await db.flush()
    if group_ids:
        for gid in group_ids:
            await db.execute(sa_insert(HostGroupMembership).values(host_id=host.id, group_id=gid))
        await db.flush()
    return host


async def create_rule(db, group_id, action="allow", protocol="tcp", direction="input", **kwargs):
    from app.models.firewall_rule import FirewallRule
    rule = FirewallRule(group_id=group_id, action=action, protocol=protocol, direction=direction, **kwargs)
    db.add(rule)
    await db.flush()
    return rule
