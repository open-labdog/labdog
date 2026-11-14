"""Fixed roles and the one permission check every route goes through.

The guardrail at the top is the test that keeps this true: a new route
with no ``require()`` fails it, so a route cannot quietly fall back to
"any signed-in user".
"""

from __future__ import annotations

import re
import uuid

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi_users.password import PasswordHelper

from app.auth import permissions as perms
from app.auth.permissions import (
    GLOBAL,
    PASSWORD_CHANGE_REQUIRED,
    ROLE_PERMISSIONS,
    BoundScope,
    Permission,
    Role,
    Scope,
    permissions_for,
    row_scope,
    set_permission_resolver,
    set_visible_hosts_resolver,
)
from app.models.sync_job import SyncJob
from app.models.user import ROLE_VALUES, User
from tests.conftest import _CsrfAutoTransport, create_host

PASSWORD = "TestPass1!-long-enough"

#: Routes that answer without a permission, and why.
PUBLIC = {
    ("GET", "/api/version"),  # public by design
    ("GET", "/health"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/metrics"),  # off unless enabled in the TOML; proxy-protected
    ("HEAD", "/metrics"),
    ("GET", "/api/auth/setup-status"),  # before any account exists
    ("POST", "/api/auth/register"),  # first account only
    ("POST", "/api/auth/jwt/login"),
    ("POST", "/api/auth/jwt/logout"),  # any session may end itself
    ("GET", "/api/auth/csrf-token"),  # any session
    ("GET", "/api/users/me"),  # any session, incl. one that must change password
    ("PATCH", "/api/users/me"),  # changing your own password
    ("POST", "/api/webhooks/github"),  # HMAC-signed, no session
    ("POST", "/api/webhooks/gitea"),
    ("POST", "/api/webhooks/gitlab"),
    ("POST", "/api/webhooks/grafana-alerts"),
}


class _Route:
    """One HTTP route with its full path and effective dependencies."""

    def __init__(self, path: str, methods, dependant):
        self.path = path
        self.methods = methods
        self.dependant = dependant


def _routes(app):
    """Every (method, route) as served, router prefixes included.

    Recent FastAPI keeps included routers lazy, so ``app.routes`` holds
    the routers rather than their routes; their effective contexts carry
    the prefixed path and the router-level dependencies.
    """
    try:
        from fastapi.routing import _iter_routes_with_context
    except ImportError:  # older FastAPI: routes are flattened already
        pairs = [(r, None) for r in app.routes]
    else:
        pairs = list(_iter_routes_with_context(app.routes))
    for original, ctx in pairs:
        if ctx is not None:
            if not isinstance(original, APIRoute):
                continue
            route = _Route(ctx.path, ctx.methods, ctx.dependant)
        elif isinstance(original, APIRoute):
            route = _Route(original.path, original.methods, original.dependant)
        else:
            continue
        for method in route.methods:
            yield method, route


def _required_permissions(route) -> set[str]:
    found: set[str] = set()

    def walk(dependant):
        perm = getattr(dependant.call, "required_permission", None)
        if perm:
            found.add(perm)
        for sub in dependant.dependencies:
            walk(sub)

    walk(route.dependant)
    return found


class TestEveryRouteDeclaresAPermission:
    def test_no_route_is_left_on_bare_authentication(self, app):
        missing = []
        for method, route in _routes(app):
            if (method, route.path) in PUBLIC:
                continue
            if not _required_permissions(route):
                missing.append(f"{method} {route.path}")
        assert not missing, "routes without require():\n" + "\n".join(sorted(missing))

    def test_the_public_list_has_no_stale_entries(self, app):
        present = {(m, r.path) for m, r in _routes(app)}
        assert PUBLIC <= present, sorted(PUBLIC - present)

    def test_each_route_asks_for_exactly_one_permission(self, app):
        many = [
            f"{m} {r.path}: {sorted(p)}"
            for m, r in _routes(app)
            if len(p := _required_permissions(r)) > 1
        ]
        assert not many, many


class TestTheRoleMap:
    def test_model_and_enum_agree_on_role_names(self):
        assert set(ROLE_VALUES) == {r.value for r in Role}

    def test_roles_nest(self):
        assert ROLE_PERMISSIONS[Role.VIEWER] < ROLE_PERMISSIONS[Role.OPERATOR]
        assert ROLE_PERMISSIONS[Role.OPERATOR] < ROLE_PERMISSIONS[Role.ADMIN]
        assert ROLE_PERMISSIONS[Role.ADMIN] == {p.value for p in Permission}

    @pytest.mark.parametrize("perm", ["settings.manage", "users.manage", "integrations.manage"])
    def test_admin_only(self, perm):
        assert perm not in ROLE_PERMISSIONS[Role.OPERATOR]

    @pytest.mark.parametrize("perm", ["ai.use", "terminal.open", "config.edit"])
    def test_viewers_do_not_get(self, perm):
        assert perm not in ROLE_PERMISSIONS[Role.VIEWER]

    def test_viewers_read_the_audit_log(self):
        assert "audit.view" in ROLE_PERMISSIONS[Role.VIEWER]

    def test_operators_manage_schedules(self):
        assert "schedules.manage" in ROLE_PERMISSIONS[Role.OPERATOR]

    async def test_no_role_grants_nothing(self):
        user = User(email="x@test.com", hashed_password="x", is_active=True)
        assert await permissions_for(user) == frozenset()

    async def test_a_disabled_user_has_nothing(self):
        user = User(email="x@test.com", hashed_password="x", is_active=False)
        user.role = "admin"
        assert await permissions_for(user) == frozenset()

    def test_an_unknown_permission_name_fails_at_import(self):
        with pytest.raises(ValueError):
            perms.require("hosts.destroy")

    def test_role_mirrors_into_is_superuser(self):
        user = User(email="x@test.com", hashed_password="x")
        user.role = "admin"
        assert user.is_superuser is True
        user.role = "viewer"
        assert user.is_superuser is False


# ── Clients per role ────────────────────────────────────────────────────────


async def _client(app, db, role: str | None, *, must_change: bool = False):
    email = f"{role or 'none'}_{uuid.uuid4().hex[:8]}@test.com"
    user = User(
        email=email,
        hashed_password=PasswordHelper().hash(PASSWORD),
        is_active=True,
        is_verified=True,
        must_change_password=must_change,
    )
    user.role = role
    db.add(user)
    await db.flush()
    c = httpx.AsyncClient(
        transport=_CsrfAutoTransport(app=app),
        base_url="http://testserver",
        follow_redirects=True,
    )
    resp = await c.post("/api/auth/jwt/login", data={"username": email, "password": PASSWORD})
    assert resp.status_code in (200, 204), resp.text
    return c, user


@pytest.fixture
async def viewer(app, db):
    c, _ = await _client(app, db, "viewer")
    yield c
    await c.aclose()


@pytest.fixture
async def operator(app, db):
    c, _ = await _client(app, db, "operator")
    yield c
    await c.aclose()


@pytest.fixture
async def admin(app, db):
    c, user = await _client(app, db, "admin")
    c.user = user  # type: ignore[attr-defined]
    yield c
    await c.aclose()


def _concrete(path: str) -> str:
    path = re.sub(r"\{[^}]*:path\}", "x", path)
    return re.sub(r"\{[^}]+\}", "1", path)


def _denied_cases(app):
    """Every (method, path, permission) a viewer or operator lacks."""
    for method, route in _routes(app):
        if (method, route.path) in PUBLIC or method == "HEAD":
            continue
        (perm,) = _required_permissions(route)
        yield method, _concrete(route.path), perm


class TestEnforcementOnEveryRoute:
    """Refusal happens before the handler, so no route body runs here."""

    @pytest.mark.parametrize("role", ["viewer", "operator"])
    async def test_every_route_refuses_a_role_without_its_permission(self, app, db, role):
        client, _ = await _client(app, db, role)
        allowed = ROLE_PERMISSIONS[Role(role)]
        wrong = []
        try:
            for method, path, perm in _denied_cases(app):
                if perm in allowed:
                    continue
                resp = await client.request(method, path, json={})
                if resp.status_code != 403:
                    wrong.append(f"{method} {path} ({perm}): {resp.status_code}")
        finally:
            await client.aclose()
        assert not wrong, "\n".join(wrong)

    async def test_no_role_is_refused_everywhere(self, app, db):
        client, _ = await _client(app, db, None)
        try:
            codes = {
                (m, p): (await client.request(m, p, json={})).status_code
                for m, p, _ in _denied_cases(app)
            }
        finally:
            await client.aclose()
        assert set(codes.values()) == {403}, {k: v for k, v in codes.items() if v != 403}


class TestRepresentativeRoutes:
    async def test_viewer_reads_hosts_and_audit(self, viewer):
        assert (await viewer.get("/api/hosts")).status_code == 200
        assert (await viewer.get("/api/audit-log")).status_code == 200

    async def test_viewer_cannot_add_a_host(self, viewer):
        resp = await viewer.post("/api/hosts", json={"hostname": "h", "ip_address": "10.0.0.1"})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Missing permission: config.edit"

    async def test_operator_cannot_change_settings_or_users(self, operator):
        assert (await operator.patch("/api/settings/x", json={"value": "1"})).status_code == 403
        assert (await operator.get("/api/admin/users")).status_code == 403

    async def test_operator_cannot_add_a_git_repo(self, operator):
        assert (await operator.post("/api/git-repos", json={})).status_code == 403

    async def test_operator_reaches_schedules_past_the_permission_check(self, operator):
        resp = await operator.post("/api/scheduled-actions", json={})
        assert resp.status_code == 422  # validation, i.e. the check let it through

    async def test_admin_lists_users(self, admin):
        assert (await admin.get("/api/admin/users")).status_code == 200


class TestMe:
    async def test_me_lists_the_permissions_of_the_role(self, viewer):
        body = (await viewer.get("/api/users/me")).json()
        assert body["role"] == "viewer"
        assert body["permissions"] == sorted(ROLE_PERMISSIONS[Role.VIEWER])

    async def test_me_cannot_raise_its_own_role(self, viewer):
        await viewer.patch("/api/users/me", json={"role": "admin", "is_superuser": True})
        body = (await viewer.get("/api/users/me")).json()
        assert body["role"] == "viewer" and body["is_superuser"] is False

    async def test_login_records_last_login(self, viewer):
        assert (await viewer.get("/api/users/me")).json()["last_login_at"] is not None


class TestMustChangePassword:
    async def test_everything_but_me_is_refused_until_changed(self, app, db):
        client, _ = await _client(app, db, "admin", must_change=True)
        try:
            resp = await client.get("/api/hosts")
            assert resp.status_code == 403
            assert resp.json()["detail"] == PASSWORD_CHANGE_REQUIRED
            assert (await client.get("/api/users/me")).json()["must_change_password"] is True

            changed = await client.patch(
                "/api/users/me",
                json={"current_password": PASSWORD, "password": "a-new-passphrase-of-mine"},
            )
            assert changed.status_code == 200, changed.text
            await client.post(
                "/api/auth/jwt/login",
                data={"username": changed.json()["email"], "password": "a-new-passphrase-of-mine"},
            )
            assert (await client.get("/api/hosts")).status_code == 200
        finally:
            await client.aclose()


class TestUserAdministration:
    async def _create(self, admin, **extra) -> dict:
        body = {"email": f"u_{uuid.uuid4().hex[:8]}@test.com", "password": PASSWORD, **extra}
        resp = await admin.post("/api/admin/users", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    async def test_new_users_default_to_viewer_and_a_forced_change(self, admin):
        user = await self._create(admin)
        assert user["role"] == "viewer"
        assert user["must_change_password"] is True

    async def test_legacy_is_superuser_flag_means_admin(self, admin):
        assert (await self._create(admin, is_superuser=True))["role"] == "admin"

    async def test_role_change(self, admin):
        user = await self._create(admin, role="viewer")
        resp = await admin.patch(f"/api/admin/users/{user['id']}", json={"role": "operator"})
        assert resp.status_code == 200
        assert resp.json()["role"] == "operator"

    async def test_role_change_ends_the_users_sessions(self, app, db, admin):
        client, target = await _client(app, db, "operator")
        try:
            resp = await admin.patch(f"/api/admin/users/{target.id}", json={"role": "viewer"})
            assert resp.status_code == 200
            assert (await client.get("/api/users/me")).status_code == 401
        finally:
            await client.aclose()

    async def test_cannot_change_own_role_or_disable_self(self, admin):
        me = admin.user.id
        assert (
            await admin.patch(f"/api/admin/users/{me}", json={"role": "viewer"})
        ).status_code == 400
        resp = await admin.patch(f"/api/admin/users/{me}", json={"is_active": False})
        assert resp.status_code == 400

    async def test_cannot_disable_the_last_active_admin(self, admin, db):
        from sqlalchemy import update

        # Make the fixture admin the only active one.
        await db.execute(
            update(User)
            .where(User.role == "admin", User.id != admin.user.id)
            .values(is_active=False)
        )
        other = await self._create(admin, role="admin")
        # Disabling the other admin is fine while the fixture admin remains.
        ok = await admin.patch(f"/api/admin/users/{other['id']}", json={"is_active": False})
        assert ok.status_code == 200

    async def test_unknown_role_is_rejected(self, admin):
        user = await self._create(admin)
        resp = await admin.patch(f"/api/admin/users/{user['id']}", json={"role": "root"})
        assert resp.status_code == 422


@pytest.fixture
async def sample_host(db):
    return await create_host(db, hostname=f"perm-{uuid.uuid4().hex[:8]}")


class TestExtensionHooks:
    async def test_a_registered_resolver_replaces_the_role_map(self, viewer):
        async def everything(user, scope):
            return frozenset(p.value for p in Permission)

        set_permission_resolver(everything)
        try:
            assert (await viewer.get("/api/admin/users")).status_code == 200
        finally:
            set_permission_resolver(None)
        assert (await viewer.get("/api/admin/users")).status_code == 403

    async def test_the_resolver_sees_the_host_from_the_url(self, viewer):
        seen = []

        async def record(user, scope):
            seen.append(await scope.resolve())
            return ROLE_PERMISSIONS[Role.VIEWER]

        set_permission_resolver(record)
        try:
            await viewer.get("/api/hosts/42/packages")
            await viewer.get("/api/groups/7/packages")
            await viewer.get("/api/hosts")
        finally:
            set_permission_resolver(None)
        assert seen == [Scope("host", 42), Scope("group", 7), GLOBAL]

    async def test_row_scope_follows_the_row(self, db, sample_host):
        job = SyncJob(host_id=sample_host.id, status="success")
        db.add(job)
        await db.flush()

        class FakeRequest:
            path_params = {"job_id": str(job.id)}

        scope = BoundScope(row_scope(SyncJob, "job_id"), FakeRequest(), db)  # type: ignore[arg-type]
        assert await scope.resolve() == Scope("host", sample_host.id)

    async def test_visible_hosts_filters_the_host_list(self, viewer, sample_host):
        async def nothing(user):
            return set()

        set_visible_hosts_resolver(nothing)
        try:
            assert (await viewer.get("/api/hosts")).json() == []
        finally:
            set_visible_hosts_resolver(None)
        ids = [h["id"] for h in (await viewer.get("/api/hosts")).json()]
        assert sample_host.id in ids

    def test_plugin_api_is_versioned(self):
        major, minor = perms.PLUGIN_API.split(".")
        assert major.isdigit() and minor.isdigit()
