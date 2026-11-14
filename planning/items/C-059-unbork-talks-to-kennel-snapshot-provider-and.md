# C-059: unbork talks to kennel: snapshot provider and surge nodes for upgrades

| | |
|---|---|
| Edition | community |
| Source | user (Dennis's second use case); decisions D-19, D-22, D-23; board item "LabDog integration with the VM layer" |
| Tier / score | P2 / 3.7 |
| Stage | 11 |
| Effort | L |
| Autonomy | B |
| Depends on | S-003, C-054, C-058 |

## Goal

An admin can connect unbork to a kennel service. From then on unbork can use kennel as a snapshot provider behind the S-003 interface for hosts that opt in, and `k8s-upgrade` can ask for a surge node for one run and give it back afterwards. With no kennel connection configured, unbork behaves exactly as before.

## Scope

In scope:
- A kennel connection setting (URL, API token, CA certificate) and a client.
- `KennelSnapshotProvider`, registered behind `app.snapshots`, off by default and opt-in per host.
- Leased hosts: a temporary host record made from a kennel lease, left out of drift sweeps and fleet counts.
- A manifest field that lets an action declare "this parameter can be filled by a leased host", and the run-time flow: request, wait, use, release, or hold on failure.
- `k8s-upgrade` in `open-unbork/unbork-playbooks` declaring that for its existing `surge_node` parameter.

Out of scope:
- Moving the default snapshot path to kennel. Direct Proxmox snapshots stay the default; the write-up calls the full move "option C", for later.
- Rollback through kennel. kennel restores beside the original only ([D-23](../DECISIONS.md)), so a kennel restore point cannot revert a host in place yet. See step 4.
- Preparing the surge VM's operating system for Kubernetes. The blueprint's template must already meet what `surge_node` asks for.
- A "last restore test" badge and `_builtin.restore_test`. See Assumptions.

## Context

- S-003 creates `backend/app/snapshots/` with `provider.py` (`SnapshotProvider` protocol: `resolve`, `capabilities`, `create`, `rollback`, `delete`, `list`, `status`, `handle_for`; dataclasses `VMRef`, `SnapshotHandle`, `Capabilities(can_snapshot, can_include_ram, restore_points, free_bytes, reasons, details)`), and `registry.py` (`register_snapshot_provider`, `provider_for(db, host_id)`, which returns the first provider that resolves the host; Proxmox is registered first). Read the merged code before starting; this text follows the S-003 spec.
- unbork identifies a VM through `VMMapping` (`backend/app/proxmox/vm_mapping.py`: `host_id`, `proxmox_node_id`, `pve_node_name`, `vmid`, `vm_type`) and `ProxmoxNode` (`backend/app/proxmox/models.py`). kennel names a VM `(cluster, vmid)` plus the cluster's certificate fingerprint. unbork needs a mapping from a `ProxmoxNode` to a kennel cluster name.
- kennel's API from earlier items: readiness (`GET /api/vms/{cluster}/{vmid}/readiness`, C-058), restore points (`POST /api/restore-points`, C-045), leases (`POST /api/leases`, `/extend`, `/hold`, `/release`, C-054), signed webhooks, idempotency keys.
- `k8s-upgrade` already supports a surge node. Its manifest (`/home/user/unbork-playbooks/actions/k8s-upgrade/manifest.yml`, version 1.8) has a string parameter `surge_node`: "A host in this LabDog group that is not a cluster node yet, but is ready to be one (container runtime, swap off, the pkgs.k8s.io repo for the target minor). It joins at the target version after the control plane, takes pods while the workers are drained, and is drained, reset and removed after the last worker." The plays are in `playbook.yml` (`k8s_surge` group, lines 38 to 170) and `roles/kubernetes-upgrade/tasks/surge-join.yml` and `surge-remove.yml`. So the pack needs a host that is a member of the targeted group; what is missing is unbork producing that host from a lease.
- The action is group-dispatched (`supports_host: false`): `backend/app/tasks/action_group.py::run_action_group` builds one inventory from the group's members, and group membership is read in `backend/app/tasks/action_orchestrator.py::_resolve_target_host_ids` (line 488). Host locks are taken for every member in `backend/app/tasks/host_lock.py`.
- Manifest schema: `backend/app/actions/manifest.py`. `ActionManifest` has `extra="ignore"` (line 69), so a new top-level key is ignored by older unbork versions. `ManifestParameter` has `extra="forbid"` and `type: Literal["string", "int", "bool", "choice"]` (lines 22 to 27), so a new parameter type or parameter key would make the whole pack fail to load on older versions. This decides the design in step 6.
- The board rule that matters most: "a failed run leaves the surge node in the cluster carrying pods, and a re-run reuses it, so on failure LabDog must extend the lease and mark it held. A held lease never expires without a person releasing it."
- Hosts: `backend/app/models/host.py` (`Host`, line 56). Drift candidates come from `backend/app/tasks/drift_sweep.py::_candidates` (line 276). There is no notion of a temporary host today.
- Connection settings for other integrations to copy: `backend/app/api/proxmox_nodes.py` (reads with `hosts.view`, writes with `integrations.manage`), encryption through `app.crypto.encryption`.

## Dependencies

- S-003 merged. C-054 and C-058 running in a kennel reachable from unbork. C-045 for the provider's `create`. C-057 only if the surge blueprint sits on a sandbox network (a real surge node must reach the cluster, so it normally does not).
- A kennel API token with scopes `read`, `leases` and `restore`, created by an admin in kennel.
- A Kubernetes cluster built from VMs named "test" on the test Proxmox cluster, and a kennel blueprint whose template is ready to join it. Dennis must provide or approve both.
- A PR to `open-unbork/unbork-playbooks` for step 9.

## Implementation steps

1. **Connection.** Migration `backend/alembic/versions/00NN_kennel.py` (next free number): `kennel_connections` (id, name, api_url, encrypted_token, verify_ssl, ca_cert_pem, enabled, last_check_at, last_check_ok); a nullable `kennel_cluster` column on `proxmox_nodes`; nullable `hosts.lease_id` (text), `hosts.lease_connection_id` and `hosts.temporary` (bool, default false); table `kennel_leases` (id, connection_id, lease_id, action_run_id, host_id nullable, parameter, blueprint, state, held, expires_at, created_at, released_at).
2. **Client** `backend/app/kennel/client.py`: an `httpx.AsyncClient` wrapper in the style of `ProxmoxClient` (`backend/app/proxmox/client.py`, TLS handling at line 69) with `readiness`, `create_restore_point`, `job`, `create_lease`, `get_lease`, `extend_lease`, `hold_lease`, `release_lease`. Every call that starts work sends an idempotency key derived from the action run id.
3. **Routes** `backend/app/api/kennel.py`, prefix `/api/kennel`:
   - `GET /connection`: `Depends(require("hosts.view"))`; never returns the token.
   - `PUT /connection`, `DELETE /connection`, `POST /connection/test`: `Depends(require("integrations.manage"))`.
   - `GET /blueprints`: `Depends(require("actions.run", scope=anywhere))`; proxies kennel's list for the run dialog.
   - `GET /leases`: `Depends(require("hosts.view", scope=anywhere))`, narrowed with `visible_host_ids()`.
   - `POST /leases/{id}/release`: `Depends(require("actions.run", scope=row_scope(KennelLease, "id")))`; releases a held lease and removes its temporary host.
4. **Snapshot provider** `backend/app/snapshots/kennel.py::KennelSnapshotProvider`, registered after Proxmox only when a connection is enabled.
   - `resolve(db, host_id)`: returns a `VMRef(kind="kennel", ...)` only for a host that has a `VMMapping`, whose `ProxmoxNode.kennel_cluster` is set, and that has opted in (a per-host setting `snapshot_provider = "kennel"`; add the column in step 1's migration). Because `provider_for` returns the first provider that resolves, add the opt-in check to the registry order: a host that opted in to kennel is offered to kennel first. Make that change in `registry.py` as a general "preferred provider kind per host" rule, not a kennel special case.
   - `capabilities(vm)`: one readiness call, mapped onto `Capabilities` (`can_snapshot` is whether a restore point can be taken, `can_include_ram=False` always, `restore_points` and `free_bytes` from the answer, kennel's reason codes into `reasons`).
   - `create(...)`: `POST /api/restore-points`, then poll the job inside the call until done, as S-003's "asynchronous completion" rule says. The handle's `ref` holds the restore point id.
   - `delete(handle)`: `DELETE /api/restore-points/{id}`.
   - `rollback(handle)`: raises `SnapshotError("kennel restores beside the original only; in-place restore is not available yet")`. The executors already treat a failed rollback as a failed rollback; check that the run's message tells the operator that a restore point exists and how to restore it beside the original in kennel.
   - With kennel unreachable, `capabilities` returns `can_snapshot=False` with reason `kennel_unreachable`, so C-005's pre-run check refuses before the run starts, as the write-up requires.
5. **Readiness in the pre-run check.** For hosts on the Proxmox provider, when a kennel connection exists, `backend/app/snapshots/precheck.py::check_snapshot_possible` (from C-005) adds kennel's restore point age to `details` as information only. It never turns a pass into a refusal.
6. **Leasable parameters.** In `backend/app/actions/manifest.py` add an optional top-level field to `ActionManifest`: `leases: list[ManifestLease]`, where `ManifestLease` is `{parameter: str, purpose: str, label: str | None}` and `parameter` must name a declared string parameter (validated). Older unbork versions ignore the key. Expose it in the action catalog schema so the run dialog can show, next to that parameter, a "Lease from kennel" choice with a blueprint picker, visible only when a connection is enabled.
7. **Run flow** in `backend/app/tasks/action_orchestrator.py`, before host runs are created (near `_resolve_target_host_ids`, line 488). The run request carries `leases: {"<parameter>": {"blueprint": "<name>"}}` (extend the run schema and `POST /api/actions/runs`; the handler checks `require_on(user, "actions.run", groups=[group_id])` as today).
   a. Reuse: if the group already holds a temporary host with a held lease for this action and parameter, use it and skip to d.
   b. `create_lease` with the run id as idempotency key, a TTL of the action's `playbook_timeout_seconds` plus a margin, and unbork's SSH public key for the group's hosts. Poll `get_lease` until `ready` or a timeout (setting, default 10 minutes). On timeout or failure, release and fail the run before any host is touched.
   c. Create the temporary host: a `Host` row with `temporary=true`, the lease ids, the lease's IP address, the SSH host key pinned from the lease (`ssh_host_key_entry`), the group's SSH key and user, and membership of the targeted group only. Set `drift_check_enabled=false`.
   d. Fill the parameter with the temporary host's hostname and continue with the normal run. The temporary host is locked with the other members.
   e. On success: `release_lease`, delete the temporary host row, mark the `kennel_leases` row released.
   f. On failure or cancel: `hold_lease` with the run id as reason, keep the temporary host, and say so in the run's log and result. A re-run takes branch a.
   g. A periodic task (register in `backend/app/tasks/beat_registry.py`, queue `default`) reconciles: a temporary host whose lease kennel reports destroyed is removed; a lease that is ready with no run using it and not held is released.
8. **Temporary hosts stay out of the way.** Exclude `Host.temporary` from `drift_sweep._candidates`, from bulk sync target lists, from scheduled fleet runs in `_resolve_target_host_ids` (the fleet branch), and from dashboard counts. Show them in the host list with a "leased" marker and their expiry. Find every `select(Host)` that builds a fleet-wide list and decide each one; add a test that lists the decisions.
9. **Pack.** In `open-unbork/unbork-playbooks`, `actions/k8s-upgrade/manifest.yml`: add `leases: [{parameter: surge_node, purpose: k8s-surge, label: Surge node}]`, bump `version`, and extend the `surge_node` help text and `README.md`. No playbook change is expected; confirm that the surge plays treat a host that joined the group at run start like any other member.
10. **Frontend.** Settings: a kennel connection form next to the Proxmox settings (`frontend/app/(dashboard)/settings/proxmox/`). Run dialog: the lease choice from step 6. Hosts list and host page: the leased marker and a "Release" button for a held lease.
11. `PLUGIN_API`: no bump is needed unless a hook signature in `app.snapshots` changes for the preferred-provider rule; if it does, bump the minor in `backend/app/auth/permissions.py` and note it.

## Acceptance criteria

- With no kennel connection, every existing test passes unchanged and no kennel call is made.
- `POST /api/kennel/connection/test` succeeds against a running kennel and fails with a clear message for a wrong token or an untrusted certificate.
- A host opted in to the kennel provider gets a kennel restore point before a destructive action, shown in the run log, and the restore point is deleted after a successful run according to the same rule as snapshots. This is the first half of the stage behaviour "unbork uses kennel as a snapshot provider behind the S-003 interface".
- With kennel stopped, a destructive run on an opted-in host is refused before the playbook starts; a host on the Proxmox provider still runs.
- Running `k8s-upgrade` on the "test" cluster with the surge lease chosen: a VM appears in kennel's pool, joins the cluster, the workers are upgraded, the node is removed, the lease is released and the temporary host is gone. This is the second half: "k8s-upgrade can request and release a surge node".
- A run made to fail after the surge node joined leaves the lease held and the temporary host present; a re-run reuses it; `POST /api/kennel/leases/{id}/release` removes both.
- A temporary host never appears in a drift sweep, a bulk sync or a fleet schedule.
- Every new route is refused for a role without its permission (`tests/test_permissions.py`).
- A pack manifest with a `leases` entry naming an undeclared parameter fails validation with a clear message.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_kennel_client.py tests/test_kennel_api.py tests/test_kennel_snapshot_provider.py tests/test_kennel_surge_lease.py tests/test_temporary_hosts.py tests/test_manifest_leases.py -q` (all new; kennel mocked with `httpx.MockTransport`), then `pytest tests/test_permissions.py tests/test_permission_scopes.py tests/test_snapshot_provider.py -q`, then the full suite once before the stage review.
- `cd frontend && npx tsc --noEmit && npm run lint`, and `npx playwright test` for the spec that covers the run dialog.
- Pack: in `unbork-playbooks`, the commands in that repository's guide (`validate_pack.py`, `ansible-lint`, its e2e).
- Running stack: unbork and kennel on local Docker on lin-manager-2, against the test Proxmox cluster. The Kubernetes cluster and every host touched are named "test". Nothing here runs on the live instance on lin-manager except a final read-only connection test.

## Docs and changelog

- New `docs/kennel.md`: what kennel is, connecting it, the snapshot provider and its limit (restore beside only), surge nodes, held leases.
- `docs/ui/actions.md`: the `leases` manifest field and the lease choice in the run dialog. `docs/examples/action-packs/`: one example manifest using it.
- `CHANGELOG.md` `[Unreleased]`, Added: "Optional connection to a kennel service: restore points as a snapshot provider for chosen hosts, and leased surge nodes for group actions such as k8s-upgrade."

## Risks and rollback

- A held surge node that nobody releases keeps running and keeps its pods. Show held leases on the Overview and send a notification when a lease is held (use an existing action-failure event if one fits).
- A provider whose `rollback` cannot revert may be mistaken for protection. The opt-in text and the run log must say that a kennel restore point is a recovery copy, not an automatic rollback.
- A temporary host deleted while a run uses it. The host lock and step 7g's "no run using it" check guard this.
- kennel in the path of a run adds a failure point. It is limited to opted-in hosts and runs that ask for a lease; both fail before the run starts.
- Rollback: disable the connection (`enabled=false`): the provider unregisters, the lease choice disappears, and hosts opted in to kennel fall back to "no provider resolves", which C-005 reports. The migration's downgrade drops the new tables and columns.

## Assumptions

- [D-19](../DECISIONS.md), [D-22](../DECISIONS.md), [D-23](../DECISIONS.md).
- Assumption (not yet confirmed): the kennel snapshot provider is opt-in per host and cannot roll back in place until in-place restore exists. Question for Dennis: is a provider with `create` and no `rollback` what the stage behaviour means, or should this half wait for in-place restore?
- Assumption (not yet confirmed): a top-level `leases` manifest field instead of the `lease` parameter type the board notes suggested, because a new parameter type makes the public pack fail to load on every older unbork version.
- Assumption (not yet confirmed): unbork polls kennel for lease state and does not receive kennel's webhooks in this item. A signed webhook receiver can follow.
- Assumption (not yet confirmed): one kennel connection per install.
- Assumption (not yet confirmed): the "last restore test" badge and `_builtin.restore_test` from the board notes are not part of this item. Question for Dennis: add them here or as a new item?

## Definition of done

- [ ] Migration, client, routes, provider, lease flow and temporary-host exclusions merged; new tests and the full suite green; ruff and bandit clean.
- [ ] Frontend changes merged; type check and lint clean.
- [ ] `k8s-upgrade` manifest PR merged in `unbork-playbooks`.
- [ ] Both stage behaviours proven on the "test" cluster; results saved in the PR.
- [ ] `docs/kennel.md`, `docs/ui/actions.md` and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status updated; one line in `planning/PROGRESS.md`.
