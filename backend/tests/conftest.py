"""
Shared pytest fixtures and factory helpers for Barricade integration tests.
"""

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


def pytest_configure(config):
    """Set test-safe security values before any app modules are imported."""
    import os

    os.environ.setdefault("BARRICADE_SECURITY__SECRET_KEY", "test-secret-key-not-for-production")
    os.environ.setdefault(
        "BARRICADE_SECURITY__ENCRYPTION_KEY", "vrPDeLMuFGehy2sYV//fyTd7EmnvOKbE2n4h7XM/8zg="
    )
    # Shared Redis across tests causes rate-limit pollution — fresh test runs
    # still trip the 5/min login and 100/min API limits because httpx hits
    # testserver from 127.0.0.1 repeatedly. Disable rate limiting for tests;
    # individual rate-limit behavior can be tested in targeted integration tests.
    os.environ.setdefault("BARRICADE_RATE_LIMIT__ENABLED", "false")


@pytest.fixture(scope="session")
def pg_url():
    import os
    import subprocess
    import sys

    from app.config import settings
    from app.crypto.key_management import generate_master_key

    ci_url = os.environ.get("DATABASE_URL")
    if ci_url:
        # CI: use the provided postgres service directly
        async_url = ci_url
        settings.database.url = async_url
        settings.security.encryption_key = generate_master_key()
        env = os.environ.copy()
        env["BARRICADE_DATABASE__URL"] = async_url
        alembic_path = str(Path(sys.executable).parent / "alembic")
        result = subprocess.run(
            [alembic_path, "upgrade", "head"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, f"Alembic failed: {result.stderr}"
        yield async_url
    else:
        # Local dev: spin up a throwaway postgres via testcontainers
        from testcontainers.postgres import PostgresContainer

        with PostgresContainer("postgres:16-alpine") as pg:
            sync_url = pg.get_connection_url()
            async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
                "postgresql://", "postgresql+asyncpg://"
            )
            settings.database.url = async_url
            settings.security.encryption_key = generate_master_key()
            env = os.environ.copy()
            env["BARRICADE_DATABASE__URL"] = async_url
            alembic_path = str(Path(sys.executable).parent / "alembic")
            result = subprocess.run(
                [alembic_path, "upgrade", "head"],
                capture_output=True,
                text=True,
                env=env,
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
    session = AsyncSession(
        bind=conn,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )

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
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=True,
    ) as c:
        yield c


async def _make_superuser(app, db):
    import httpx
    from fastapi_users.password import PasswordHelper
    from httpx import ASGITransport

    from app.models.user import User as UserModel

    email = f"superuser_{uuid.uuid4().hex[:8]}@test.com"
    password = "TestPass1!"
    ph = PasswordHelper()
    user = UserModel(
        email=email,
        hashed_password=ph.hash(password),
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True)
    resp = await c.post("/api/auth/jwt/login", data={"username": email, "password": password})
    assert resp.status_code in (200, 204), f"Login failed: {resp.text}"
    return c


@pytest.fixture
async def superuser_client(app, db):
    c = await _make_superuser(app, db)
    yield c
    await c.aclose()


@pytest.fixture
async def regular_user_client(app, db):
    import httpx
    from fastapi_users.password import PasswordHelper
    from httpx import ASGITransport

    from app.models.user import User as UserModel

    email = f"regular_{uuid.uuid4().hex[:8]}@test.com"
    password = "TestPass1!"
    ph = PasswordHelper()
    user = UserModel(
        email=email,
        hashed_password=ph.hash(password),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True)
    resp = await c.post("/api/auth/jwt/login", data={"username": email, "password": password})
    assert resp.status_code in (200, 204), f"Login failed: {resp.text}"
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
        priority=priority if priority is not None else int(uuid.uuid4().int % 1000) + 1,
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
    pub = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        .decode()
    )
    encrypted = encrypt_ssh_key(pem, get_master_key())
    ssh_key = SSHKey(
        name=name or f"key-{uuid.uuid4().hex[:8]}",
        public_key=pub,
        encrypted_private_key=encrypted,
    )
    db.add(ssh_key)
    await db.flush()
    return ssh_key


async def create_host(db, hostname=None, ip="10.0.0.1", ssh_key_id=None, group_ids=None):
    from sqlalchemy import insert as sa_insert

    from app.models.host import Host, HostGroupMembership

    host = Host(
        hostname=hostname or f"host-{uuid.uuid4().hex[:8]}.test",
        ip_address=ip,
        ssh_key_id=ssh_key_id,
    )
    db.add(host)
    await db.flush()
    if group_ids:
        for gid in group_ids:
            await db.execute(sa_insert(HostGroupMembership).values(host_id=host.id, group_id=gid))
        await db.flush()
    return host


async def create_rule(
    db,
    group_id,
    action="allow",
    protocol="tcp",
    direction="input",
    **kwargs,
):
    from app.models.firewall_rule import FirewallRule

    rule = FirewallRule(
        group_id=group_id,
        action=action,
        protocol=protocol,
        direction=direction,
        **kwargs,
    )
    db.add(rule)
    await db.flush()
    return rule
