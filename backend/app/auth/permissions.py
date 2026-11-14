"""Who may do what: the permission catalog, the fixed roles, and ``require()``.

Every API route declares the one permission it needs with
``Depends(require("sync.run"))``. Endpoints never test a role by name.
That is the whole point of the module: the Community edition maps three
fixed roles onto the catalog below, and an extension (RBAC in the
Enterprise edition) can replace that mapping, and scope it to hosts and
groups, through :func:`set_permission_resolver` without touching a
single route.

The catalog, :func:`require`, :class:`Scope` and the two ``set_*``
hooks are a public contract for extensions. :data:`PLUGIN_API` names its
version; bump the major part when a change would break an extension that
was written against the previous one.

Scope
-----
Each check carries a lazily resolved :class:`BoundScope`. By default it
comes from the URL: ``{host_id}`` gives a host scope, ``{group_id}`` a
group scope, anything else the global scope. Routes that address a row
belonging to a host or group (a sync job, an action run) pass
``scope=row_scope(Model, "param")``. Resolving a scope can cost a query,
so nothing resolves it unless a resolver asks; the Community resolver
never does.

Routes whose target is in the request body (starting an action run,
adding hosts to a group) cannot be scoped from the URL. They are checked
globally by ``require()``, which only says the user may do this
*somewhere*; the handler then calls :func:`require_on` with the hosts and
groups it read from the body.

List and catalog routes (the host list, the action catalog, the runs
list) pass ``scope=anywhere``: the user needs the permission on at least
one host or group, and the handler narrows what it returns through
:func:`visible_host_ids` / :func:`visible_group_ids`. A resolver that
scopes grants answers an :data:`ANYWHERE` check with the union of
everything the user holds anywhere.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.users import current_active_user
from app.db import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

#: Version of the extension contract in this module. ``major.minor``:
#: minor for additions, major for anything that could break an extension.
#:
#: 1.0  permission catalog, ``require()``, the two ``set_*_resolver`` hooks.
#: 1.1  ``app.plugins`` entry-point loading (``unbork.plugins`` and
#:      ``unbork.migrations``), ``app.auth.external`` (login
#:      providers, ``resolve_external_user``, ``issue_session``) and the
#:      ``user_identities`` table.
#: 1.2  the ``any`` scope (:data:`ANYWHERE`, :func:`anywhere`) for list and
#:      catalog routes, :func:`require_on` for targets read from a request
#:      body, ``set_visible_groups_resolver`` / :func:`visible_group_ids`,
#:      :func:`visible_filter`, :func:`param_scope` for ``?host_id=`` style routes, and
#:      ``GET /api/users/me/permissions`` for one host or group.
PLUGIN_API = "1.2"


class Role(StrEnum):
    """The three fixed roles. ``users.role`` may also be NULL: no role."""

    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Permission(StrEnum):
    """Everything a route can ask for. Dotted, ``<area>.<verb>``."""

    HOSTS_VIEW = "hosts.view"
    AUDIT_VIEW = "audit.view"
    CONFIG_EDIT = "config.edit"
    SYNC_RUN = "sync.run"
    DISCOVERY_RUN = "discovery.run"
    ACTIONS_RUN = "actions.run"
    SCHEDULES_MANAGE = "schedules.manage"
    TERMINAL_OPEN = "terminal.open"
    AI_USE = "ai.use"
    AI_APPROVE = "ai.approve"
    ALERTS_MANAGE = "alerts.manage"
    INTEGRATIONS_MANAGE = "integrations.manage"
    SETTINGS_MANAGE = "settings.manage"
    USERS_MANAGE = "users.manage"


_VIEWER = frozenset({Permission.HOSTS_VIEW, Permission.AUDIT_VIEW})
_OPERATOR = _VIEWER | {
    Permission.CONFIG_EDIT,
    Permission.SYNC_RUN,
    Permission.DISCOVERY_RUN,
    Permission.ACTIONS_RUN,
    Permission.SCHEDULES_MANAGE,
    Permission.TERMINAL_OPEN,
    Permission.AI_USE,
    Permission.AI_APPROVE,
    Permission.ALERTS_MANAGE,
}
_ADMIN = frozenset(Permission)

#: The fixed role → permission mapping. The only place a role name means
#: anything.
ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset(str(p) for p in _VIEWER),
    Role.OPERATOR: frozenset(str(p) for p in _OPERATOR),
    Role.ADMIN: frozenset(str(p) for p in _ADMIN),
}


# ── Scope ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Scope:
    """What a check is about: ``global``, or one host or group."""

    kind: str  # "global" | "host" | "group"
    id: int | None = None


GLOBAL = Scope("global")
#: "On at least one host or group". The route filters what it returns.
ANYWHERE = Scope("any")

#: Turns a request into a :class:`Scope`. Called at most once per check,
#: and only when a resolver asks.
ScopeSpec = Callable[[Request, AsyncSession], Awaitable[Scope]]


async def path_scope(request: Request, _db: AsyncSession) -> Scope:
    """The default: ``{host_id}`` or ``{group_id}`` in the URL, else global."""
    params = request.path_params
    for kind in ("host", "group"):
        raw = params.get(f"{kind}_id")
        if raw is not None:
            try:
                return Scope(kind, int(raw))
            except (TypeError, ValueError):
                return GLOBAL
    return GLOBAL


async def anywhere(_request: Request, _db: AsyncSession) -> Scope:
    """Scope spec for list and catalog routes: :data:`ANYWHERE`."""
    return ANYWHERE


def param_scope(kind: str, param: str) -> ScopeSpec:
    """Scope from a path or query parameter, for ``?host_id=`` style routes.

    A missing or malformed value is global, which a scoping resolver then
    refuses unless the user holds the permission everywhere.
    """

    async def resolve(request: Request, _db: AsyncSession) -> Scope:
        raw = request.path_params.get(param, request.query_params.get(param))
        try:
            return Scope(kind, int(raw)) if raw is not None else GLOBAL
        except (TypeError, ValueError):
            return GLOBAL

    resolve.__name__ = f"param_scope_{kind}_{param}"
    return resolve


def row_scope(model: Any, param: str) -> ScopeSpec:
    """Scope of a row that belongs to a host or group, addressed by ``{param}``.

    The row's ``host_id`` wins over its ``group_id``. A missing row, or a
    row with neither, is global, and the handler then answers 404 as usual.
    """

    async def resolve(request: Request, db: AsyncSession) -> Scope:
        try:
            row = await db.get(model, int(request.path_params[param]))
        except (KeyError, TypeError, ValueError):
            return GLOBAL
        if row is None:
            return GLOBAL
        if getattr(row, "host_id", None) is not None:
            return Scope("host", row.host_id)
        if getattr(row, "group_id", None) is not None:
            return Scope("group", row.group_id)
        return GLOBAL

    resolve.__name__ = f"row_scope_{getattr(model, '__name__', 'model')}_{param}"
    return resolve


@dataclass
class BoundScope:
    """A scope tied to one request; :meth:`resolve` runs the spec once."""

    spec: ScopeSpec | None
    request: Request | None = None
    db: AsyncSession | None = None
    _resolved: Scope | None = field(default=None, init=False, repr=False)

    async def resolve(self) -> Scope:
        if self._resolved is None:
            if self.spec is None or self.request is None or self.db is None:
                self._resolved = GLOBAL
            else:
                self._resolved = await self.spec(self.request, self.db)
        return self._resolved

    @classmethod
    def of(cls, scope: Scope) -> BoundScope:
        """An already-known scope, for :func:`authorize` from a handler."""
        bound = cls(spec=None)
        bound._resolved = scope
        return bound


# ── Resolvers (the extension points) ────────────────────────────────────────

PermissionResolver = Callable[[User, BoundScope | None], Awaitable[frozenset[str]]]
VisibleHostsResolver = Callable[[User], Awaitable[set[int] | None]]
VisibleGroupsResolver = Callable[[User], Awaitable[set[int] | None]]


async def role_permissions(user: User, scope: BoundScope | None = None) -> frozenset[str]:
    """Community resolver: the user's fixed role, scope ignored.

    A NULL role grants nothing. That is what an account whose rights come
    only from elsewhere (RBAC bindings, a directory group) looks like when
    nothing elsewhere is installed.
    """
    try:
        role = Role(user.role) if user.role else None
    except ValueError:
        logger.warning("user %s has unknown role %r; granting nothing", user.id, user.role)
        return frozenset()
    return ROLE_PERMISSIONS.get(role, frozenset()) if role else frozenset()


async def all_hosts_visible(user: User) -> set[int] | None:
    """Community: every user who may view hosts sees all of them."""
    return None


async def all_groups_visible(user: User) -> set[int] | None:
    """Community: every user who may view hosts sees every group."""
    return None


_permission_resolver: PermissionResolver = role_permissions
_visible_hosts_resolver: VisibleHostsResolver = all_hosts_visible
_visible_groups_resolver: VisibleGroupsResolver = all_groups_visible


def set_permission_resolver(resolver: PermissionResolver | None) -> None:
    """Replace how permissions are computed. ``None`` restores the default."""
    global _permission_resolver
    _permission_resolver = resolver or role_permissions


def set_visible_hosts_resolver(resolver: VisibleHostsResolver | None) -> None:
    """Replace which hosts a user's lists show. ``None`` restores the default."""
    global _visible_hosts_resolver
    _visible_hosts_resolver = resolver or all_hosts_visible


def set_visible_groups_resolver(resolver: VisibleGroupsResolver | None) -> None:
    """Replace which groups a user's lists show. ``None`` restores the default."""
    global _visible_groups_resolver
    _visible_groups_resolver = resolver or all_groups_visible


async def permissions_for(user: User, scope: BoundScope | None = None) -> frozenset[str]:
    """What *user* may do, globally or within *scope*."""
    if not user.is_active:
        return frozenset()
    return await _permission_resolver(user, scope)


async def visible_host_ids(user: User) -> set[int] | None:
    """Host ids *user*'s lists may show, or ``None`` for all of them."""
    return await _visible_hosts_resolver(user)


async def visible_group_ids(user: User) -> set[int] | None:
    """Group ids *user*'s lists may show, or ``None`` for all of them."""
    return await _visible_groups_resolver(user)


async def visible_filter(user: User, *, host_col: Any = None, group_col: Any = None) -> Any:
    """A WHERE clause keeping rows on hosts or groups *user* may see.

    ``None`` when nothing needs filtering (Community, or a user who sees
    everything). A row matches when its host is visible or its group is;
    a row with neither (a global operation) is hidden from a user whose
    view is limited.
    """
    hosts = await visible_host_ids(user) if host_col is not None else None
    groups = await visible_group_ids(user) if group_col is not None else None
    if hosts is None and groups is None:
        return None
    clauses = []
    if host_col is not None:
        clauses.append(host_col.in_(hosts) if hosts is not None else host_col.is_not(None))
    if group_col is not None:
        clauses.append(group_col.in_(groups) if groups is not None else group_col.is_not(None))
    return or_(*clauses)


async def authorize(user: User, permission: str, scope: BoundScope | None = None) -> bool:
    return str(permission) in await permissions_for(user, scope)


async def require_on(
    user: User,
    permission: str | Permission,
    *,
    hosts: Iterable[int] = (),
    groups: Iterable[int] = (),
) -> None:
    """Refuse unless *user* holds *permission* on every host and group named.

    For routes whose targets come from the request body. All or nothing:
    one target the user may not touch refuses the whole request, and the
    403 names the targets that failed, so a bulk request is never quietly
    narrowed to the part the user happened to be allowed.

    With no targets this is a global check.
    """
    perm = str(Permission(permission))
    targets = [Scope("host", int(h)) for h in dict.fromkeys(hosts)] + [
        Scope("group", int(g)) for g in dict.fromkeys(groups)
    ]
    if not targets:
        targets = [GLOBAL]
    denied = [t for t in targets if not await authorize(user, perm, BoundScope.of(t))]
    if denied:
        named = ", ".join(t.kind if t.id is None else f"{t.kind} {t.id}" for t in denied)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {perm} on {named}"
        )


# ── The dependency ──────────────────────────────────────────────────────────

#: Answer for a user whose password an administrator set. Everything but
#: ``/api/users/me`` (and logging out) is refused until they change it.
PASSWORD_CHANGE_REQUIRED = "password_change_required"  # nosec B105 - an error code


def require(permission: str | Permission, *, scope: ScopeSpec | None = None):
    """FastAPI dependency: the signed-in user, if they hold *permission*.

    Returns the ``User`` so it drops in where ``current_active_user`` was.
    """
    perm = str(Permission(permission))  # an unknown name fails at import
    spec = scope or path_scope

    async def dependency(
        request: Request,
        user: User = Depends(current_active_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if user.must_change_password and user.hashed_password:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=PASSWORD_CHANGE_REQUIRED)
        if not await authorize(user, perm, BoundScope(spec, request, db)):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {perm}")
        return user

    dependency.required_permission = perm  # type: ignore[attr-defined]
    dependency.__name__ = f"require_{perm.replace('.', '_')}"
    return dependency
