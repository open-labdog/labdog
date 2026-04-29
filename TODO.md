# TODO

Open tasks that need design input or decisions before implementation.

---

## Pre-release checklist

Items to complete before tagging a public release. These are
scoped-and-decided; they just need doing.

**Status as of 2026-04-26**: 1 release-blocker left — publish
`labdog-playbooks`. Everything in Polish is intentionally not blocking
v0.1.0.

### Release blockers

- [x] **Frontend: workflow form is missing `action_key` and
      `action_parameters`.** Surfaced 2026-04-25. The per-group
      Workflow form hardcoded `linux-upgrade` and gave no UI to pick
      `linux-os-upgrade` / `k8s-upgrade` or enter their parameters,
      even though the DB model, API, and GitOps importer all
      accepted them. Fixed by adding an Action `<select>` driven by
      the live `/api/actions/` catalog plus a parameter-renderer
      block that walks `ActionDefinition.parameters` and renders
      the right input control per type (bool / choice / int /
      string), with required-marker asterisks and help text.
      Switching action resets the parameter dict.
- [x] **Frontend: firewall rule dialog rejects optional Port End.**
      Surfaced 2026-04-25. Root cause was the `setValueAs` handler in
      `frontend/components/rule-dialog.tsx` returning `parseInt(null,
      10) === NaN` on the register-time call (the user hadn't typed
      anything yet). Fixed in the handler — null/undefined and NaN
      results both coerce to null — and the schema's port fields
      switched from `.optional().nullable()` to the cleaner
      `.nullish()`.
- [x] **Frontend: groups list does not invalidate after create.**
      Surfaced 2026-04-25. The new-group page used a raw `apiFetch` +
      `router.push` and never touched the TanStack Query cache for
      `["groups-summary"]`. Fixed by pulling `useQueryClient` into the
      page and calling `invalidateQueries({ queryKey:
      ["groups-summary"] })` between the POST and the redirect.
- [x] **License decision + `LICENSE` file.** AGPL-3.0-or-later.
      Canonical FSF text landed in both labdog and labdog-playbooks;
      README license sections, `backend/pyproject.toml`, and
      `frontend/package.json` all declare it.
- [x] **`SECURITY.md` + vulnerability disclosure process.** Landed
      at the repo root with GitHub private vulnerability reporting
      as the primary channel, direct email as fallback, and
      documented in-scope / out-of-scope areas.
- [x] **Release-artifacts job in GitHub Actions.** Landed in
      `.github/workflows/ci.yml` as the `release-artifacts` job:
      runs on `v*` tag push (creates a GitHub Release via
      `softprops/action-gh-release`) and on `workflow_dispatch`
      for dry-runs (build-only; uploads to the workflow-run
      artifacts instead of a Release). Release process +
      maintainer version-bump convention documented in
      `CONTRIBUTING.md`.
- [x] **CHANGELOG.md with a v0.1.0 entry.** Landed at the repo
      root in Keep a Changelog format. v0.1.0 entry is a feature
      inventory grouped by capability area (host management,
      configuration modules, actions, update workflows, GitOps,
      discovery, drift detection, audit log, auth, operations) plus
      explicit known-limitations and stack sections. Date
      placeholder set to today; the maintainer adjusts at tag time
      per the release process in `CONTRIBUTING.md`. An "Unreleased"
      section sits at the top so subsequent commits have a place
      to land.
- [ ] **Publish labdog-playbooks.** Tag and push to
      `github.com/open-labdog/labdog-playbooks`. Docs already
      link to that URL aspirationally; the link 404s until this
      lands.
- [x] **GitOps schedule configurability — Phase 2 (global
      schedules).** Phase 1 landed in commit `ae3a8ac` (per-group
      `workflow:` section). Phase 2 lands the
      `drift.check_interval_minutes` setting and the independent
      `ScanConfig` rows under a new optional `_global.yaml` at the
      repo root. Implementation:
      `LabDogGlobalYAML` schema + `import_global_from_yaml`
      dispatcher (separate per-repo advisory-lock namespace), drift
      handler with leave-alone semantics, discovery handler with
      wipe semantics resolving `ssh_key` / `default_groups` by
      name. Webhook task reads `_global.yaml` once per delivery
      before the per-group loop; missing file is silent skip,
      typos abort the global import but don't block per-group
      imports. 16 integration tests; example file at
      `docs/examples/gitops/_global.yaml`.
- [x] **Sync preview is firewall-only.** Surfaced 2026-04-26 end-to-end
      module test, fixed 2026-04-26 via option (b) in the original
      writeup — relabel the tab and clarify its scope. The tab on
      `/groups/{id}` is now "Firewall Sync" with explanatory copy:
      "Previews and applies firewall rule changes only. Services,
      packages, /etc/hosts entries, cron jobs, users, DNS resolver,
      and CA certs sync from each module's own tab." The groups-list
      quick-action Sync button and host-detail Sync All button were
      already fanning out across modules; only the per-tab
      firewall-only path needed the rename. Option (a) — a single
      multi-module preview — remains a follow-on if operators later
      ask for it.
- [x] **GitOps end-to-end test pass.** Landed at
      `backend/tests/integration/test_gitops_e2e.py` — five
      `pytest -m integration` tests against a locally-hosted bare
      git repo (`file://` URL, no external infrastructure):
      multi-module group YAML covering all eight modules
      (firewall + services + packages + hosts entries + cron jobs
      + linux users + linux groups + resolver + workflow) including
      a second-push diff, `_global.yaml` round-trip with name-based
      cross-references for ssh_key + default_groups, GitHub
      webhook receiver with valid HMAC signature including the
      full task-body invocation (clone via `file://`, import,
      verify DB), invalid-signature rejection (401), and
      branch-mismatch ignore (200 ignored). Patches
      `task_session` + `clone_repo` so the task body runs against
      the test transaction. The older
      `tests/integration/test_gitops_workflow.py` (firewall-only
      flow + UI mutation lock) stays in place as the simpler
      lifecycle test.
- [x] **Backup + restore documentation.** Landed at
      `docs/backup-restore.md`: what to back up and what not to,
      `pg_dump` custom-format invocation, encryption-key handling,
      a systemd timer + script for daily backups, restore path
      for fresh-host and point-in-time, and disaster scenarios
      for lost-key / lost-DB / both-lost. Indexed from
      `docs/README.md` under a new Operations section.

### Strongly recommended

- [x] **`CONTRIBUTING.md`.** Landed at the repo root: dev setup
      pointer, test commands, conventional-commit style, PR
      process, inbound = outbound AGPL licensing, per-file-header
      policy (not required), code of conduct.

### Polish

- [x] **Auto-sync version strings from the tag.** The
      `release-artifacts` job now rewrites
      `backend/pyproject.toml` + `frontend/package.json` from
      `${{ github.ref_name }}` before `build.sh` runs (tag
      pushes only — `workflow_dispatch` dry-runs leave the tree
      untouched). `CONTRIBUTING.md` updated to drop the manual
      pre-tag bump.
- [x] **Encryption-key rotation runbook.** Landed in `f748031`:
      `backend/scripts/rotate_encryption_key.py` re-encrypts every
      `encrypted_*` column (ssh_keys, proxmox_nodes,
      git_repositories) under a new master key in one transaction;
      `docs/encryption-key-rotation.md` is the operator runbook;
      6 unit tests cover happy path, idempotent no-op, wrong-key
      rollback, and CLI key-validation. Note: the existing
      truncate-and-re-enter path in `docs/backup-restore.md`
      remains a valid alternative for installs with few
      credentials.
- [x] **Upgrade guide** (`docs/upgrade.md`). Landed in `fe7bbd9`:
      pre-upgrade backup checklist, Docker / .deb / .rpm upgrade
      mechanics, three-step verification, three rollback paths
      (tag pin, package downgrade, restore-from-backup). Notes
      explicitly that v0.1.0 has no prior version.
- [x] **Production deployment example.** Landed in `fe7bbd9` as
      `docs/production-deploy.md`: full `compose.yaml` with Caddy
      sidecar, named volume for `ansible.packs_root_dir`, env-only
      secrets, dev-vs-prod difference table, prominent pointer to
      the canonical .deb + systemd path.
- [x] **Security hardening page.** Landed in `fe7bbd9` as
      `docs/security-hardening.md`: nginx + Caddy reverse-proxy
      examples (with WebSocket upgrade for the SSH terminal), CSP
      block, `limit_req_zone` rate-limiting, `cookie_secure` /
      `force_https` flip, least-privilege Postgres grant, superuser
      scope guidance.
- [x] **Trivy / SBOM baseline review.** Landed in `fe7bbd9` as
      `docs/trivy-rationale.md`: per-CVE table for the six entries
      in `.trivyignore` (ncurses, systemd, two openssh-client,
      libexpat1, libnghttp2-14) with add-date, removal trigger, and
      re-evaluation steps via `apt-cache policy` + Debian
      security-tracker.
- [ ] **Retire `.gitlab-ci.yml`.** Keep during migration. Remove
      once the GitHub Actions pipeline has been green for ~two weeks
      and no one's relying on GitLab Pages / Packages.
- [x] **Audit-log coverage gaps.** Surfaced 2026-04-25, fixed
      2026-04-25. Added `log_action` to `create_group`,
      `update_group`, `delete_group`, `add_hosts_to_group`, and
      `remove_hosts_from_group` in `app/api/groups.py` with
      consistent `entity_type="host_group"` and verbs (`create`,
      `update`, `delete`, `add_hosts`, `remove_hosts`). Memberships
      record the actual host_ids changed in `before_state` /
      `after_state`. Audit page now shows the full lifecycle of
      group changes, not just workflow events.
- [x] **Audit log displays raw `User #1` instead of email.**
      Surfaced 2026-04-25, fixed 2026-04-25. The `/audit-log`
      endpoint now LEFT JOINs `users` and exposes a `user_email`
      field on each row; the audit page renders `user_email`
      with `User #N` / `System` fallbacks so deleted users still
      show usefully.
- [x] **404 → empty-body for "no config yet" endpoints.**
      Surfaced 2026-04-25, fixed 2026-04-25. Converted all six
      endpoints to return `200` with `null` (singletons) or `[]`
      (lists) instead of `404`: `/api/hosts/{id}/resolver`,
      `/api/hosts/{id}/effective-resolver`,
      `/api/hosts/{id}/latest-workflow-run`,
      `/api/proxmox/hosts/{id}/vm-mapping`,
      `/api/groups/{id}/workflow`, and
      `/api/groups/{id}/workflow/runs`. Frontend resolver
      branches updated to discriminate "not configured" via
      `data === null` instead of `error.status === 404`.
- [x] **Dashboard: "Check All" naming.** Surfaced 2026-04-25,
      fixed 2026-04-25. The button hit `/api/hosts/{id}/collect-state`
      (an SSH state-collection, not a drift check or sync push) yet
      was labelled "Check All" — confusing because Status flipped
      to "In Sync" while Last Check / Last Sync columns stayed at
      "Never". Renamed the dashboard button to "Collect State" (and
      the per-row variant to "Collect") with a tooltip explaining
      the distinction. Column labels left untouched — they
      correctly track different timestamps.
- [x] **Host detail tabs row overflows at ~1080px viewport.**
      Surfaced 2026-04-25, fixed 2026-04-25. Switched the host
      detail's `<div role="tablist">` from `overflow-x-auto` to
      `flex-wrap` so the 11 tabs wrap to a second row at typical
      laptop widths, matching what the Group detail page already
      did.
- [x] **Wide host-detail tables overflow when comments are long.**
      Landed in `f2e3f98` (2026-04-27): trimmed `defaultWidth` on
      Source/Destination/Group/Comment/Actions for both
      `current-state-firewall` (col-sum 790 → 660) and
      `host-effective-rules` (col-sum 1210 → 1000), with cell-level
      `inline-block max-w-[…] truncate` plus `title` attributes on
      Source/Destination/Comment so full text shows on hover. Did
      NOT reintroduce the framework-level `table-layout: fixed`
      change.
- [ ] **Manifest-validation CI check on labdog-playbooks.** Add a
      GitHub Actions job that validates every `*.manifest.yml`
      against `app.actions.manifest.ActionManifest.model_validate`.
      Catches typos in pack contributions before they reach a
      labdog instance.
- [ ] **Bulk-sync multiple groups from the Groups overview.**
      Surfaced 2026-04-27. The Groups list at `/groups`
      (`frontend/app/(dashboard)/groups/page.tsx`) already
      renders a per-row checkbox and tracks `selected` state for
      bulk delete (`handleBulkDelete`, line 143), but there's no
      bulk-sync action — operators have to click Sync per row.
      Add a "Sync selected" button to the bulk-action bar that
      reuses the multi-module fan-out from `handleSyncGroup`
      (line 218: firewall + services + hosts-mgmt + linux-users
      + cron + packages + resolver) across every selected group.
      **Race-condition status (updated 2026-04-29):** the
      dispatch-after-commit race (`NoResultFound` under load) was
      fixed in `d07692e` (BUG-37), so naive serial fan-out is
      now safe at the persistence layer. The remaining concurrency
      hazard — overlapping ansible-runner invocations against the
      same host when two groups share members — is the design
      target of the planned **coalesced per-host sync** redesign
      for v0.2.0 (one orchestrator task per host, single unified
      playbook).
      Until that lands, ship the UI as a serial per-group
      `POST` loop from the client (or a small server-side
      endpoint that does the same) so two selected groups that
      share a host queue rather than collide. Add an integration
      test that selects two groups sharing a host and asserts no
      two ansible-runner processes target that host concurrently
      — the test stays valid before and after Option C and pins
      the contract.
- [x] **Promote Discovery to a top-level MANAGE nav entry.**
      Landed in `f2e3f98` (2026-04-27): Discovery is now a sibling
      under MANAGE between Hosts and Groups. Pending stays a Hosts
      child only when `pendingTotal > 0`.
- [x] **Sync preview hides the "Managed by LabDog" fallback
      comment.** Landed in `f02593e` (2026-04-27) via option (a):
      `formatRule()` in
      `frontend/app/(dashboard)/groups/[id]/sync/client-page.tsx`
      now uses `r.comment || "Managed by LabDog"` so the displayed
      diff matches the line that actually lands on the host.
- [x] **"Add Hosts to Group" dialog needs Select-All.** Landed in
      `f2e3f98` (2026-04-27): header checkbox toggles every
      visible (post-filter) row with indeterminate state on
      partial selection.
- [x] **"Add to Group" dialog on host detail needs Select-All.**
      Landed in `f2e3f98` (2026-04-27) alongside the groups-side
      picker — same pattern in both places for UX symmetry.

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

