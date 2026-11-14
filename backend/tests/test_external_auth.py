"""The Community hooks SSO builds on: identities, providers, sessions, plugins.

Community has no login method that uses these; the Enterprise SSO package
does. These tests pin the contract it relies on (``PLUGIN_API`` 1.1).
"""

import uuid
from http.cookies import SimpleCookie
from importlib.metadata import EntryPoint

import pytest
from fastapi import FastAPI, Response
from fastapi_users.password import PasswordHelper
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import select

from app.auth import external
from app.auth.external import (
    ExternalIdentity,
    ExternalLoginRefused,
    LoginProvider,
    issue_session,
    resolve_external_user,
)
from app.auth.permissions import PLUGIN_API, permissions_for
from app.auth.users import UserManager
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.user_identity import UserIdentity
from app.plugins import is_compatible, load_plugins

pytestmark = pytest.mark.integration


def _email() -> str:
    return f"sso_{uuid.uuid4().hex[:8]}@example.com"


def _identity(email=None, *, subject=None, verified=True, provider="oidc:test", name=None):
    return ExternalIdentity(
        provider=provider,
        subject=subject or uuid.uuid4().hex,
        email=email if email is not None else _email(),
        email_verified=verified,
        display_name=name,
    )


async def _local_user(db, email, *, role="admin", active=True) -> User:
    user = User(
        email=email,
        hashed_password=PasswordHelper().hash("TestPass1!Secure"),
        role=role,
        is_active=active,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    return user


# ── resolve_external_user ────────────────────────────────────────────────────


class TestProvisioning:
    async def test_new_person_gets_an_account_with_no_password_and_no_role(self, db):
        ident = _identity(name="Alice Example")
        user = await resolve_external_user(db, ident, provision=True, link_by_email=False)

        assert user.hashed_password is None
        assert user.role is None
        assert user.is_superuser is False
        assert user.display_name == "Alice Example"
        assert await permissions_for(user) == frozenset()
        link = (
            await db.execute(select(UserIdentity).where(UserIdentity.user_id == user.id))
        ).scalar_one()
        assert (link.provider, link.subject) == (ident.provider, ident.subject)

    async def test_provisioning_is_audited(self, db):
        user = await resolve_external_user(db, _identity(), provision=True, link_by_email=False)
        actions = (
            (await db.execute(select(AuditLog.action).where(AuditLog.entity_id == user.id)))
            .scalars()
            .all()
        )
        assert "user_provisioned" in actions

    async def test_unknown_person_is_refused_when_provisioning_is_off(self, db):
        with pytest.raises(ExternalLoginRefused) as exc:
            await resolve_external_user(db, _identity(), provision=False, link_by_email=False)
        assert exc.value.code == "not_provisioned"

    async def test_nothing_is_committed(self, db):
        """A caller that refuses afterwards rolls the new account back."""
        ident = _identity()
        await resolve_external_user(db, ident, provision=True, link_by_email=False)
        assert db.in_transaction()
        await db.rollback()
        found = (
            await db.execute(select(User).where(User.email == ident.email))
        ).scalar_one_or_none()
        assert found is None

    @pytest.mark.parametrize(
        "email, code", [(None, "no_email"), ("", "no_email"), ("not-an-email", "bad_email")]
    )
    async def test_an_unusable_email_is_refused(self, db, email, code):
        ident = ExternalIdentity(provider="oidc:test", subject=uuid.uuid4().hex, email=email)
        with pytest.raises(ExternalLoginRefused) as exc:
            await resolve_external_user(db, ident, provision=True, link_by_email=True)
        assert exc.value.code == code


class TestReturningUser:
    async def test_the_link_wins_over_a_changed_email(self, db):
        ident = _identity()
        first = await resolve_external_user(db, ident, provision=True, link_by_email=False)

        renamed = ExternalIdentity(provider=ident.provider, subject=ident.subject, email=_email())
        again = await resolve_external_user(db, renamed, provision=False, link_by_email=False)

        assert again.id == first.id

    async def test_same_subject_at_another_provider_is_somebody_else(self, db):
        ident = _identity()
        await resolve_external_user(db, ident, provision=True, link_by_email=False)
        other = ExternalIdentity(provider="ldap:corp", subject=ident.subject, email=_email())
        with pytest.raises(ExternalLoginRefused):
            await resolve_external_user(db, other, provision=False, link_by_email=False)

    async def test_a_disabled_account_is_refused(self, db):
        ident = _identity()
        user = await resolve_external_user(db, ident, provision=True, link_by_email=False)
        user.is_active = False
        await db.flush()
        with pytest.raises(ExternalLoginRefused) as exc:
            await resolve_external_user(db, ident, provision=True, link_by_email=False)
        assert exc.value.code == "disabled"


class TestLinkingByEmail:
    async def test_verified_email_links_when_allowed(self, db):
        email = _email()
        local = await _local_user(db, email)
        user = await resolve_external_user(
            db, _identity(email.upper(), verified=True), provision=False, link_by_email=True
        )
        assert user.id == local.id
        assert user.hashed_password  # the local password keeps working
        assert user.role == "admin"

    async def test_unverified_email_never_takes_over_an_account(self, db):
        email = _email()
        await _local_user(db, email)
        with pytest.raises(ExternalLoginRefused) as exc:
            await resolve_external_user(
                db, _identity(email, verified=False), provision=True, link_by_email=True
            )
        assert exc.value.code == "email_in_use"

    async def test_no_linking_unless_the_provider_allows_it(self, db):
        email = _email()
        await _local_user(db, email)
        with pytest.raises(ExternalLoginRefused) as exc:
            await resolve_external_user(
                db, _identity(email, verified=True), provision=True, link_by_email=False
            )
        assert exc.value.code == "email_in_use"

    async def test_a_disabled_local_account_is_not_linked(self, db):
        email = _email()
        await _local_user(db, email, active=False)
        with pytest.raises(ExternalLoginRefused) as exc:
            await resolve_external_user(db, _identity(email), provision=False, link_by_email=True)
        assert exc.value.code == "disabled"


# ── issue_session ────────────────────────────────────────────────────────────


class TestSession:
    async def _sign_in(self, db, user) -> SimpleCookie:
        response = Response()
        await issue_session(user, None, response, UserManager(SQLAlchemyUserDatabase(db, User)))
        jar = SimpleCookie()
        for name, value in response.raw_headers:
            if name == b"set-cookie":
                jar.load(value.decode())
        return jar

    async def test_sets_the_auth_and_csrf_cookies_and_last_login(self, db):
        user = await resolve_external_user(db, _identity(), provision=True, link_by_email=False)
        jar = await self._sign_in(db, user)
        assert "labdog_auth" in jar
        assert "labdog_csrf" in jar
        assert user.last_login_at is not None

    async def test_the_cookie_is_a_normal_session(self, db, external_client):
        user = await resolve_external_user(db, _identity(), provision=True, link_by_email=False)
        jar = await self._sign_in(db, user)
        external_client.cookies.set("labdog_auth", jar["labdog_auth"].value)

        me = await external_client.get("/api/users/me")
        assert me.status_code == 200
        assert me.json()["email"] == user.email
        # No role: signed in, but every permission-gated route says no.
        assert (await external_client.get("/api/hosts")).status_code == 403

    async def test_password_login_refuses_an_sso_only_account(self, db, external_client):
        user = await resolve_external_user(db, _identity(), provision=True, link_by_email=False)
        resp = await external_client.post(
            "/api/auth/jwt/login", data={"username": user.email, "password": "anything-at-all"}
        )
        assert resp.status_code == 400

    async def test_an_sso_only_account_cannot_set_a_password_itself(self, db, external_client):
        user = await resolve_external_user(db, _identity(), provision=True, link_by_email=False)
        jar = await self._sign_in(db, user)
        external_client.cookies.set("labdog_auth", jar["labdog_auth"].value)
        external_client.cookies.set("labdog_csrf", jar["labdog_csrf"].value)
        resp = await external_client.patch(
            "/api/users/me",
            json={"password": "BrandNewPass1!", "current_password": ""},
            headers={"X-CSRF-Token": jar["labdog_csrf"].value},
        )
        assert resp.status_code == 400


# ── Login providers ──────────────────────────────────────────────────────────


@pytest.fixture
def provider_sources():
    external.clear_login_provider_sources()
    yield external
    external.clear_login_provider_sources()


class TestProviders:
    async def test_community_offers_none(self, external_client, provider_sources):
        resp = await external_client.get("/api/auth/providers")
        assert resp.status_code == 200
        assert resp.json() == {"providers": []}

    async def test_lists_what_sources_offer(self, external_client, provider_sources):
        async def source():
            return [
                LoginProvider("corp", "Corporate SSO", "redirect", "/api/auth/sso/corp/start"),
                LoginProvider("ad", "Active Directory", "password", "/api/auth/ldap/ad/login"),
            ]

        provider_sources.register_login_provider_source(source)
        body = (await external_client.get("/api/auth/providers")).json()
        assert [p["key"] for p in body["providers"]] == ["corp", "ad"]
        assert body["providers"][1] == {
            "key": "ad",
            "name": "Active Directory",
            "kind": "password",
            "url": "/api/auth/ldap/ad/login",
        }

    async def test_a_failing_source_does_not_break_the_page(
        self, external_client, provider_sources
    ):
        async def broken():
            raise RuntimeError("database down")

        async def fine():
            return [LoginProvider("corp", "Corporate SSO", "redirect", "/api/auth/sso/corp/start")]

        provider_sources.register_login_provider_source(broken)
        provider_sources.register_login_provider_source(fine)
        body = (await external_client.get("/api/auth/providers")).json()
        assert [p["key"] for p in body["providers"]] == ["corp"]

    @pytest.mark.parametrize("url", ["https://evil.example/x", "//evil.example/x", "api/x"])
    def test_only_same_origin_paths(self, url):
        with pytest.raises(ValueError):
            LoginProvider("x", "X", "redirect", url)

    def test_only_known_kinds(self):
        with pytest.raises(ValueError):
            LoginProvider("x", "X", "popup", "/x")  # type: ignore[arg-type]


# ── Plugin loading ───────────────────────────────────────────────────────────


def _register(app):
    app.state.test_plugin_registered = True


_register.requires_plugin_api = "1.0"


def _register_too_new(app):  # pragma: no cover - must never run
    raise AssertionError("an incompatible plugin was registered")


_register_too_new.requires_plugin_api = "2.0"


def _register_unversioned(app):  # pragma: no cover - must never run
    raise AssertionError("an unversioned plugin was registered")


def _register_broken(app):
    raise RuntimeError("boom")


_register_broken.requires_plugin_api = "1.0"


def _ep(name, attr):
    return EntryPoint(name=name, value=f"tests.test_external_auth:{attr}", group="unbork.plugins")


class TestPlugins:
    def test_compatibility_rule(self):
        major, minor = (int(x) for x in PLUGIN_API.split("."))
        assert is_compatible(f"{major}.0")
        assert is_compatible(PLUGIN_API)
        assert not is_compatible(f"{major}.{minor + 1}")
        assert not is_compatible(f"{major + 1}.0")
        assert not is_compatible("one.two")

    def test_loads_compatible_plugins_and_skips_the_rest(self, caplog):
        app = FastAPI()
        loaded = load_plugins(
            app,
            [
                _ep("good", "_register"),
                _ep("too-new", "_register_too_new"),
                _ep("unversioned", "_register_unversioned"),
                _ep("broken", "_register_broken"),
                _ep("missing", "no_such_attribute"),
            ],
        )
        assert [p.name for p in loaded] == ["good"]
        assert app.state.test_plugin_registered is True
        assert "too-new" in caplog.text and "needs plugin API '2.0'" in caplog.text
        assert "register() failed" in caplog.text
        assert "failed to import" in caplog.text

    def test_a_plain_install_has_no_plugins(self):
        assert load_plugins(FastAPI(), []) == []


# ── Password login paths ─────────────────────────────────────────────────────


@pytest.fixture
def login_paths():
    from app.auth import login_paths as lp

    lp.reset_password_login_paths()
    yield lp
    lp.reset_password_login_paths()


class TestPasswordLoginPaths:
    async def test_a_registered_path_is_csrf_exempt(self, external_client, login_paths):
        path = "/api/auth/test-directory/login"
        before = await external_client.post(path, data={"username": "a", "password": "b"})
        assert before.status_code == 403  # the CSRF middleware answers first

        login_paths.register_password_login_path(path)
        after = await external_client.post(path, data={"username": "a", "password": "b"})
        assert after.status_code != 403  # reaches routing: no such route here

    @pytest.mark.parametrize(
        "path", ["/api/auth/ldap/login?provider=ad", "/api/auth/{slug}/login", "/login", "api/x"]
    )
    def test_only_exact_api_paths(self, login_paths, path):
        with pytest.raises(ValueError):
            login_paths.register_password_login_path(path)

    def test_core_paths_are_always_there(self, login_paths):
        assert login_paths.is_password_login_path("/api/auth/jwt/login")
        assert login_paths.is_password_login_path("/api/auth/register")
        assert not login_paths.is_password_login_path("/api/hosts")


_migrated: list[str] = []


def _migrate():
    _migrated.append("ran")


_migrate.requires_plugin_api = "1.0"


def _migrate_too_new():  # pragma: no cover - must never run
    raise AssertionError("an incompatible migration ran")


_migrate_too_new.requires_plugin_api = "9.0"


def _migrate_broken():
    raise RuntimeError("relation already exists")


_migrate_broken.requires_plugin_api = "1.0"


class TestPluginMigrations:
    def test_runs_compatible_migrations_and_survives_the_rest(self, caplog):
        from app.plugins import run_plugin_migrations

        _migrated.clear()
        done = run_plugin_migrations(
            [
                EntryPoint("a-good", "tests.test_external_auth:_migrate", "unbork.migrations"),
                EntryPoint(
                    "b-new", "tests.test_external_auth:_migrate_too_new", "unbork.migrations"
                ),
                EntryPoint(
                    "c-broken", "tests.test_external_auth:_migrate_broken", "unbork.migrations"
                ),
            ]
        )
        assert done == ["a-good"]
        assert _migrated == ["ran"]
        assert "need plugin API '9.0'" in caplog.text
        assert "c-broken" in caplog.text and "failed; skipped" in caplog.text
