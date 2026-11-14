# S-019: Extension hook: add a probe to host state collection

| | |
|---|---|
| Edition | shared |
| Source | roadmap, found while writing the stage 9 specs (no board item) |
| Tier / score | P2 / 8.0 |
| Stage | 9 |
| Effort | S |
| Autonomy | B |
| Depends on | none |

## Goal

An installed extension can have a read-only probe of its own run on a host whenever a
full state collection runs, over the SSH connection that collection already has open. A
probe that fails or hangs never changes the outcome of the collection. A Community
install with no extension collects exactly as before. Enterprise rollback on hosts that
are not Proxmox guests will be the first user.

## Scope

In scope:
- `register_host_probe()` and a reset function for tests in a new `app.hosts.probes`
  module.
- Running registered probes from `collect_module_state()` on a full collection, each
  isolated by a savepoint and a time limit.
- A `PLUGIN_API` minor bump, `CLAUDE.md`, the plugin loader's docstring.

Out of scope:
- Any probe. Community registers none, and the seven module collectors are unchanged.
- A place to store probe results. A probe writes to its extension's own tables through
  the session it is given.
- Adding a module to `COLLECTABLE_MODULES`, drift checks or sync for extension data.
- Showing probe results or probe failures in the UI.
- Running probes on single-module collections or on drift sweeps.

## Context

- `backend/app/api/host_state.py`: `collect_module_state(host_id, module, db)` (line 198)
  loads the host and key, builds the collectors with `_build_collectors()` (line 594, a
  fixed dict of seven), opens one SSH connection with `ssh_connect_host` inside an
  `AsyncExitStack`, runs `_probe_host()` (line 283, the source address and the
  competing-store notice) and then `_run_collectors()` (line 326), which records a
  failing collector on its own module and carries on. After the connection closes it
  runs the inline drift checks, refreshes the host's status and commits once.
  `COLLECTABLE_MODULES` (line 31) is a closed list.
- The only caller is `_collect_state_async` in
  `backend/app/tasks/builtin_dispatchers.py` (line 294), which holds the per-host lock
  for the whole collection (`CLAUDE.md`, "Per-host serialization"). Time a probe spends
  is time the host stays claimed.
- The connection object is an `asyncssh` connection; collectors call
  `await conn.run(command, check=False)`.
- The session is shared by every collector and committed once at the end, so anything a
  probe adds to it is committed with the collection unless it is rolled back first.
- `backend/app/hosts/` exists as a package (`dependents.py`).
- Extensions load in Celery workers (`backend/app/tasks/__init__.py`, line 126), which
  is where collection runs, so a probe registered in `register(app)` is present there.
- Tests that drive the collection with SSH stubbed:
  `backend/tests/test_collect_module_state.py` (a `_Conn` fake with a `commands` log),
  `tests/test_batched_collectors.py`, `tests/test_collect_state_queue.py`.
- `PLUGIN_API = "1.2"` (`backend/app/auth/permissions.py`, line 71); earlier stages add
  minors.

## Dependencies

- No item dependency and no new package.

## Implementation steps

1. Create `backend/app/hosts/probes.py`:
   - `HostProbe = Callable[[Host, Any, AsyncSession], Awaitable[None]]`, called as
     `await fn(host, conn, db)`.
   - `register_host_probe(name: str, fn: HostProbe) -> None`. Names are unique; the same
     name with the same function is a no-op, with another function a `ValueError`.
   - `reset_host_probes()` for tests, and `PROBE_TIMEOUT_SECONDS = 30`.
   - `async def run_host_probes(host, conn, db) -> dict[str, str]`: for each probe in
     registration order, run it inside `async with db.begin_nested():` and
     `asyncio.timeout(PROBE_TIMEOUT_SECONDS)`. Catch `Exception` and `TimeoutError`, log
     a warning with the probe name and host id, and record `"failed"` or `"timeout"`;
     otherwise `"ok"`. The savepoint rolls back what a failed probe wrote. Return the
     per-probe result for tests and logs.
   - The module docstring is the contract: a probe is read-only on the host, must use
     only the given connection (never open its own), may write its own rows through the
     session, must not commit or roll back the session, must not touch
     `HostModuleStatus` or the host row, and has 30 seconds.
2. `backend/app/api/host_state.py`, in `collect_module_state()`: inside the
   `async with AsyncExitStack()` block, after `await _run_collectors(...)`, add
   `if module is None: await run_host_probes(host, conn, db)`. Do nothing when no probe
   is registered. The unreachable-host and host-key-mismatch exits are unchanged, so a
   probe never runs without a working connection.
3. Bump `PLUGIN_API` to the next free minor and add the comment line:
   "`app.hosts.probes`: `register_host_probe` (a read-only probe run after the module
   collectors on a full state collection)."
4. `CLAUDE.md`, section "Extensions (plugins) and directory sign-in": one bullet for the
   hook with the rules from the docstring. Update the `backend/app/plugins.py`
   docstring's list of hooks.
5. Tests, in a new `backend/tests/test_host_probes.py`, reusing the stubs of
   `tests/test_collect_module_state.py` (move its `_Conn` and patch helpers into a small
   shared module under `tests/` if importing them is awkward). A stand-in extension in
   the test module (a `register(app)` with `requires_plugin_api`, loaded with
   `load_plugins(FastAPI(), [EntryPoint(...)])` as in `tests/test_external_auth.py`,
   line 315) registers the probes under test. A fixture calls `reset_host_probes()`
   before and after each test.
6. Ask for review before merge (class B). The reviewer checks the savepoint and time
   limit, and that a probe cannot change a module's status.

## Acceptance criteria

- With a stand-in probe registered, a full collection calls it once with the host, the
  same connection object the collectors used, and the session; the row it adds through
  the session is committed with the collection.
- A collection for one module (`module="service"`) does not call the probe.
- A probe that raises after adding a row leaves no row behind, the seven module states
  and the host's `sync_status` equal those of a run without the probe, the function
  returns the same results and notices, and one warning is logged naming the probe.
- A probe that sleeps past the time limit (patched to a fraction of a second in the
  test) is cancelled and recorded as `timeout`; a second probe registered after it still
  runs.
- When the host is unreachable or its key changed, no probe is called.
- Registering a second function under an existing name raises `ValueError`.
- With no probe registered, `tests/test_collect_module_state.py`,
  `tests/test_batched_collectors.py` and `tests/test_collect_state_queue.py` pass
  unchanged.
- `PLUGIN_API` is one minor higher (or more), and `is_compatible("1.2")` is still true.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_host_probes.py tests/test_collect_module_state.py tests/test_batched_collectors.py tests/test_collect_state_queue.py tests/test_external_auth.py -q`
- `ruff check . && ruff format --check .`
- The full suite runs once before the stage review.
- One real run by the stage review, on local Docker on lin-manager-2 with a small test
  plugin whose probe runs `uname -r`: collect state on a host named "test" and see the
  probe's log line and unchanged module states. Only hosts named "test" are used, and
  the probe is read-only.

## Docs and changelog

- `CLAUDE.md` and the `app/plugins.py` docstring as in step 4. There is no extension
  developer page under `docs/` at the time of writing; if one exists by then, add the
  hook there. No user-facing page changes, because Community shows nothing new.
- `CHANGELOG.md`, `[Unreleased]`, under "Added": "Extension hook for host state
  collection. An extension can add a read-only probe that runs with a full collection.
  A Community install collects exactly as before. Plugin API minor bump."

## Risks and rollback

- A probe lengthens the time a host stays claimed, by up to 30 seconds each. The limit
  is fixed in Community, and probes run only on full collections.
- "Read-only" cannot be enforced by the hook: the connection can run any command. The
  contract states it, and the extension is expected to test its own command list.
- Cancelling a probe at the time limit while it waits on the database could leave the
  session unusable. The savepoint context rolls back on cancellation; the timeout test
  must include a probe that is cancelled during a query, and if that proves unreliable,
  apply the limit to the SSH calls only and say so in the contract.
- A probe that commits would commit half a collection. The contract forbids it; the
  test for the failing probe would catch a regression in the driver, not in a probe.
- Rollback: revert the commit and set `PLUGIN_API` back. An extension built against the
  new minor is then skipped at load with a log line, and collection is as before.

## Assumptions

- Assumption (not yet confirmed): probes run only on a full collection, not when one
  module is collected and not on drift sweeps, to keep single-module refreshes fast.
- Assumption (not yet confirmed): a failing probe is logged and nothing is shown to the
  operator. Question for Dennis: should a probe failure appear as a notice on the
  collection, like the competing-store warning?
- Assumption (not yet confirmed): a fixed 30-second limit per probe.
- Assumption (not yet confirmed): results live in the extension's own tables. The
  alternative, a generic "extra facts" column on the host for everyone to read, was not
  chosen because it would need a schema and a UI. Question for Dennis: is a Community
  fact store wanted later?
- The minor bump follows the rule in `CLAUDE.md`: additions bump the minor.

## Definition of done

- [ ] `app/hosts/probes.py` and the call in `collect_module_state()` are in place.
- [ ] `tests/test_host_probes.py` passes; the collection tests still pass.
- [ ] `PLUGIN_API` bumped with its comment; `CLAUDE.md`, the `plugins.py` docstring and
      `CHANGELOG.md` updated.
- [ ] Reviewed before merge.
- [ ] `planning/roadmap.yaml`: S-019 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
