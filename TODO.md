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




