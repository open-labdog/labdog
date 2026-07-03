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

- [ ] **Audit GitHub Actions pins for Node 24 readiness (low priority).**
      GitHub is deprecating the Node 20 runtime on Actions runners; the
      runner default has already moved to Node 24 (surfaced as a warning
      during the v0.6.1 release run, e.g. under `actions/deploy-pages`).
      Nothing fails on Node 24 today, so this is not urgent — but before
      Node 20 support is fully removed, sweep `.github/workflows/*.yml`
      for any action pinned to a version whose runtime is Node 20 and
      bump to a Node 24-compatible release, so no workflow starts failing
      when the old runtime is dropped.

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

## Grafana metrics — follow-ups

**Context:** 0.4.0 shipped instant CPU/memory/disk on the host page,
querying the **default** Grafana instance by the `labdog_host_id` label
that the alloy-install action stamps. A few deliberate deferrals:

- **Per-host metrics backend routing.** Today every host is queried
  against the single default Grafana instance. Add a nullable
  `host.metrics_instance_id` FK, set post-run when alloy-install runs
  against a host with a chosen instance, and query that instead of the
  default — so different hosts can report to different backends. (Needs
  a post-run linking hook analogous to `post_run_register`.)
- **Loki log surfacing** on the host page (the integration already
  stores the Loki push URL; querying/displaying logs is unbuilt).
- **More metrics / tuning:** network throughput, per-mount disk, and
  operator-configurable thresholds + refresh interval.

---

## Dependency & supply-chain follow-ups (2026-07 code audit)

**Context:** The 2026-07 code audit's security, correctness, and cleanup
findings were fixed on the `code-audit` branch (see its `git log` — each
commit is the canonical record). The vulnerable dependency floors were
raised (`cryptography>=49`, `gitpython>=3.1.49`, `asyncssh>=2.23.1`,
`starlette>=1.0.1`, `python-multipart>=0.0.30`) and `backend/uv.lock`
added. These are the deferred hardening/maintenance tasks that remain.

- [ ] **Migrate ESLint 9 → 10 (frontend).** ESLint v9 reaches EOL ~2026-08-06.
      Flat config is already in place (`eslint.config.mjs`), so this is just the
      version bump — but it is **currently blocked upstream**: bumping `eslint`
      to 10 crashes lint with `context.getFilename is not a function`, because
      `eslint-config-next` (even the latest 16.2.10) bundles
      `eslint-plugin-react@7.37.5`, which still calls the API ESLint 10 removed.
      Re-attempt once `eslint-plugin-react` ships an ESLint-10-compatible
      release and `eslint-config-next` picks it up (then just bump both).

- [ ] **`lucide-react` 0.577 → 1.x.** Breaking (brand icons removed) — plan
      separately; the safe react-query / tailwindcss / zod / react-hook-form
      minor bumps have already landed.

