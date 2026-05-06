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
