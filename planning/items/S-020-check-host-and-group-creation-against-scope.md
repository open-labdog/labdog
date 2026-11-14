# S-020: Check host and group creation against scope

| | |
|---|---|
| Edition | shared |
| Source | roadmap, found while writing the stage 9 specs (no board item) |
| Tier / score | P2 / 10.0 |
| Stage | 9 |
| Effort | S |
| Autonomy | B |
| Depends on | none |

## Goal

Creating a host checks the right to edit configuration on the groups the new host joins,
the way every other route with targets in its body already does, and creating a group is
pinned as an install-wide right. In Community nothing changes for any role. With a
resolver that grants rights per group (Enterprise scoped roles for teams), a user who may
edit one group can add a host to it and to no other.

## Scope

In scope:
- `POST /api/hosts` (`create_host`): scope check on `group_ids`, made before any SSH
  contact.
- `POST /api/discovery/add-hosts` (`add_discovered_hosts`): the same check on its
  `group_ids`.
- `POST /api/groups` (`create_group`): stays a global check, stated in code and pinned
  by a test.
- A 409 for a hostname that is taken, with a text that names no other object.
- The "Add host" entry points in the frontend for a user whose rights are scoped.
- A `PLUGIN_API` minor bump, `CLAUDE.md`.

Out of scope:
- Letting a scoped user create groups, reserving priority ranges, or making group names
  or hostnames unique per team. Names and priorities stay unique across the install.
- Making `discovery.run` or `integrations.manage` grantable per group.
- Any resolver. Community's resolver ignores scope and keeps doing so.
- Which SSH keys a user may see or use.

## Context

- The rule is in `CLAUDE.md`, "Access control": targets in the request body are checked
  in the handler with `require_on(user, perm, hosts=[...], groups=[...])`, all or
  nothing; list routes use `scope=anywhere`; anything that changes other hosts' outcome
  asks `require_on(user, perm)` with no targets, a global check.
- `backend/app/api/hosts.py`, `create_host` (line 140): `Depends(require("config.edit"))`
  with the default path scope. The path has no `{host_id}`, so the scope is global and a
  user who holds `config.edit` on one group only is refused. `HostCreate.group_ids`
  (`backend/app/schemas/hosts.py`, line 62) names the groups, and the handler inserts
  the memberships at line 210 without asking about them.
- The same handler opens an SSH connection to `body.ip_address` with the named key when
  no hostname is given (line 160), before anything about the groups is checked.
- `hosts.hostname` is unique (`backend/app/models/host.py`, line 57). `create_host` has
  no check for it, so a duplicate surfaces as a database error instead of a 409.
- `update_host` in the same file already does it right: line 377 calls
  `require_on(user, "config.edit", groups=...)` for the groups that change.
- `backend/app/api/groups.py`, `create_group` (line 63): `require("config.edit")`,
  global because the path has no id. Its 409 texts ("Group name already exists", "Group
  priority already in use") name no other object. `update_group` (line 220) calls
  `require_on(user, "config.edit")` for a priority change, with the reason in a comment:
  a priority decides merges on every host the group shares.
- `backend/app/api/discovery.py`, `add_discovered_hosts` (line 130):
  `require("discovery.run")`, and `BulkAddRequest.group_ids`
  (`backend/app/schemas/discovery.py`, line 38) is not checked.
- `require_on()` (`backend/app/auth/permissions.py`, line 325) with no targets checks the
  global scope, so one call covers "these groups" and "no group named".
- Frontend: `frontend/app/(dashboard)/hosts/client-page.tsx` (line 277) links to
  `/hosts/new` for everyone. `frontend/app/(dashboard)/hosts/new/page.tsx` treats groups
  as optional (line 81). `frontend/lib/auth.ts` has `canAnywhere()` (line 33) and
  `isScoped()` (line 38).
- `backend/tests/test_permission_scopes.py` registers a stand-in scoped resolver
  (`Grant`, line 34, with the `scoped` fixture at line 66) and pins what the core
  promises such a resolver. `TestBodyTargets` already covers moving a host between
  groups (line 152) and a priority change (line 158).

## Dependencies

- No item dependency and no new package.

## Implementation steps

1. `backend/app/api/hosts.py`, `create_host`:
   - Change the dependency to `require("config.edit", scope=anywhere)`.
   - As the first statement of the handler, before the key lookup and before any SSH
     contact: `await require_on(user, "config.edit", groups=body.group_ids)`. With no
     group named this is a global check, so only a user who may edit everywhere can add
     an ungrouped host. Say so in a comment.
   - Turn a duplicate hostname into `409` with the detail "A host with this hostname
     already exists": a pre-check before the insert, and an `IntegrityError` catch
     around the flush for the race, as `_flush_or_conflict` does in `groups.py` (line
     28). The text is the same whether or not the caller may see the other host.
2. `backend/app/api/discovery.py`, `add_discovered_hosts`: when `body.group_ids` is not
   empty, `await require_on(user, "config.edit", groups=body.group_ids)` before the key
   is loaded (rename the `_` parameter to `user`). The route keeps
   `require("discovery.run")`.
3. `backend/app/api/groups.py`, `create_group`: add `await require_on(user, "config.edit")`
   as the first statement with a comment that mirrors the one in `update_group`: a new
   group takes a priority that decides merges on shared hosts, so creating one needs the
   right everywhere. The dependency already checks globally; the explicit call keeps
   that true if the route's scope is ever changed.
4. Frontend: in `hosts/client-page.tsx`, show "Add host" only when
   `canAnywhere(user, "config.edit")`. In `hosts/new/page.tsx`, when `isScoped(user)` and
   the user lacks global `config.edit`, require at least one group and show one line of
   help ("Choose a group you may edit"). The group list already comes from
   `GET /api/groups`, which returns only visible groups. Show the API's 403 detail as the
   form error.
5. Bump `PLUGIN_API` to the next free minor and add the comment line:
   "`POST /api/hosts` and `POST /api/discovery/add-hosts` check `config.edit` on the
   groups in the body (`require_on`); host creation is an `any`-scope route; group
   creation is a global check."
6. `CLAUDE.md`, "Access control": add host creation to the "Targets in the request body"
   bullet and group creation to the "Anything that changes other hosts' outcome" bullet.
7. Tests, in `backend/tests/test_permission_scopes.py`, class `TestBodyTargets`, using
   the existing `scoped` fixture and `world`: see Acceptance criteria. Add one Community
   test in `backend/tests/test_permissions.py` or the host API tests that an operator
   still creates a host with and without groups, and a viewer is refused.
8. Ask for review before merge (class B): this changes a permission check on routes that
   write.

## Acceptance criteria

- With the stand-in scoped resolver (rights on `g_in` only):
  `POST /api/hosts` with `group_ids: [g_in]` returns 201; with `[g_out]`, with
  `[g_in, g_out]` and with `[]` it returns 403, no host row is created, and the detail
  names the refused target (`group <id>` or `global`).
- In the refused cases no SSH connection is attempted (assert that `ssh_connect` is not
  called when the hostname is empty and a key is named).
- With the same resolver, `POST /api/groups` returns 403, and `POST /api/discovery/add-hosts`
  with `group_ids: [g_out]` is refused for a user who holds `discovery.run` globally and
  `config.edit` on `g_in` only.
- In Community (no resolver registered): an operator and an admin create a host with
  groups, without groups, and a group, exactly as before; a viewer gets 403 on all three.
- Creating a second host with an existing hostname returns 409 with the fixed text, for
  a caller who cannot see the first host as well as for one who can.
- `tests/test_permissions.py` passes: every route still declares exactly one permission
  and refuses a role without it.
- A scoped user sees "Add host", must pick a group, and gets the host; a viewer does not
  see the button.
- `PLUGIN_API` is one minor higher (or more), and `is_compatible("1.2")` is still true.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_permission_scopes.py tests/test_permissions.py tests/test_groups.py -q`,
  plus the host API test file the new 409 test goes into.
- `ruff check . && ruff format --check .`
- `cd frontend && npx tsc --noEmit && npm run lint && npx playwright test hosts`
- The full backend suite runs once before the stage review.
- Nothing here needs a managed host: host creation with a given hostname does no SSH.
  The scoped-user path through the UI needs a scoping resolver, so it is checked by the
  stage review on local Docker on lin-manager-2 with the combined build.

## Docs and changelog

- `CLAUDE.md` as in step 6. `docs/ui/` page for hosts (the "Add host" part): one
  sentence that a host with no group can only be added by someone who may edit
  configuration everywhere.
- `CHANGELOG.md`, `[Unreleased]`, under "Changed": "Creating a host checks the right to
  edit configuration on the groups it joins, and a taken hostname answers 409 instead of
  an error. Nothing changes for the built-in roles. Plugin API minor bump."

## Risks and rollback

- This loosens the gate on a route that writes: with a scoping resolver, a user without
  global `config.edit` can now create hosts. The all-or-nothing `require_on`, the
  ungrouped-host rule and the tests in step 7 bound it. In Community the answer is the
  same as before for every role, because the resolver ignores scope.
- A scoped user can still name any SSH key id and any address. Keys are visible to
  anyone who may view hosts, as today; this item does not widen that, and it moves the
  permission check ahead of the SSH contact.
- A 409 on a hostname tells a scoped user that the name exists somewhere in the install.
  That follows from hostnames being unique across the install and cannot be hidden
  without changing the model; the text gives no more than that.
- Rollback: revert the commit and set `PLUGIN_API` back. Host creation is a global check
  again.

## Assumptions

- Assumption (not yet confirmed): group creation and priority changes stay install-wide
  rights. Question for Dennis: should a user with rights on some groups be able to
  create groups at all?
- Assumption (not yet confirmed): an ungrouped host needs global `config.edit`.
- Assumption (not yet confirmed): the bulk-add route keeps `discovery.run` as its one
  declared permission and checks `config.edit` on the groups in the handler, like
  `update_host` does for moved hosts.
- Assumption (not yet confirmed): the change counts as an addition to the extension
  contract (a resolver is now asked about host creation), so it bumps the minor.

## Definition of done

- [ ] The three handlers, the 409 and the frontend change are in place.
- [ ] New cases in `tests/test_permission_scopes.py` pass; the permission, group and
      host tests still pass; frontend type check and lint pass.
- [ ] `PLUGIN_API` bumped with its comment; `CLAUDE.md`, the hosts docs page and
      `CHANGELOG.md` updated.
- [ ] Reviewed before merge.
- [ ] `planning/roadmap.yaml`: S-020 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
