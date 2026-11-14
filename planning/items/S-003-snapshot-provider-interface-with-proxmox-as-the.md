# S-003: Snapshot provider interface, with Proxmox as the first provider

| | |
|---|---|
| Edition | shared |
| Source | roadmap, "Rollback on VMware, other hypervisors and cloud" (hypervisor abstraction), shaped by decision D-19 |
| Tier / score | P1 / 6.5 |
| Stage | 4 |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal

Every snapshot, rollback and snapshot delete that the action executors and the AI rollback path perform goes through one provider interface, with Proxmox as the built-in provider. Nothing an operator sees changes. A later provider (the planned VM operations service "kennel", or an Enterprise provider for hosts that are not Proxmox guests) can register without touching the executors.

## Scope

In scope:

- A provider protocol, a registry with a registration hook, and a Proxmox provider that wraps today's code.
- Moving the callers listed under Context onto it.
- A per-VM capability check as an interface method with a Proxmox implementation (C-005 decides what a failed check does; C-044 uses `include_ram`).
- A `PLUGIN_API` minor bump.

Out of scope:

- Any behaviour change, any new refusal, any new setting, any UI.
- Generalising `ProxmoxNode` and `VMMapping` into a "connection with a kind". The tables stay.
- Proxmox discovery and the node API (`backend/app/proxmox/discovery.py`, `backend/app/api/proxmox_nodes.py`, `proxmox_discovery.py`): they manage connections, not snapshots.
- Power, guest exec, clone or network operations. Those belong to kennel (stage 11).

## Context

Proxmox snapshot calls are made from these places today:

- `backend/app/tasks/action_host.py`: `_load_envelope` (line 594) reads `VMMapping` and `ProxmoxNode` and builds a `ProxmoxClient`; `_Envelope` (line 242) holds `client`, `pve_node`, `vmid`, `vm_type`, `snapshot_name`; `_phase_a_snapshot` (758), `_rollback` (1108), `_delete_snapshot` (1143).
- `backend/app/tasks/action_group.py`: `_snapshot_all` (1007), `_load_vm_mappings` (1109), `_snapshot_member` (1143), `_rollback_member` (1527), `_delete_snapshot_after_rollback` (1565), `_cleanup_all` (1587); `_HostCtx` holds `proxmox_client`, `pve_node`, `vmid`, `vm_type`.
- `backend/app/ai/snapshots.py`: `SnapshotTarget`, `resolve_target` (the nearest thing to a provider today), `client_for`, `snapshot_before_change`.
- `backend/app/ai/remediation.py`: `get_vm_status` (about line 374), `_newest_only` and `_clear_the_way` (`get_vm_config`, `list_node_storage`, `list_snapshots`, lines 908 to 960), `_restore` (delete and `rollback_to_snapshot`, about 1017 to 1051).
- `backend/app/tasks/ai_snapshots.py` (retention sweep) and `backend/app/workflows/snapshot_cleanup.py` with `backend/app/tasks/snapshot_reaper.py` (orphan cleanup; it calls `client._request` directly at about line 172).
- The step functions all take a raw client: `backend/app/workflows/steps/snapshot.py::create_snapshot` (lock wait and naming), `steps/rollback.py::rollback_to_snapshot` (revert, start, wait for SSH, mark `out_of_sync`), `steps/cleanup.py::delete_snapshot` (lock retry), `steps/preflight.py::run_preflight`.
- `backend/app/proxmox/client.py::ProxmoxClient` is the HTTP client. `create_snapshot` (line 229) sends only `snapname` and `description`.

Existing tests fix the seams that must keep working. They patch `app.workflows.steps.snapshot.create_snapshot`, `app.workflows.steps.rollback.rollback_to_snapshot`, `app.workflows.steps.cleanup.delete_snapshot`, `app.proxmox.client.ProxmoxClient` and `app.ai.snapshots.resolve_target`, and `tests/test_action_host_phases.py` constructs `_Envelope(client=..., pve_node=..., vmid=...)` directly.

## Dependencies

- [D-19](../DECISIONS.md) fixes the interface: a per-VM capability check (snapshot, RAM, restore point, free space) that C-005 calls; `create(vm, label, include_ram)` returning an opaque handle; `rollback(handle, start)`; `delete`; asynchronous completion.
- `PLUGIN_API` is `"1.2"` in `backend/app/auth/permissions.py:71` at the time of writing. Take the next free minor.

## Implementation steps

1. Record the baseline first: run the test files named in the Test plan and keep the result. They must pass unmodified at the end.
2. New package `backend/app/snapshots/`.
   - `provider.py`: frozen dataclasses `VMRef(kind, host_id, ref: Mapping, label)`, `SnapshotHandle(kind, host_id, name, ref: Mapping, include_ram, created_at)`, `Capabilities(can_snapshot, can_include_ram, restore_points, free_bytes, reasons: tuple[str, ...], details: Mapping)`; exceptions `SnapshotError` and `SnapshotBusy`; and `class SnapshotProvider(Protocol)` with coroutines `resolve(db, host_id) -> VMRef | None`, `capabilities(vm) -> Capabilities`, `create(vm, *, label, run_id, name_prefix, include_ram=False, lock_wait_seconds=0, on_wait=None) -> SnapshotHandle`, `rollback(handle, *, start=True) -> None`, `delete(handle, *, lock_wait_seconds=...) -> None`, `list(vm) -> list[SnapshotInfo]`, `status(vm) -> str`, `handle_for(vm, name) -> SnapshotHandle`. `ref` is opaque to callers; only the provider that made it reads it.
   - "Asynchronous completion": each coroutine returns when the operation has finished. A provider whose backend answers with a job id (a Proxmox UPID, a remote service's job) polls inside the call.
   - `registry.py`: `register_snapshot_provider(provider)`, `providers()`, and `async provider_for(db, host_id) -> tuple[SnapshotProvider, VMRef] | None`, which asks providers in registration order and returns the first that resolves the host. Proxmox is registered at import, first.
   - `proxmox.py`: `ProxmoxSnapshotProvider`. `resolve` is today's `resolve_target` logic (mapping, node, client). `create`, `rollback` and `delete` call the existing step functions through their modules (`from app.workflows.steps import snapshot as snapshot_step`, then `snapshot_step.create_snapshot(...)`), and build the client through `app.proxmox.client.ProxmoxClient` looked up at call time, so the existing patches still intercept. `list`, `status` and `capabilities` wrap `list_snapshots`, `get_vm_status`, `get_vm_config` and `list_node_storage`; move `_disk_storages` and the storage-type knowledge from `ai/remediation.py` here and import them back.
3. Split `rollback_to_snapshot` conceptually, not physically: the provider's `rollback(handle, start=True)` is "revert and start"; waiting for SSH and marking the host `out_of_sync` is provider-neutral. Keep the function and its signature (tests patch it) and have the Proxmox provider own the first half. Add a neutral helper `backend/app/snapshots/recover.py::wait_for_host(host, ssh_key_path, db)` that other providers reuse.
4. `action_host.py`: `_load_envelope` calls `provider_for`. `_Envelope` gains `provider` and `vm` and keeps its present fields and the `can_snapshot` / `has_snapshot` properties, filled from the Proxmox `ref` so existing tests and log lines are unchanged. Phases A, E and F call the provider.
5. `action_group.py`: the same for `_snapshot_all`, `_snapshot_member`, `_rollback_member`, `_delete_snapshot_after_rollback` and `_cleanup_all`. `_HostCtx` gains `provider`, `vm` and `handle`, and keeps the old slots.
6. `ai/snapshots.py`: `resolve_target` stays as a thin wrapper over the registry (it is patched in six tests); `snapshot_before_change` calls `provider.create(..., name_prefix=AI_SNAPSHOT_PREFIX)`. `ai/remediation.py`, `tasks/ai_snapshots.py` and `workflows/snapshot_cleanup.py` use `status`, `list`, `delete` and `rollback` from the provider. Replace the private `client._request` call in `snapshot_cleanup.py` with a public method.
7. `ActionHostRun.snapshot_name` and `AIToolCall.snapshot_name` keep storing the name. `handle_for(vm, name)` rebuilds a handle from a host and a stored name. No migration.
8. `backend/app/auth/permissions.py`: bump `PLUGIN_API` to the next minor and add a line to the comment block: `app.snapshots` (`SnapshotProvider`, `register_snapshot_provider`). Update the "Extensions (plugins)" section of `CLAUDE.md` with one paragraph.
9. Add `backend/tests/test_snapshot_provider.py`: a fake provider registered for the test resolves a host with no `VMMapping`, and a failing destructive run through `action_host` and `action_group` calls its `create`, `rollback` and `delete`; with no provider resolving, the run logs today's "no Proxmox VM mapping" line.
10. Add a guard test in the same file: no module under `app/tasks/` or `app/ai/` imports `ProxmoxClient` or calls a `ProxmoxClient` snapshot method except through `app.snapshots` (an AST or text scan with an explicit allow-list for `app/proxmox/` and `app/api/proxmox_*.py`).

## Acceptance criteria

- The existing snapshot test files pass with no change to their source: `git diff --stat` shows none of them.
- The guard test in step 10 passes.
- A fake provider can serve a host with no Proxmox mapping end to end (step 9).
- Run output for a Proxmox-backed run is byte-identical in its `[snapshot]`, `[rollback]` and `[cleanup]` lines (asserted in the existing tests).
- `PLUGIN_API` is bumped by one minor and the comment names the addition.
- No new route, permission, migration or setting.

## Test plan

- Baseline and final: `cd backend && source .venv/bin/activate && pytest tests/test_action_host.py tests/test_action_host_phases.py tests/test_action_group.py tests/test_action_group_paths.py tests/test_snapshot_lock_wait.py tests/test_snapshot_cleanup.py tests/test_proxmox_client.py tests/ai/test_remediation.py tests/ai/test_snapshot_retention.py -q`.
- New: `pytest tests/test_snapshot_provider.py -q`. Then `pytest tests/ -q` once before the stage review, with `ruff check . && ruff format --check .` and `bandit -q -r app -b bandit-baseline.json`.
- Real host: on local Docker on `lin-manager-2`, run the `example` action against a VM named "test" twice, once passing and once with verify failing, and compare the run log with one taken before the change.
- Enterprise: after the bump, run `pytest -q` in `/home/user/unbork-enterprise` against this branch to confirm the package still loads (a newer minor in Community is compatible).

## Docs and changelog

- No user docs change. `CLAUDE.md` gets the hook paragraph.
- `CHANGELOG.md` `[Unreleased]`, Changed: "Snapshots, rollbacks and snapshot cleanup go through a provider interface (`app.snapshots`). Proxmox is the built-in provider; behaviour is unchanged. Extension API 1.x adds `register_snapshot_provider`."

## Risks and rollback

- This path runs inside every destructive action. The main risk is a silent difference in lock waiting, naming or cleanup. The unchanged test files and the log comparison are the guard.
- Keeping the legacy `_Envelope` and `_HostCtx` fields is deliberate duplication. It can be removed later, in an item that is allowed to edit those tests.
- Back out by reverting the commits. There is no data change.

## Assumptions

- [D-19](../DECISIONS.md) for the method set. Assumption (not yet confirmed): "asynchronous completion" means the coroutine returns on completion and polls internally, not a callback or job-id API. Question for Dennis if kennel needs detached jobs.
- Assumption (not yet confirmed): one provider per host, first match in registration order, Proxmox first. An extension that wants to override Proxmox for a mapped host is not supported.
- Assumption (not yet confirmed): `restore_points` in `Capabilities` is a declared capability only (False for Proxmox). Restore points are kennel's subject and nothing calls it yet.
- "Tests pass unchanged" in `stages/stage-4.md` is read literally, which is why the patch points and the envelope fields stay.

## Definition of done

- [ ] `app/snapshots/` merged, all callers moved, guard test in place.
- [ ] Existing snapshot tests green and unmodified; new tests green; ruff and bandit clean.
- [ ] `PLUGIN_API` bumped, `CLAUDE.md` and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set, one line added to `planning/PROGRESS.md`.
