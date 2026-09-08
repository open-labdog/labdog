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

ID counter as of last housekeeping pass: `BUG-81`, `SEC-35`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Security findings — High

Filed 2026-05-21 from a `security-auditor` whitebox source-level
review (see the `code-audit` branch history for the baseline). Each entry was
spot-checked against current HEAD before filing.

_No bugs are currently open._

### AI command classifier — Low

- [ ] **BUG-60** `backend/app/ai/safety.py:452` — `2>&1` is classified
      `mutating`, because the segment splitter breaks on the `&`.

      **Symptom.** Any command ending in the most common redirection
      idiom — `systemctl status sshd 2>&1`, `ls -l 2>&1` — is refused in
      a read-only AI session and raises an approval request in an
      approval session, despite writing nothing.

      **Root cause.** `_SEGMENT_SPLIT` splits on `[;|&\n]`, so
      `ls -l 2>&1` becomes the two segments `ls -l 2>` and `1`. The
      second has head `1`, which is not on `READ_ONLY_HEADS`, so
      default-deny classifies the pipeline as `mutating`. The redirect
      rule itself is innocent here and is asserted not to fire on an fd
      dup (`tests/ai/test_safety.py::TestRedirection`).

      **Severity: Low.** Fails safe — over-classification costs an
      approval prompt, never an unintended write. Filed because the
      idiom is common enough that the prompts read as noise, and noisy
      prompts are how an operator learns to approve without reading.

      **Fix direction.** Teach `_segments` to recognise fd-dup
      redirections before splitting, e.g. mask `\d*>&\d+` out of the
      line, split, then restore. Deliberately not done alongside the
      2026-09 classifier hardening: changing how a command is cut into
      segments is the single edit most able to reopen the bypasses that
      work closed, and it wants its own diff and its own review rather
      than riding along in a security fix.

      Predates the hardening — verified against `dev` at `c784afb`, not
      introduced by it.

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

---

## Open — 2026-09 whitebox audit

Filed 2026-09-03 from a full whitebox review (security, correctness,
performance) of the backend, frontend, packaging and CI. Every entry was
verified against source at `c784afb` before filing; the ones already
fixed are absent rather than ticked, per the open-only convention.

Findings from the same pass that have already landed are absent rather
than listed: the reflected XSS in the SPA dynamic-route rewrite, the AI
command-classifier bypasses, action-parameter template injection, pack
manifest path containment, and the quick-win batch (unauthenticated
resolver reads, registration privilege flags, `retention_days=0` wiping
the audit log, the scheduler tick poisoning itself, two temp-dir leaks,
the sync-tray request loop, the CSRF-less password change). Search the
log: `git log --grep "SEC"`.

**Note on the privilege model.** LabDog is deliberately flat — every
authenticated user has the same permissions and `is_superuser` gates only
user administration. That is a confirmed design decision, not a finding.
Its consequence shapes the severities below: the AI classifier, the
action-parameter validator and pack content policy are the *only* controls
protecting host root from an ordinary account. All three are now in
place: the classifier hardening, the extra-vars validator, and the pack
content policy.

### Security — Medium

### Security — Low

- [ ] **SEC-35** Grafana, Loki, Mimir and AI-provider base URLs are
      scheme-checked but may point at loopback, RFC1918 or 169.254.169.254
      (`app/grafana/schemas.py:16-25`, `app/ai/schemas.py:25-33`). Close to
      blind — responses must parse as the expected shape and httpx does not
      follow redirects by default — and a documented homelab decision. Git
      repo URLs *do* block these (`app/schemas/git_repos.py:18-25`). Filed
      so it is not re-discovered as new, not because it needs changing.

### Correctness — High

_No bugs are currently open._

### Correctness — Medium

- [ ] **BUG-81** `backend/app/tasks/builtin_dispatchers.py:_sync_async` —
      `_builtin.sync` reports `succeeded` when the underlying sync was
      deferred, not run.

      **Symptom.** A scheduled `_builtin.sync` whose host is genuinely
      busy finishes as `succeeded` with the sync still queued. The
      `SyncJob` is re-dispatched later by the host queue, so the work
      does happen — but the action-run history says it happened at a time
      it did not, and an operator reading the run list has no way to tell
      a real sync from a deferred one.

      **Root cause.** The `status == "deferred"` branch falls through with
      `succeeded` unchanged, on the reasoning that a defer "is not a
      failure either". True, but neither is it success.

      **Severity: Low.** Only misreports; the sync is not lost. Split out
      of the BUG-78/79/80 fix, which was about the path not working at
      all — this is about what it says when it does.

      **Fix direction.** `ActionHostRun` has no "deferred" terminal
      status, so this needs either a new one or the run staying `pending`
      with `pending_reason` set and the parent left un-finalised until the
      queued sync completes. The second is more honest and more work.
