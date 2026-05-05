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

### Release blockers

- [ ] **Publish labdog-playbooks.** Tag and push to
      `github.com/open-labdog/labdog-playbooks`. Docs already
      link to that URL aspirationally; the link 404s until this
      lands.

### Polish

- [ ] **Add an "About" section to the UI.** Show version, license
      (AGPL-3.0-or-later), commit SHA / build date, and a link to the
      project repo. Useful for support ("which version are you on?")
      and required for AGPL attribution surfacing. Likely lives under
      Settings or as a footer link.
- [ ] **Verify `packaging/` is up-to-date and run its tests.** Walk
      `packaging/` (deb, rpm, tarball builders + install/uninstall
      scripts) against current `backend/` and `frontend/` layouts and
      systemd unit expectations — catch drift in file paths, baked-in
      versions, dependency lists, post-install hooks. Then run whatever
      test/validation harness exists (`packaging/tests/`, lintian, rpmlint,
      a smoke install in a container) and fix what fails.
- [ ] **Retire `.gitlab-ci.yml`.** Keep during migration. Remove
      once the GitHub Actions pipeline has been green for ~two weeks
      and no one's relying on GitLab Pages / Packages.
- [ ] **Manifest-validation CI check on labdog-playbooks.** Add a
      GitHub Actions job that validates every `*.manifest.yml`
      against `app.actions.manifest.ActionManifest.model_validate`.
      Catches typos in pack contributions before they reach a
      labdog instance.

---

## Bundled pack scope — keep 1:1 or slim down?

**Context:** The scaffold at `../labdog-playbooks/` is a 1:1 copy of
`backend/app/ansible/` (the bundled pack baked into the image). Both
ship the same three actions today. An operator adding
`labdog-playbooks` as a git Action Pack under Integrations → Git
Repos + Action Packs will then have the same three keys from two
sources; the external pack wins by priority tier and the bundled
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

## Proxmox VM provisioning — possible future feature (parked)

**Context:** Explored on 2026-04-23 as a potential direction. Parked —
not on the near-term roadmap. Captured here so the scoping conversation
doesn't need to happen from scratch next time.

**What was explored:** Turning LabDog into a Proxmox VM provisioning
tool, reusing the existing terraform + K8s install scaffolding (kept
locally, not in the public repo) as the starting point. Goal stretched
toward "one-click Kubernetes cluster."

**Scope options on the table:**

- **Narrow — "Clone & adopt" as a Hosts extension.** Wrap the existing
  `ProxmoxClient` with clone/configure methods, add a `Hosts → Add →
  Create new VM` flow, auto-register the VM as a LabDog host. No
  terraform, no tfstate, no new nav. ~1/3 the scope of the IaC option.
- **Full IaC subsystem.** Bundle OpenTofu in the container image,
  persist tfstate on a volume, model `Blueprint` / `Job` / `VM` tables,
  two-phase plan→approve→apply flow, dedicated "Provisioning" nav.
  Multi-VM replicas supported. Significantly larger.
- **Pack action only.** Package the existing terraform+K8s roles as a
  destructive pack action. Zero new LabDog features. Fits the pack
  system that shipped in the same iteration as this doc.

**Product-fit assessment (why it's parked):**

LabDog's sharp edge is "small-fleet Linux config management with a
UI." Provisioning is adjacent; full IaC + native K8s cluster install
drifts into "homelab platform / Rancher competitor" territory where
dedicated tools (Terraform, Kubespray, RKE2, Talos) are stronger. The
narrow "clone & adopt" path is defensible; the full stack is not,
without a deliberate product pivot.

**Distance to "one-click K8s cluster" if we ever pursue it:**

Three gaps beyond the V1 IaC plan:

1. **Multi-host coordinated action execution** — LabDog's action system
   is one-playbook-per-host. K8s init needs master-first, token flow,
   then workers. Either add "group-level execution" mode to actions
   (broader benefit) or make K8s a provisioning-specific recipe
   (narrower, faster).
2. **Harden the K8s install roles** — exist but are described as
   "barebones (no HA, no persistent storage, no observability)."
3. **Post-provision recipe wiring + UI** — new blueprint field, new
   step in the Celery task, UI to pick recipe and view install
   progress, kubeconfig artifact download.

Order-of-magnitude: V1 provisioning ~2–4 weeks, post-provision K8s
recipe + UI ~2–3 weeks on top. Lab-grade cluster only, not production.

**If we revisit:**

- The detailed implementation plan for "Full IaC subsystem" was
  drafted during the exploration and lives in the maintainer's
  local working notes. Can be regenerated if it's gone.
- Decision locked during exploration (not binding on a revisit):
  OpenTofu over Terraform (MIT vs BUSL), two-step plan→approve→apply,
  multi-VM replicas in V1, dedicated nav entry.
- Before committing to *any* scope here, clarify the product
  narrative. The three scopes correspond to meaningfully different
  products ("config manager," "homelab platform," "Rancher-lite").
  This is an exec-level choice, not an engineering one.

**Strong default if we do nothing else:** pack-action-only. The pack
system already exists; wrapping the terraform+K8s roles into a pack
gives homelab users the K8s story with zero LabDog feature creep.

---

## Multi-host coordination — upgrade a K8s cluster in one click

**Context:** LabDog's action system fans out per-host — each host
gets its own `ActionHostRun`, runs its playbook independently, and
exits. That's fine for `linux-upgrade` but wrong for anything where
hosts need to coordinate: K8s cluster upgrade drains one node at a
time and needs the control plane up throughout; the existing local
terraform/k8s install scaffolding uses a single playbook against
all hosts with shared facts via a dummy host.

**User's real target:** "upgrade a K8s cluster with a single click."
Not the full provisioning story — just lifecycle ops on an existing
cluster. Similar needs to one-click provisioning but with narrower
scope.

**Decisions needed before implementing:**

1. **Single-playbook-many-hosts vs. staged per-host.** Two shapes:
   - **Group-level execution:** one ansible-runner invocation with
     an inventory containing every host in the group. Playbook uses
     `serial:`, `when:`, `delegate_to:` to orchestrate. Matches how
     Ansible was designed to be used. Requires a new execution path
     distinct from the current per-host fanout.
   - **DB-mediated baton pass:** per-host runs as today, but they
     read/write shared state (Redis key? DB row?) to hand off
     tokens/state. Fragile; forces playbook authors to learn a
     LabDog-specific coordination protocol. Reject.
2. **Ordering.** Some actions need control-plane nodes upgraded
   before workers. Does the manifest declare ordering, or is the
   playbook responsible via `hosts:` patterns and `serial:`? Lean:
   let the playbook handle it (matches Ansible idiom); LabDog just
   builds the multi-host inventory from the selected group.
3. **Snapshot semantics for group runs.** Take a snapshot of every
   mapped VM before the run, roll back all on any failure? Or per
   host, and leave partially-upgraded clusters to the operator?
   Probably all-snapshots-up-front, bulk-rollback on failure.
   Expensive on storage; possibly opt-in via manifest.
4. **Progress reporting.** The UI today shows per-host progress.
   A single ansible-runner invocation produces one log stream —
   the UI needs a different view ("cluster upgrade" rather than
   "3 hosts running the same thing").
5. **Failure atomicity.** What happens if host 3 of 5 fails? The
   current answer for per-host runs is "keep going, each host's
   state is independent." For cluster ops the answer is
   "half-upgraded K8s control plane is worse than not-upgraded —
   stop and roll back." Manifest flag `stop_on_first_failure`?

**Implementation sketch (pre-plan):**

- New manifest flag `execution: per_host | group` (default
  `per_host`; existing actions stay as they are).
- When `execution=group`: the orchestrator creates a single
  `ActionRun` with no `ActionHostRun` fanout; a new
  `GroupActionExecution` record carries the single stream.
- `run_ansible()` called once with a multi-host inventory generated
  from every host in the selected group. Bundled role paths stay
  the same.
- Snapshot orchestration: sequential `create_snapshot` per mapped
  VM before the run; `rollback_to_snapshot` for each on failure.
  Budget this carefully — 10 nodes × 30s snapshot latency is real.
- UI: new run-detail layout that shows ansible-runner's play
  recap per host instead of one-stream-per-host.

**Blast radius / risk:** meaningful. Touches the orchestrator, task
runner, SSE shape, UI run detail. Not a weekend feature.

**Priority:** high, but gate behind a proper plan document (do
Phase 1 exploration + design agent before implementing). K8s
upgrade is the concrete first customer; a one-off special case for
that playbook is tempting but would bifurcate the action system.
Solve the general case or don't solve it at all.
