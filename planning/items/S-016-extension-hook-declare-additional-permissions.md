# S-016: Extension hook: declare additional permissions

| | |
|---|---|
| Edition | shared |
| Source | roadmap, found while writing the stage 5 specs (no board item) |
| Tier / score | P0 / 12.0 |
| Stage | 1 |
| Effort | S |
| Autonomy | B |
| Depends on | none |

## Goal

An installed extension can declare a permission of its own, and its routes can ask for
it with `Depends(require("..."))` and `require_on()` exactly as core routes do. A
Community install with no extension has the same catalog and the same role mapping as
before. Enterprise maintenance windows will be the first user.

## Scope

In scope:
- `register_permission()`, `known_permissions()` and a reset function for tests in
  `backend/app/auth/permissions.py`.
- `require()` and `require_on()` accepting a registered name.
- Which of the three fixed roles hold a registered permission when no other resolver is
  installed.
- A `PLUGIN_API` minor bump, `CLAUDE.md`, the plugin loader's docstring.

Out of scope:
- Any new core permission, and any change to what the three roles may do today.
- Removing or renaming a core permission from an extension.
- Grant storage, scoping rules or a UI for extension permissions. A resolver installed
  with `set_permission_resolver()` owns those.
- Frontend changes: `useCan()` already takes any string (`frontend/lib/auth.ts`, line 55).

## Context

- `backend/app/auth/permissions.py`: `class Permission(StrEnum)` (line 82) is the closed
  catalog. `require()` (line 362) does `str(Permission(permission))`, so "an unknown name
  fails at import"; `require_on()` (line 325) does the same at line 341.
- `ROLE_PERMISSIONS` (line 117) is built once from `_VIEWER`, `_OPERATOR` and
  `_ADMIN = frozenset(Permission)`. `role_permissions()` (line 236), the Community
  resolver, reads it on every check.
- `backend/tests/test_permissions.py` asserts
  `ROLE_PERMISSIONS[Role.ADMIN] == {p.value for p in Permission}` (line 148), finds each
  route's permission through `dependency.required_permission` (line 108), and checks
  that every route refuses a role that lacks it (line 249). That test already covers
  routes an extension adds, because it walks the real app.
- `GET /api/users/me` (`backend/app/api/auth_setup.py`, line 112) returns whatever the
  resolver answers, so a registered permission reaches the frontend with no change.
- A route module evaluates `require("...")` when it is imported. An extension that
  imports its router at the top of its package would therefore ask for its permission
  before its `register(app)` has run.
- Extensions load in the API process and in Celery workers (`backend/app/plugins.py`,
  `backend/app/tasks/__init__.py` line 126), so registration runs more than once per
  install and must be repeatable.
- `PLUGIN_API = "1.2"` (line 71).

## Dependencies

- No item dependency and no new package.
- S-001, S-002 and S-015 also bump `PLUGIN_API` in stage 1. Each takes the next free
  minor at the time it merges.

## Implementation steps

1. In `backend/app/auth/permissions.py`, below `ROLE_PERMISSIONS`:
   - `_extension_permissions: dict[str, frozenset[Role]]`.
   - `register_permission(name: str, *, roles: Iterable[Role | str] = (Role.ADMIN,))`.
     The name must match `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$` and must not be a core
     name; otherwise `ValueError`. `roles` says which fixed roles hold it under the
     Community resolver; `admin` is always included, so the invariant "admin holds
     everything" stays true. Registering the same name again with the same roles is a
     no-op; with different roles it raises `ValueError`.
   - The function rebuilds the affected entries of `ROLE_PERMISSIONS` (new frozensets,
     assigned into the same dict, so importers that hold the dict see the change).
   - `known_permissions() -> frozenset[str]`: core names plus registered ones.
   - `reset_extension_permissions()` for tests: clears the registry and restores
     `ROLE_PERMISSIONS` to the core mapping.
   - `_checked(permission) -> str`: returns the name if it is in `known_permissions()`,
     else raises `ValueError` naming it and saying that an extension must call
     `register_permission()` before importing the module that uses it.
2. Replace `str(Permission(permission))` in `require()` and `require_on()` with
   `_checked(permission)`. Core behaviour is unchanged: an unknown name still fails when
   the route module is imported.
3. Bump `PLUGIN_API` to the next free minor and add the comment line:
   "`register_permission`, `known_permissions` (an extension declares permissions of its
   own; register before importing the routes that require them)."
4. `CLAUDE.md`: in "Access control", one sentence that the catalog can be extended and
   that `known_permissions()` is the full list. In "Extensions (plugins) and directory
   sign-in", one bullet for the hook with the ordering rule from step 1. Update the
   `backend/app/plugins.py` docstring, which lists the hooks a `register` may call.
5. Tests, in a new `backend/tests/test_extension_permissions.py`. A stand-in extension in
   the test module (a `register(app)` with `requires_plugin_api`, loaded with
   `load_plugins(app, [EntryPoint(...)])` as in `tests/test_external_auth.py`, line 315)
   calls `register_permission("standin.override", roles=("admin", "operator"))` and then
   adds a router with one route behind `require("standin.override")` and one that calls
   `require_on(user, "standin.override", hosts=[...])`. A fixture resets the registry
   before and after each test. Reuse `_client` from `tests/test_permissions.py` and the
   `Grant` stand-in resolver from `tests/test_permission_scopes.py`.
6. Change the assertion at `tests/test_permissions.py:148` to compare with
   `known_permissions()`, so it holds whether or not an extension is installed.
7. Ask for review before merge (class B). The reviewer checks the naming rule and the
   role default.

## Acceptance criteria

- With the stand-in loaded, an admin and an operator reach its route, a viewer gets 403
  with `Missing permission: standin.override`, and `GET /api/users/me` lists the
  permission for the admin and the operator only.
- With the `Grant` resolver installed and granting `standin.override` on one host,
  `require_on(user, "standin.override", hosts=[that host])` passes and another host is
  refused with 403.
- `require("standin.unknown")` raises `ValueError` at definition time, with a message
  that names `register_permission`.
- `register_permission("hosts.view")`, `register_permission("Bad Name")` and a second
  registration with different roles each raise `ValueError`; an identical second
  registration does not.
- After `reset_extension_permissions()`, `ROLE_PERMISSIONS` equals the core mapping and
  `known_permissions()` equals `{p.value for p in Permission}`.
- `tests/test_permissions.py` passes unchanged apart from step 6, including the sweep
  that every route declares one permission.
- `PLUGIN_API` is one minor higher (or more), and `is_compatible("1.2")` is still true.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_extension_permissions.py tests/test_permissions.py tests/test_permission_scopes.py tests/test_external_auth.py -q`
- `ruff check . && ruff format --check .`
- The full suite runs once before the stage review.
- Nothing here needs a running stack or a managed host.

## Docs and changelog

- `CLAUDE.md` and the `app/plugins.py` docstring as in step 4. There is no extension
  developer page under `docs/` at the time of writing; if one exists by then, add the
  hook there.
- `CHANGELOG.md`, `[Unreleased]`, under "Added": "Extension hook for permissions. An
  extension can declare permissions of its own for its routes. The built-in catalog and
  roles are unchanged. Plugin API minor bump."

## Risks and rollback

- Order of registration. A route module imported before `register_permission()` fails
  with the message from step 1, the plugin loader logs it and skips the plugin
  (`load_plugins` catches import and register errors), and the install still starts.
- Two extensions choosing the same name with different roles: the second raises and is
  skipped. The naming rule asks for an area prefix of the extension's own.
- A registration that leaks between tests would change `ROLE_PERMISSIONS` for the rest
  of the suite. The fixture in step 5 resets it, and step 6 keeps the admin invariant
  honest either way.
- Rollback: revert the commit and set `PLUGIN_API` back. An extension built against the
  new minor is then skipped at load with a log line.

## Assumptions

- Assumption (not yet confirmed): a registered permission goes to `admin` only unless
  the extension names more roles. Question for Dennis: should `operator` be the default
  instead, given that operators run most things in Community?
- Assumption (not yet confirmed): `Permission` stays a closed enum for core names and
  extension names live beside it, so core code that iterates `Permission` keeps its
  meaning. Extensions that list the catalog should move to `known_permissions()`.
- Assumption (not yet confirmed): no description or label is stored with a permission.
  An extension that shows its permissions in a UI carries its own texts.
- The minor bump follows the rule in `CLAUDE.md`: additions bump the minor.

## Definition of done

- [ ] `register_permission`, `known_permissions`, the reset function and the `require` /
      `require_on` change are in place.
- [ ] `tests/test_extension_permissions.py` passes; the permission and plugin tests
      still pass.
- [ ] `PLUGIN_API` bumped with its comment; `CLAUDE.md`, the `plugins.py` docstring and
      `CHANGELOG.md` updated.
- [ ] Reviewed before merge.
- [ ] `planning/roadmap.yaml`: S-016 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
