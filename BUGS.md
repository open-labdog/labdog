# Bug Registry

Open bugs in LabDog. New entries are added as bugs are surfaced.

## Convention: open-only

**Only open bugs belong in this file.** When a bug is fixed:

1. Land the fix and write a descriptive commit message that references
   the bug ID (e.g. `fix(sync): BUG-37 — dispatch Celery tasks after
   commit`). That commit message is the canonical record (symptom,
   root cause, fix).
2. Delete the entry from this file in the same commit. Do **not** mark
   bugs `[x]` and leave them here — fixed entries belong in git history,
   not in the registry.

To retrace a historical bug ID referenced elsewhere
(`BUG-NN`, `SEC-NN`, `TYPE-NN`, `DEAD-NN`), search the commit log:

```
git log --grep BUG-37
git log -- backend/app/api/sync.py
```

## How to file an entry

Format each entry as:

    - [ ] **BUG-NN** `path/to/file.ext:LINE` — one-line summary

      Symptom, root cause, severity tier (Critical / High / Medium /
      Low). If reproduced from a specific scenario, note it. Group
      related bugs under the same severity heading.

ID counter as of last housekeeping pass: `BUG-57`, `SEC-19`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Security findings — High

Filed 2026-05-21 from a `security-auditor` whitebox source-level
review (see the `code-audit` branch history for the baseline). Each entry was
spot-checked against current HEAD before filing.

_No bugs are currently open._

### Reliability — Medium

- [ ] **BUG-55** `Dockerfile:160` — the app runs as container PID 1 and never
      reaps orphaned grandchildren, so every `ssh` that outlives its `git`
      parent becomes a permanent zombie.

      **Symptom.** Zombie `ssh` processes accumulate on the container host for
      as long as the container runs. Observed on a production instance
      (`openlabdog/labdog:test`): 115 zombies — 114 `ssh`, 1 `git` — all
      parented to the `python -m app` PID, accruing at roughly 26/day over 4.4
      days of container uptime. Each zombie holds a task slot until the parent
      exits, so the count only ever grows.

      **Root cause.** `Dockerfile:160` is `CMD ["python", "-m", "app"]` with no
      init as ENTRYPOINT, so the application is PID 1. `subprocess.run` in
      `backend/app/actions/git_sync.py:50` correctly reaps `git` itself, but
      `git` spawns `ssh` for SSH remotes (`GIT_SSH_COMMAND`, set in
      `backend/app/packs/git_auth.py:87` and
      `backend/app/gitops/git_service.py:87`). When `ssh` outlives `git` it is
      orphaned, re-parented to PID 1 — the app — and there it stays: nothing in
      `backend/app/` installs a `SIGCHLD` handler or calls `waitpid`, so the
      orphan is never reaped.

      **Severity: Medium.** Not urgent at the observed rate — the affected host
      sat at 571 of 31,008 threads, about three years of headroom — but the leak
      is unbounded and scales with git-backed pack sync frequency, so a busier
      instance leaks proportionally faster. The end state is severe and not
      gracefully recoverable: once a host cannot `fork()`, it cannot start or
      stop containers, `exec` into them, or accept SSH logins, and only a reboot
      clears it. A Kubernetes node was taken out this way by an unrelated
      controller with the same PID-1 defect on 2026-08-19.

      **Reproduce.** Run the published image without `--init`, configure a
      git-backed action pack over SSH, and let pack sync run for a few days.
      `ps -eo stat,ppid,comm | awk '$1 ~ /Z/'` on the host shows the pile, all
      parented to the app PID.

      **Fix.** This should not depend on the deployment remembering to pass
      `--init` — the published image is run by people who will not. Either add
      an init as ENTRYPOINT (tini or dumb-init, `ENTRYPOINT ["/usr/bin/tini",
      "--"]`), or reap orphans in-process at startup when `os.getpid() == 1`.
      Narrowing `GIT_SSH_COMMAND` with `-o ControlMaster=no -o ControlPersist=no`
      would stop `ssh` outliving `git` in this specific path, but leaves the
      general PID-1 defect in place for any future subprocess.

      Deployments can mitigate today with `init: true` on the compose service;
      that has been applied to the lin-manager stack in `infra/docker-gitops`,
      which is what surfaced this.

### Merge correctness — Medium

- [ ] **BUG-57** `backend/app/hosts_mgmt/merge.py:118` — per-entry
      `priority` is decorative in five modules; a same-key entry
      silently overwrites instead.

      **Symptom.** Adding a hosts-file override for an IP that already
      has an entry replaces the existing one without warning. Raising
      the new entry's **Priority** does not change the outcome, and
      neither does lowering it.

      **Root cause — two defects that compound.**

      1. *The merge keys on identity alone and overwrites.* Host-level
         entries are applied as `merged[ip] = ...` with no membership
         check ([`merge.py:118-129`](backend/app/hosts_mgmt/merge.py)),
         so the last row read wins. Which row that is depends on an
         unordered `SELECT`, so the winner is not even stable. Group
         entries use the opposite rule — `if ip not in merged`,
         first-wins in `HostGroup.priority DESC` order.
      2. *`priority` is never consulted.* `HostsEntry.priority` exists
         as a column, is validated `ge=0, le=10000` in the schema, and
         is rendered as a form field — but no merge engine reads it.
         The only priority that affects any outcome is
         `HostGroup.priority`, which orders the groups.

      **Same issue, other modules.** The dead per-entry `priority`
      column is not specific to hosts entries. In each of these the
      merge reads `HostGroup.priority` only:

      | Module | Merge key | Reads entry `priority`? | Unique constraint? |
      |---|---|---|---|
      | `hosts_mgmt` | `ip_address` | No | **No** |
      | `services` | `service_name` | No | **No** |
      | `cron` | `(name, user)` | No — passthrough to response only | **No** |
      | `user_mgmt` | `username` / `groupname` | No | **No** |
      | `packages` | `package_name` | No — passthrough only | Yes |

      `packages` is the one that already behaves: it carries
      `uq_package_rules_group_pkg` / `uq_package_rules_host_pkg`, so a
      duplicate is refused at write time rather than silently resolved
      at merge time. The other four have no unique constraint on
      `(scope, key)`, which is why the collision surfaces as a silent
      overwrite.

      `rules` (firewall) is **not** affected — it genuinely orders by
      group priority then `rule.priority`
      ([`rules/merge.py:83`](backend/app/rules/merge.py)).

      **Fix direction** (not yet done). Either honour `priority` in the
      merge or remove it from the UI and schema — but not leave a
      control that reads as if it disambiguates and does nothing. The
      narrower fix the reporter asked for is to make the field
      unavailable and say so. Adding the missing unique constraints,
      following the `packages` precedent, would turn the silent
      overwrite into an honest error at the point of entry. Note that
      keying hosts entries on `ip_address` alone also forbids two
      hostnames sharing an IP, which is legitimate in `/etc/hosts`.

      Reported 2026-08-21 from the **Add Hosts Entry Override** dialog.
