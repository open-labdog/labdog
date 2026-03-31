import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from sqlalchemy import select

from app.config import settings
from app.ssh_terminal.session_registry import SessionRegistry, SessionInfo


def _make_token(user_id: int) -> str:
    return jwt.encode(
        {"sub": str(user_id), "aud": ["fastapi-users:auth"]},
        settings.security.secret_key,
        algorithm="HS256",
    )


class TestSessionRegistry:

    async def test_register_deregister(self):
        reg = SessionRegistry(max_per_user=5, max_total=50)
        sid = reg.generate_session_id()

        ok = await reg.register(sid, user_id=1, host_id=10)
        assert ok is True
        assert reg.get_total_session_count() == 1
        assert reg.get_user_session_count(1) == 1

        removed = await reg.deregister(sid)
        assert removed is not None
        assert removed.session_id == sid
        assert reg.get_total_session_count() == 0

    async def test_per_user_limit(self):
        limit = 2
        reg = SessionRegistry(max_per_user=limit, max_total=50)

        for i in range(limit):
            ok = await reg.register(f"s{i}", user_id=1, host_id=10)
            assert ok is True

        ok = await reg.register("overflow", user_id=1, host_id=10)
        assert ok is False
        assert reg.get_user_session_count(1) == limit

        ok = await reg.register("other-user", user_id=2, host_id=10)
        assert ok is True

    async def test_global_limit(self):
        reg = SessionRegistry(max_per_user=10, max_total=3)

        for i in range(3):
            ok = await reg.register(f"s{i}", user_id=i + 1, host_id=10)
            assert ok is True

        ok = await reg.register("overflow", user_id=99, host_id=10)
        assert ok is False
        assert reg.get_total_session_count() == 3

    async def test_touch_updates_timestamp(self):
        reg = SessionRegistry(max_per_user=5, max_total=50)
        sid = "touch-test"
        await reg.register(sid, user_id=1, host_id=10)

        before = reg._sessions[sid].last_activity
        import asyncio
        await asyncio.sleep(0.01)
        await reg.touch(sid)
        after = reg._sessions[sid].last_activity

        assert after > before

    async def test_get_idle_sessions(self):
        reg = SessionRegistry(max_per_user=5, max_total=50)
        sid = "idle-test"
        await reg.register(sid, user_id=1, host_id=10)

        reg._sessions[sid].last_activity = datetime.now(timezone.utc) - timedelta(seconds=600)

        idle = reg.get_idle_sessions(timeout_seconds=300)
        assert sid in idle

        sid2 = "fresh"
        await reg.register(sid2, user_id=2, host_id=10)
        idle2 = reg.get_idle_sessions(timeout_seconds=300)
        assert sid2 not in idle2

    async def test_cleanup_session(self):
        reg = SessionRegistry(max_per_user=5, max_total=50)
        sid = "cleanup-test"

        mock_ws = AsyncMock()
        mock_process = MagicMock()
        mock_conn = MagicMock()

        await reg.register(
            sid, user_id=1, host_id=10,
            websocket=mock_ws, ssh_process=mock_process, ssh_conn=mock_conn,
        )
        assert reg.get_total_session_count() == 1

        await reg.cleanup_session(sid)

        assert reg.get_total_session_count() == 0
        mock_process.close.assert_called_once()
        mock_conn.close.assert_called_once()
        mock_ws.close.assert_awaited_once_with(code=1000)

    async def test_cleanup_nonexistent_session(self):
        reg = SessionRegistry(max_per_user=5, max_total=50)
        await reg.cleanup_session("does-not-exist")


pytestmark = pytest.mark.integration


class TestWSAuth:

    async def test_valid_cookie_returns_user(self, db):
        from fastapi_users.password import PasswordHelper
        from app.models.user import User as UserModel
        from app.auth.ws_auth import get_ws_user

        email = f"wsauth_{uuid.uuid4().hex[:8]}@test.com"
        ph = PasswordHelper()
        user = UserModel(
            email=email,
            hashed_password=ph.hash("TestPass1!"),
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        db.add(user)
        await db.flush()

        token = _make_token(user.id)
        mock_ws = AsyncMock()
        mock_ws.cookies = {"barricade_auth": token}

        result = await get_ws_user(mock_ws, db)
        assert result.id == user.id
        assert result.email == email

    async def test_missing_cookie_closes_4401(self, db):
        from app.auth.ws_auth import get_ws_user

        mock_ws = AsyncMock()
        mock_ws.cookies = {}

        with pytest.raises(RuntimeError, match="no cookie"):
            await get_ws_user(mock_ws, db)

        mock_ws.close.assert_awaited_once_with(code=4401, reason="Not authenticated")

    async def test_invalid_token_closes_4401(self, db):
        from app.auth.ws_auth import get_ws_user

        mock_ws = AsyncMock()
        mock_ws.cookies = {"barricade_auth": "this.is.garbage"}

        with pytest.raises(RuntimeError, match="invalid token"):
            await get_ws_user(mock_ws, db)

        mock_ws.close.assert_awaited_once_with(code=4401, reason="Invalid token")

    async def test_inactive_user_closes_4401(self, db):
        from fastapi_users.password import PasswordHelper
        from app.models.user import User as UserModel
        from app.auth.ws_auth import get_ws_user

        email = f"inactive_{uuid.uuid4().hex[:8]}@test.com"
        ph = PasswordHelper()
        user = UserModel(
            email=email,
            hashed_password=ph.hash("TestPass1!"),
            is_active=False,
            is_superuser=False,
            is_verified=True,
        )
        db.add(user)
        await db.flush()

        token = _make_token(user.id)
        mock_ws = AsyncMock()
        mock_ws.cookies = {"barricade_auth": token}

        with pytest.raises(RuntimeError, match="user not found"):
            await get_ws_user(mock_ws, db)

        mock_ws.close.assert_awaited_once_with(
            code=4401, reason="User not found or inactive"
        )


class TestSSHConnect:

    async def test_host_not_found_raises(self, db):
        from app.ssh_terminal.ssh_connect import open_ssh_shell, HostNotFoundError

        with pytest.raises(HostNotFoundError, match="not found"):
            await open_ssh_shell(host_id=999999, db=db)

    async def test_no_ssh_key_raises(self, db):
        from app.ssh_terminal.ssh_connect import open_ssh_shell, NoSSHKeyError
        from app.models.host import Host

        host = Host(
            hostname=f"nokey-{uuid.uuid4().hex[:8]}.test",
            ip_address="10.99.99.1",
            ssh_key_id=None,
        )
        db.add(host)
        await db.flush()

        with pytest.raises(NoSSHKeyError, match="no SSH key"):
            await open_ssh_shell(host_id=host.id, db=db)

    @patch("app.ssh_terminal.ssh_connect.asyncssh")
    @patch("app.ssh_terminal.ssh_connect.decrypt_ssh_key", return_value="fake-pem")
    @patch("app.ssh_terminal.ssh_connect.get_master_key", return_value=b"key")
    async def test_successful_connection(
        self, mock_master, mock_decrypt, mock_asyncssh, db,
    ):
        import asyncssh as real_asyncssh
        from tests.conftest import create_ssh_key, create_host
        from app.ssh_terminal.ssh_connect import open_ssh_shell

        ssh_key = await create_ssh_key(db)
        host = await create_host(db, ssh_key_id=ssh_key.id, ip="10.88.0.1")

        mock_process = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.create_process = AsyncMock(return_value=mock_process)
        # Preserve the real Error class so `except asyncssh.Error` works
        mock_asyncssh.Error = real_asyncssh.Error
        mock_asyncssh.import_private_key = MagicMock(return_value="imported-key")

        mock_ssh_connect = AsyncMock(return_value=mock_conn)
        with patch("app.ssh_terminal.ssh_connect.ssh_connect", mock_ssh_connect):
            conn, process = await open_ssh_shell(host_id=host.id, db=db)

        assert conn is mock_conn
        assert process is mock_process
        mock_ssh_connect.assert_awaited_once()
        mock_conn.create_process.assert_awaited_once()


class TestSSHTerminalEndpoint:

    async def test_ws_auth_failure(self, app, db):
        from starlette.testclient import TestClient

        with TestClient(app) as tc:
            with pytest.raises(Exception):
                with tc.websocket_connect("/api/ssh-terminal/ws/1") as ws:
                    ws.receive_text()

    async def test_ws_host_not_found(self, app, db):
        from starlette.testclient import TestClient
        from app.ssh_terminal.ssh_connect import HostNotFoundError

        fake_user = MagicMock()
        fake_user.id = 42

        with (
            patch("app.api.ssh_terminal.get_ws_user", new_callable=AsyncMock, return_value=fake_user),
            patch("app.api.ssh_terminal.open_ssh_shell", new_callable=AsyncMock, side_effect=HostNotFoundError("nope")),
            TestClient(app) as tc,
        ):
            with pytest.raises(Exception):
                with tc.websocket_connect("/api/ssh-terminal/ws/999999") as ws:
                    ws.receive_text()

    async def test_ws_session_limit_exceeded(self, app, db):
        from starlette.testclient import TestClient
        from app.ssh_terminal.session_registry import registry

        fake_user = MagicMock()
        fake_user.id = 777

        original_max = registry._max_per_user
        registry._max_per_user = 0

        try:
            with (
                patch("app.api.ssh_terminal.get_ws_user", new_callable=AsyncMock, return_value=fake_user),
                TestClient(app) as tc,
            ):
                with pytest.raises(Exception):
                    with tc.websocket_connect("/api/ssh-terminal/ws/1") as ws:
                        ws.receive_text()
        finally:
            registry._max_per_user = original_max

    async def test_ws_no_ssh_key_closes_4400(self, app, db):
        from starlette.testclient import TestClient
        from app.ssh_terminal.ssh_connect import NoSSHKeyError

        fake_user = MagicMock()
        fake_user.id = 55

        with (
            patch("app.api.ssh_terminal.get_ws_user", new_callable=AsyncMock, return_value=fake_user),
            patch("app.api.ssh_terminal.open_ssh_shell", new_callable=AsyncMock, side_effect=NoSSHKeyError("no key")),
            TestClient(app) as tc,
        ):
            with pytest.raises(Exception):
                with tc.websocket_connect("/api/ssh-terminal/ws/1") as ws:
                    ws.receive_text()
