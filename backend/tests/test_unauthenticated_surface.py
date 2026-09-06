"""SEC-34: four small things that were more exposed than they needed to be.

None of these is a hole on its own. Together they are the difference
between an install that tells an unauthenticated caller nothing and one
that hands over a route map, a certificate inventory, and whatever text
a third-party client library happened to put in an exception.

The WebSocket half is the exception: the session-generation check added
for SEC-30 was genuinely missing there, so a token invalidated by
logging out still opened a terminal.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

from app.auth.token_version import SESSION_GENERATION_CLAIM
from app.config import settings


def _token(user_id: int, generation: int | None = 0) -> str:
    payload: dict = {"sub": str(user_id), "aud": ["fastapi-users:auth"]}
    if generation is not None:
        payload[SESSION_GENERATION_CLAIM] = generation
    return jwt.encode(payload, settings.security.secret_key, algorithm="HS256")


async def _user(db, *, active: bool = True, generation: int = 0):
    from fastapi_users.password import PasswordHelper

    from app.models.user import User as UserModel

    user = UserModel(
        email=f"ws_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=PasswordHelper().hash("CorrectHorseBattery1!"),
        is_active=active,
        is_superuser=False,
        is_verified=True,
        token_version=generation,
    )
    db.add(user)
    await db.flush()
    return user


class TestTheApiDocsAreOffByDefault:
    """They require no authentication and enumerate every route, its
    parameters and its schemas.

    With them disabled the paths still answer 200 — the SPA catch-all
    serves the frontend shell for anything it does not recognise — so
    these tests assert on the substance rather than the status code. The
    schema is what matters, and it is gone.
    """

    @staticmethod
    def _paths(app) -> set[str]:
        """Every route path, including those inside included routers."""
        found: set[str] = set()

        def walk(routes):
            for route in routes:
                path = getattr(route, "path", None)
                if path:
                    found.add(path)
                walk(getattr(getattr(route, "router", None), "routes", []) or [])
                walk(getattr(route, "routes", []) or [])

        walk(app.routes)
        return found

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    async def test_no_schema_is_served(self, client, path):
        resp = await client.get(path)
        body = resp.text
        assert '"openapi"' not in body
        assert "swagger-ui" not in body.lower()
        assert "redoc" not in body.lower()

    def test_the_setting_defaults_to_off(self):
        from app.config import ServerConfig

        assert ServerConfig().expose_docs is False

    def test_no_docs_routes_are_registered(self):
        from app.main import create_app

        with patch.object(settings.server, "expose_docs", False):
            app = create_app()
        paths = self._paths(app)
        assert "/docs" not in paths
        assert "/redoc" not in paths
        assert "/openapi.json" not in paths

    def test_turning_the_setting_on_brings_them_back(self):
        """An operator who wants them can have them — the point is that
        it is a decision, not the default."""
        from app.main import create_app

        with patch.object(settings.server, "expose_docs", True):
            app = create_app()
        paths = self._paths(app)
        assert "/docs" in paths
        assert "/redoc" in paths
        assert "/openapi.json" in paths

    def test_the_api_itself_is_unaffected(self):
        """Disabling the docs must not disable the routes they document.

        Asks the app rather than reading its route table: FastAPI keeps
        included routers wrapped, so the table is not a reliable list of
        what is reachable.
        """
        from starlette.testclient import TestClient

        from app.main import create_app

        with patch.object(settings.server, "expose_docs", False):
            app = create_app()
        # No context manager: the lifespan would open real connections
        # and routing does not need it.
        assert TestClient(app).get("/api/version").status_code == 200


class TestTheProviderTestDoesNotEchoExceptions:
    """A client library raising on an auth failure has been known to
    quote the request it sent, credentials included, and this response
    goes to the browser."""

    async def test_an_unexpected_error_is_summarised_not_quoted(self, superuser_client, db):
        from app.ai.models import AIProvider

        provider = AIProvider(
            name="leaky",
            provider_type="openai_compat",
            base_url="https://example.invalid/v1",
            model="gpt-x",
            enabled=True,
        )
        db.add(provider)
        await db.flush()

        secret = "sk-should-never-be-echoed-0123456789"
        with patch(
            "app.api.ai.build_provider",
            side_effect=RuntimeError(f"401 while sending Authorization: Bearer {secret}"),
        ):
            resp = await superuser_client.post(f"/api/ai/providers/{provider.id}/test")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is False
        assert secret not in resp.text
        assert "Authorization" not in resp.text
        # Still tells the operator where to look.
        assert "log" in body["message"].lower()


class TestTheTerminalHandshake:
    async def test_a_stale_session_generation_is_refused(self, db):
        """The gap SEC-30 left: revoking a session everywhere did not
        cover the socket that hands out a root shell."""
        from app.auth.ws_auth import get_ws_user

        user = await _user(db, generation=3)
        ws = AsyncMock()
        ws.cookies = {"labdog_auth": _token(user.id, generation=2)}

        with pytest.raises(RuntimeError, match="stale session generation"):
            await get_ws_user(ws, db)
        ws.close.assert_awaited_once_with(code=4401, reason="Session no longer valid")

    async def test_the_current_generation_is_accepted(self, db):
        from app.auth.ws_auth import get_ws_user

        user = await _user(db, generation=3)
        ws = AsyncMock()
        ws.cookies = {"labdog_auth": _token(user.id, generation=3)}

        assert (await get_ws_user(ws, db)).id == user.id

    async def test_a_token_from_before_the_claim_existed_is_refused(self, db):
        from app.auth.ws_auth import get_ws_user

        user = await _user(db)
        ws = AsyncMock()
        ws.cookies = {"labdog_auth": _token(user.id, generation=None)}

        with pytest.raises(RuntimeError, match="invalid token"):
            await get_ws_user(ws, db)

    @pytest.mark.parametrize(
        ("origin", "allowed"),
        [
            ("http://localhost:3000", True),
            ("https://evil.example.com", False),
            (None, True),
        ],
    )
    async def test_the_origin_is_checked(self, origin, allowed):
        """A missing Origin is allowed on purpose: browsers always send
        one, so its absence means a non-browser client, which the cookie
        already gates."""
        from app.auth.ws_auth import check_ws_origin

        ws = MagicMock()
        ws.headers = {} if origin is None else {"origin": origin}
        with patch.object(settings.security, "allowed_origins", ["http://localhost:3000"]):
            assert await check_ws_origin(ws) is allowed

    async def test_an_unauthenticated_peer_never_gets_an_accepted_socket(self, app, db):
        """Accepting first meant an unauthenticated caller held an open
        socket, however briefly, on the endpoint that hands out a root
        shell."""
        from starlette.testclient import TestClient

        accepted = []

        real_accept = None

        with TestClient(app) as tc:
            with patch(
                "starlette.websockets.WebSocket.accept",
                new_callable=AsyncMock,
                side_effect=lambda *a, **k: accepted.append(True),
            ):
                with pytest.raises(Exception):
                    with tc.websocket_connect("/api/ssh-terminal/ws/1"):
                        pass

        assert accepted == [], "handshake was accepted before authentication"
        assert real_accept is None
