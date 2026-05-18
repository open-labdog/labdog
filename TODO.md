# TODO

Open tasks and forward-looking design notes for LabDog.

## Convention: open-only

**Only open items belong in this file.** When a task is completed:

1. Land the fix and write a descriptive commit message — that commit
   message is the canonical record (what changed, why, how).
2. Delete the entry from this file in the same commit (or a follow-up
   `docs(todo): Tick off ...` commit). Do **not** mark items `[x]`
   and leave them here.

To retrace a completed task, search the commit log:

```
git log --grep "labdog-playbooks"
git log -- frontend/app/\(dashboard\)/groups/page.tsx
```

---

## Pre-release checklist

### Polish

- [ ] **Container-based packaging smoke test in CI.** A hand-run
      smoke pass during v0.2.0 prep surfaced and fixed three
      install-path bugs (see `git log --grep packaging` for the
      individual fix commits). Still open: add `packaging/tests/`
      with a containerised harness (Ubuntu 24.04 .deb, Rocky 9 .rpm,
      Ubuntu 24.04 tarball-via-install.sh) and a new CI job that
      runs it after `release-artifacts`, so the smoke procedure that
      was run by hand for v0.2.0 becomes a permanent gate on
      subsequent releases.
- [ ] **Mark `version-check` as a required status check on `main`.**
      The new release pipeline gates release PRs on a `version-check`
      job that asserts `VERSION` is bumped, semver-shaped, and the
      `vX.Y.Z` tag isn't already taken. The gate only enforces if
      branch protection on `main` lists `version-check` as a required
      status check — otherwise a maintainer can merge a PR even when
      the job fails or is skipped. Configure in
      **Settings → Branches → main → Branch protection rules →
      Require status checks to pass before merging → search for
      "version-check"**. Same screen, also confirm "Require branches
      to be up to date" so the check runs against the actual merge
      commit. This is GitHub repo config, not a code change — won't
      land in any commit; needs a maintainer with admin rights.

---

## Bundled pack scope — keep 1:1 or slim down?

**Context:** The scaffold at `../labdog-playbooks/` is a 1:1 copy of
`backend/app/ansible/` (the bundled pack baked into the image). Both
ship the same three actions today. An operator adding
`labdog-playbooks` as a git Action Pack under Integrations → Git
Repos + Action Packs will then have the same three keys from two
sources; the external pack wins by position (it sits above bundled) and the bundled
copy becomes a pure safety net for when the pack's GitRepository is
unreachable at boot.

**Decision needed:** what goes in the safety net?

- **Option A — keep identical.** Every action present in both. Fresh
  installs work offline; the external pack adds nothing on a
  successful clone. Biggest image, most duplication (playbooks
  drift risk).
- **Option B — slim to a minimal "known-safe" subset.** e.g. only
  `linux-upgrade` (the most common case) and leave `k8s-upgrade` /
  `linux-os-upgrade` to the remote. Smaller image, but an offline
  install is partially functional.
- **Option C — remove bundled entirely.** External pack is
  mandatory. Cleanest conceptually but breaks air-gapped deploys
  and makes first-boot failure modes worse.

I'd default to **A** until we have a reason to trim — duplication
cost is negligible and drift is catchable by keeping the two dirs
byte-identical in CI (a one-line `diff -r` gate).

---

## k8s-upgrade — broaden OS support

**Context:** The bundled `k8s-upgrade` action is currently apt-only;
the role refuses to run on `ansible_os_family != "Debian"` with a
clear error. RHEL / Rocky / Alma-family hosts are the obvious next
target — `dnf` plus `dnf versionlock` instead of `apt` + `apt-mark
hold`, otherwise the kubeadm flow is identical.

**Sketch:**

- Split `tasks/upgrade-control-plane.yml`,
  `tasks/upgrade-worker.yml`, and `tasks/upgrade-packages.yml` into
  per-distro subtasks (`-debian.yml` / `-redhat.yml`) with
  `ansible.builtin.import_tasks` selected on `ansible_os_family`.
- Drop the `Refuse non-Debian-family hosts` task in
  `tasks/main.yml`.
- Verify the kubeadm + kubelet + kubectl repo at `pkgs.k8s.io`
  serves the requested `target_version` for the host's OS family
  in `tasks/preflight.yml`.
- Smoke-test on at least one Rocky 9 + Debian 12 mixed cluster
  before declaring done.

---

## Action manifests — opt-in post-run module sync

**Context:** Actions mutate host state. LabDog's modules (firewall,
services, packages, cron, hosts-file, users, resolver) track desired
state and reconcile via the existing sync / drift pipeline. The two
systems don't currently talk to each other — an action that installs
a package, opens a port, or adds a cron entry leaves the relevant
module's view stale until the next periodic drift check (or a manual
sync) catches up.

**Sketch:**

- Extend `ActionManifest` (`backend/app/actions/manifest.py`) and
  `ActionDefinition` (`backend/app/actions/types.py`) with an optional
  `post_run_sync: list[Literal["firewall", "services", "packages",
  "cron", "hosts_file", "users", "resolver"]] = []`.
- Plumb through `_manifest_to_definition` in `app/actions/packs.py`
  and the API `ActionDefinitionOut`.
- After a successful action run (both per-host and group-dispatch
  paths), if `post_run_sync` is non-empty, dispatch the corresponding
  module sync against the same target via the existing
  `host_sync_orchestrator` / option-c pipeline. Reuses everything:
  the per-host advisory lock, the coalesced playbook, the audit log,
  the SSE channel.
- Failures of a post-run sync are surfaced on the `ActionRun` as a
  warning, not a status flip — the action itself succeeded; only the
  reconciliation didn't.
- UI: action cards / run-detail show a small chip listing the modules
  that re-synced after the run (`packages ✓`, `services ✓`).

**Why it's worth doing:** the alternatives are (a) ad-hoc API
callbacks from inside playbooks (security + reachability + coupling
issues), or (b) operators remembering to click Sync after every
action. (b) is what we have today and it drifts in practice; (a) is
much more invasive than this is. Manifest-driven post-run sync keeps
the contract local to the action manifest and reuses the entire
existing pipeline.

**Open questions:**

1. Does `post_run_sync` run on dry-run / `__dry_run`? Probably no —
   no state changed, no reconciliation needed.
2. Cluster-mode runs target a group; the post-run sync should fan out
   per-host across the group (not on the driver node only).
3. Should the manifest also support `pre_run_sync` (sync to converge
   *before* the action runs, so the action sees a known baseline)?
   Probably yes for symmetry, but punt until a concrete need surfaces.

---

## CA certificate management in the UI

**Context:** Proxmox nodes (and other HTTPS targets) often use
self-signed or privately-issued TLS certificates. The Proxmox client
(`backend/app/proxmox/client.py`) uses httpx with default strict SSL
verification, so any host whose CA is not in the container's system
trust store causes `[SSL: CERTIFICATE_VERIFY_FAILED]` at discovery
time (see BUG-45).

**Sketch:**

- Add a CA Certificates section to the Integrations settings page.
  Users paste or upload one or more PEM-encoded CA certificates.
- Store them in the DB (or a dedicated config dir mounted into the
  container) and expose them via a settings key.
- Pass the stored CA bundle as the `verify=` argument to httpx
  `AsyncClient` in `ProxmoxClient.__init__` (or merge with the
  system trust store via `truststore` / `certifi`).
- Optionally: expose a per-host "TLS verify" toggle for the escape
  hatch (`verify=False`) behind a visible warning in the UI.

---

## Action runtime — honour run-time toggles in `action_host.py`

**Context:** `ActionRun` carries three operator-controllable toggles
for destructive actions: `snapshot_enabled`, `verify_enabled`, and
`auto_rollback`. They're exposed in the run-create dialog and the
schedule wizard. The group-dispatch path
(`app/tasks/action_group.py`) consults them before applying each
phase of the snapshot/verify/rollback envelope. The per-host path
(`app/tasks/action_host.py`) does not — it unconditionally applies
the envelope whenever the action is `destructive: true` and the
host has a Proxmox VM mapping. The UI presents the toggles as
controls but they have no effect on host-targeted runs.

**Sketch:**

- In `_run_action_host_async` (or wherever the per-host snapshot /
  verify / rollback logic lives), read `run.snapshot_enabled`,
  `run.verify_enabled`, `run.auto_rollback` and gate the
  corresponding phase on each. Match the pattern in
  `app/tasks/action_group.py` — the phases are the same shape,
  just N=1 instead of N=members.
- Extend an existing test in `tests/test_action_host.py` (or
  whichever file covers the per-host envelope) to verify each
  toggle suppresses its phase: `snapshot_enabled=false` skips
  Phase A; `verify_enabled=false` skips Phase D;
  `auto_rollback=false` leaves the snapshot in place on failure.

**Why it matters:** Without this, the per-host path silently
ignores operator choices in the run dialog. An operator who unticks
"Take snapshot" for a quick local change still gets a snapshot
taken — surprising at best.

---

## Action runtime — per-host advisory locks for action runs

**Context:** `host_sync_orchestrator.run_host_sync` acquires a
PostgreSQL advisory lock per host so concurrent sync requests for
the same host queue instead of trampling each other. The action
runtime (`app/tasks/action_host.py` and the newer
`app/tasks/action_group.py`) does NOT acquire any lock. That means
a sync and an action — or two concurrent actions — can target the
same host in parallel. Concrete failure mode: an `apt`-using
action runs at the same time as a `packages` sync; both fight over
`/var/lib/dpkg/lock`; one fails mid-flight.

**Sketch:**

- Wrap the per-host envelope in
  `_run_action_host_async` with the same per-host advisory lock
  pattern `host_sync_orchestrator` uses
  (`SELECT pg_advisory_xact_lock(host_id)` inside the run-time
  transaction). Hold for the lifetime of snapshot → playbook →
  verify → rollback.
- For `action_group.run_action_group`: acquire the lock for EVERY
  member host up front, before Phase A. The single
  ansible-playbook invocation touches all members in parallel, so
  partial locking creates the same race the sync path already
  protects against.
- Add a test that verifies a sync against host X is blocked by an
  in-flight action against host X (and vice versa). Use the
  existing `pending`/`running` queue pattern from
  `tests/test_host_sync_orchestrator.py` as a template.

**Why it matters:** Today the docs and CLAUDE.md describe per-host
advisory locks as a property of the action runtime. They aren't.
Either fix the implementation to match, or fix the docs to admit
the gap — preferably the former, since the original sync-side
implementation exists exactly because the race is real.
