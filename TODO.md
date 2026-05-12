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

- [ ] **Add an "About" section to the UI.** Show version, license
      (AGPL-3.0-or-later), commit SHA / build date, and a link to the
      project repo. Useful for support ("which version are you on?")
      and required for AGPL attribution surfacing. Likely lives under
      Settings or as a footer link.
- [ ] **Smoke-test the packaging artifacts.** A static audit pass over
      `packaging/` is done (license/homepage/maintainer fixed, maintainer
      scripts made deb+rpm portable, systemd unit hardened, TOML config
      brought in line with dev). Still TODO: actually build the .deb /
      .rpm / .tar.gz and verify they install cleanly on Debian 12 and
      Rocky 9, the service starts, and `--purge` / `rpm -e` clean up
      properly. Add `packaging/tests/` with a container-based smoke test
      so the CI release-artifacts job can gate on it.
- [ ] **Manifest-validation CI check on labdog-playbooks.** Add a
      GitHub Actions job that validates every `*.manifest.yml`
      against `app.actions.manifest.ActionManifest.model_validate`.
      Catches typos in pack contributions before they reach a
      labdog instance.
- [ ] **Surface the active action catalog on `/action-packs`.** Today
      the only way to see which actions a pack contributes — and which
      pack ultimately wins each key — is to open a host or group and
      look at its Actions tab. Add a panel on the **Action Packs**
      page that lists every key in the live registry with its winning
      pack, the candidates it shadows, and a link to the resolution
      modal when contested. Lets operators audit pack precedence
      without picking an arbitrary host first. Data is already there
      via `GET /api/actions/` (winners + `overridden_from`) and
      `GET /api/action-resolutions` (contests).

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
- After a successful action run (both per-host and cluster-mode
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
