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

## Mirror gitlab labdog-playbooks to github (resolves BUG-46)

**Context:** Commit `8fb167d` switched the bundled action pack from
an in-repo mirror to a build-time clone from
`open-labdog/labdog-playbooks` at the SHA pinned in
`LABDOG_PLAYBOOKS_REF`. The current pinned SHA is
`e6e73728ea3692a5189553d51c225e7961517000` — that commit only
exists on the maintainer's internal gitlab
(`gitlab.lan.tyresson.se/dennis/labdog-playbooks.git`); github
`open-labdog/labdog-playbooks` still has the old flat-layout
content (no alloy-install, no idempotent k8s-upgrade, no
directory-per-action layout).

**Consequence today:** every build path is broken without an env
override. github CI, public `docker build`, `packaging/build.sh`,
and `./dev/dev.sh start` (without `LABDOG_PLAYBOOKS_LOCAL`) all
attempt the github clone + checkout and fail because the SHA
doesn't exist there. Workarounds: set
`LABDOG_PLAYBOOKS_REPO=https://gitlab.lan.tyresson.se/dennis/labdog-playbooks.git`
as build-arg / env, or use `LABDOG_PLAYBOOKS_LOCAL` for dev. See
`BUGS.md` BUG-46.

**Procedure to resolve:**

1. Push gitlab `labdog-playbooks/main` → github
   `open-labdog/labdog-playbooks/main`:
   ```
   cd /home/dennis/priv/gitlab/labdog-playbooks
   git remote add github https://github.com/open-labdog/labdog-playbooks.git  # if not already
   git push github main
   ```
2. Capture the resulting github commit SHA:
   ```
   git ls-remote https://github.com/open-labdog/labdog-playbooks main
   ```
3. Bump `LABDOG_PLAYBOOKS_REF` in the labdog repo to that SHA.
   Commit as `build: bump LABDOG_PLAYBOOKS_REF to <sha> (gitlab -> github sync)`.
4. Verify a clean build works without overrides:
   ```
   docker build --build-arg LABDOG_PLAYBOOKS_REF=$(cat LABDOG_PLAYBOOKS_REF) -t labdog:test .
   ```
   and confirm `/app/app/ansible/actions/` lists the four current
   actions (alloy-install, k8s-upgrade, linux-os-upgrade,
   linux-upgrade).
5. Delete this TODO entry + BUG-46 in the same commit.

**Going forward:** treat gitlab → github mirroring as a one-shot
during this transition. After this lands, develop directly on
github (or push gitlab → github regularly via a sync hook). The
two repos drifting again would re-create this exact problem.

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

