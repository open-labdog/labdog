# C-054: kennel: leases and the sweeper

| | |
|---|---|
| Edition | community |
| Source | user (Dennis, kennel thread); decision D-22; board item "Companion VM layer" |
| Tier / score | P2 / 4.7 |
| Stage | 11 |
| Effort | L |
| Autonomy | B |
| Depends on | C-028 |

## Goal

Every VM kennel creates has a lease with an owner, a purpose and an expiry. A sweeper removes every VM in kennel's pool whose lease has expired or that has no lease at all, so nothing kennel made is ever left behind.

## Scope

In scope:
- The lease model, its states, and the routes to create, read, extend, hold and release a lease.
- One way to make a VM for a lease: a full or linked clone of a Proxmox template named by a blueprint.
- Proxmox tags and a description on every kennel VM that carry the lease identity.
- The sweeper, and the webhooks `lease.ready` and `lease.expiring`.

Out of scope:
- Leases made from a backup (sandbox copies and restore tests use the lease record from this item but create the VM in C-045 and C-055).
- The sandbox network (C-057). A blueprint names its bridge; until C-057 it is whatever bridge the operator configured.
- OpenTofu blueprints. See Assumptions.
- unbork asking for a lease (C-059).

## Context

- The board item sets the rules this item implements: a lease has "an owner, a purpose and an expiry"; the sweeper "lists the pool on Proxmox and destroys anything expired or without a lease. It trusts Proxmox tags, not OpenTofu state, so a killed worker, a lost state file or a crashed LabDog all end the same way"; and "a held lease never expires without a person releasing it", because a failed Kubernetes upgrade leaves the surge node carrying pods.
- The write-up "How far kennel can go" fixes the calls: `POST /api/leases`, then `/extend`, `/hold`, `/release`; the caller's run id is the idempotency key; `lease.ready` carries the IP address and the SSH host key; `lease.expiring` warns before expiry.
- From C-028: the `jobs` table and worker, `webhooks.py` with its outbox, `clusters` with a pool, the `leases` token scope, and the Proxmox client copied from unbork's `backend/app/proxmox/client.py`. That client has no clone, destroy, tag or agent-exec call yet; unbork's has `get_vm_agent_interfaces` (line 182), `start_vm` (line 385), `get_vm_status` (line 369) and `wait_for_task` (line 337) to copy.
- Proxmox calls this item adds to kennel's client: `POST /nodes/{node}/qemu/{template}/clone` (with `newid`, `name`, `pool`, `full`), `PUT /nodes/{node}/qemu/{vmid}/config` (tags, description, cloud-init `ciuser`, `sshkeys`, `ipconfig0`, `net0`), `POST .../status/start`, `POST .../status/stop`, `DELETE /nodes/{node}/qemu/{vmid}?purge=1&destroy-unreferenced-disks=1`, `GET /pools/{pool}`, `GET /cluster/nextid`, `POST .../agent/exec` and `GET .../agent/exec-status`.

## Dependencies

- C-028.
- Proxmox side, by Dennis: at least one cloud-init template VM on the test cluster that the pool token may clone (`VM.Clone` on the template, which lives outside the pool), with the QEMU guest agent installed. Record its vmid in kennel's blueprint configuration.
- A storage in the pool's scope with room for a few test VMs.

## Implementation steps

1. **Migration** `backend/alembic/versions/0002_leases.py` in kennel: `blueprints` (name unique, cluster_id, template_vmid, full_clone bool, cores, memory_mb, disk_gb, bridge, cloud-init user, default_ttl_seconds, max_ttl_seconds, max_concurrent) and `leases` (id UUID, blueprint_id nullable, cluster_id, vmid nullable, owner_token_id, owner_ref text, purpose text, state, held bool, hold_reason, expires_at, callback_url, idempotency_key unique per owner, ip_address, ssh_host_key, created_at, ready_at, released_at, destroyed_at, error).
2. **States:** `provisioning → ready → released → destroyed`, with `failed` from `provisioning`, and `expired` set by the sweeper before it destroys. `held` is a flag, not a state: a held lease is never expired by the sweeper.
3. **Tagging.** Every VM kennel creates gets, before its first start, the tags `kennel` and `kennel-lease-<short id>` and a description line `kennel lease <uuid> owner <owner_ref> expires <ISO time>`. Put this in one function, `kennel/leases/tags.py::stamp(vm, lease)`, that C-045 and C-055 also call. Allocate the lease row and its id before the clone call, so there is never a tagged VM without a row or a clone without a tag for longer than one API call.
4. **Routes** in `kennel/api/leases.py`, each with `Depends(require("leases"))` unless noted:
   - `POST /api/leases` (blueprint, ttl_seconds, owner_ref, purpose, callback_url, ssh_public_key, optional `Idempotency-Key` header). Returns 202 with the lease and a job id. The same key returns the same lease.
   - `GET /api/leases/{id}` and `GET /api/leases?owner_ref=&state=` (`require("read")`).
   - `POST /api/leases/{id}/extend` (ttl_seconds, capped by the blueprint's `max_ttl_seconds`).
   - `POST /api/leases/{id}/hold` (reason) and `POST /api/leases/{id}/unhold`.
   - `POST /api/leases/{id}/release`: stops and destroys the VM now.
   - `GET/POST/PATCH/DELETE /api/blueprints` (`require("admin")`; `GET` with `read`).
5. **Provision job** (`kind="lease_provision"`): check `max_concurrent` and free space on the target storage; take `GET /cluster/nextid`; clone into the pool; stamp tags; set cores, memory, bridge and cloud-init (user, the caller's SSH public key, DHCP); start; wait for the guest agent; read the IP address with the agent interface call and the SSH host key with `agent/exec` (`cat /etc/ssh/ssh_host_ed25519_key.pub`); set `ready`; send `lease.ready` with id, vmid, IP address and host key. On any failure: destroy what was created, set `failed` with the error, send `lease.failed`.
6. **Sweeper** (`kind="sweep"`, scheduled by the worker loop every 60 seconds per cluster, with a row lock so two workers never sweep one cluster at once):
   - List the pool's members from Proxmox. This list, not the database, is the source of truth.
   - For each VM: find its lease by the `kennel-lease-…` tag. Destroy it when there is no such tag, or the lease row is missing, or the lease is `released`, `failed` or `expired`, or `expires_at` has passed and `held` is false. Never touch a VM outside the pool, whatever its tags say.
   - A VM younger than a grace period (default 10 minutes, by Proxmox creation time or `meta` config field) is skipped when it has no tag yet, to avoid racing a clone in progress.
   - Mark leases whose VM no longer exists as `destroyed`.
   - Send `lease.expiring` once, a configurable time (default 15 minutes) before expiry, for leases that are not held.
   - Every destroy is written to `audit_log` with the reason.
   - Destroy means: stop if running, then delete with purge. Retry a locked VM with backoff, as unbork's `backend/app/workflows/steps/cleanup.py` does.
7. **Safety switches:** `sweeper.dry_run` (log what would be destroyed; default true on a cluster until the operator turns it off after reading one dry-run report) and `sweeper.max_destroys_per_run` (default 10; more than that stops the run and raises an error, since it suggests a wrong pool).
8. Extend `GET /health` with the time of the last successful sweep per cluster; unhealthy when it is older than five intervals.
9. Docs: kennel `docs/leases.md` (blueprints, states, hold, the sweeper's rules and switches) and `docs/api.md`.

## Acceptance criteria

- `POST /api/leases` with a blueprint returns a lease that reaches `ready`, and the VM on Proxmox is in the pool with both tags and the description line.
- `lease.ready` arrives at the callback with a valid signature, the IP address and the SSH host key, and an SSH connection with the supplied key succeeds.
- A lease with a 2 minute TTL is destroyed by the sweeper within two sweep intervals of expiry, and its row ends `destroyed`.
- A held lease past its expiry is still there after three sweep intervals.
- A VM cloned by hand into the pool with no lease tag is destroyed after the grace period. A VM outside the pool carrying a `kennel` tag is never touched.
- Killing the worker in the middle of a provision leaves a VM that the next sweep removes.
- This is the stage behaviour: "Every VM kennel creates has a lease with an owner and an expiry, and the sweeper removes what expires or is orphaned."
- With `sweeper.dry_run=true` nothing is destroyed and the report lists what would be.
- Two `POST /api/leases` calls with one idempotency key create one VM.

## Test plan

- In the kennel repository: `cd backend && source .venv/bin/activate && pytest tests/test_leases_api.py tests/test_lease_provision.py tests/test_sweeper.py -q` (new; Proxmox mocked with `httpx.MockTransport`, including a fake pool listing), then `pytest tests/ -q`, `ruff check . && ruff format --check .`, `bandit -q -r kennel`.
- Against the test cluster from lin-manager-2, with a pool that holds nothing but kennel's own VMs: the seven behaviours above. Run the sweeper in dry-run first and read the report before turning it off.
- The sweeper is never pointed at a pool that contains a VM someone wants. No production VM is in the pool.

## Docs and changelog

- kennel `docs/leases.md`, `docs/api.md`, `docs/proxmox-token.md` (add `VM.Clone` on the template), and `CHANGELOG.md` `[Unreleased]`: "Leases with owner, purpose and expiry; hold; a sweeper that removes expired and orphaned VMs from the pool."
- unbork: none in this item.

## Risks and rollback

- The sweeper destroys VMs. The guards are: pool membership as the only scope, the pool-scoped token from C-028 (which cannot delete outside the pool even if the code is wrong), dry-run by default, the per-run cap and the audit log.
- A wrong pool name at setup would aim the sweeper at real VMs. The cluster self-test from C-028 should warn when the pool already contains VMs without kennel tags, and the sweeper refuses to leave dry-run while it does unless the operator confirms.
- A lease held forever wastes resources. List held leases on `GET /api/leases?held=true` and report their count on `/health`.
- Rollback: set `sweeper.dry_run=true` and stop creating leases; existing VMs are removed by hand from the pool.

## Assumptions

- [D-22](../DECISIONS.md) puts leases and the sweeper in kennel's scope.
- Assumption (not yet confirmed): the first blueprint kind is "clone a Proxmox template" through the API. The board notes proposed OpenTofu modules with the `bpg/proxmox` provider and state in Postgres. Question for Dennis: is the API clone enough for stage 11, with OpenTofu blueprints as a later item, or is OpenTofu required now?
- Assumption (not yet confirmed): the sweeper interval is 60 seconds and the orphan grace period is 10 minutes.
- Assumption (not yet confirmed): DHCP on the blueprint's bridge gives the VM its address. Static addressing is not planned.
- Assumption (not yet confirmed): `owner_ref` is free text set by the caller (unbork sends its run id).

## Definition of done

- [ ] Migration, routes, provision job and sweeper merged in kennel; tests and lint green.
- [ ] The behaviours above proven on the test cluster; the dry-run report saved in the PR.
- [ ] kennel docs and changelog updated.
- [ ] `planning/roadmap.yaml` status updated; one line in `planning/PROGRESS.md`.
