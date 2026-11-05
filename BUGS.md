# Bug Registry

Open bugs in LabDog. New entries are added as bugs are surfaced.

## Convention: open-only

**Only open bugs belong in this file.** When a bug is fixed:

1. Land the fix and write a descriptive commit message that references
   the bug ID (e.g. `fix(sync): BUG-37 — dispatch Celery tasks after
   commit`). That commit message is the canonical record (symptom,
   root cause, fix).
2. Delete the entry from this file in the same commit, and its heading
   and intro when it was the last entry under them. Do **not** mark
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

ID counter as of last housekeeping pass: `BUG-124`, `SEC-38`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

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

### Security — Low

- [ ] **SEC-35** Grafana, Loki, Mimir and AI-provider base URLs are
      scheme-checked but may point at loopback, RFC1918 or 169.254.169.254
      (`app/grafana/schemas.py:16-25`, `app/ai/schemas.py:25-33`). Close to
      blind — responses must parse as the expected shape and httpx does not
      follow redirects by default — and a documented homelab decision. Git
      repo URLs *do* block these (`app/schemas/git_repos.py:18-25`). Filed
      so it is not re-discovered as new, not because it needs changing.

---

## Open — 2026-10-29 valheim

Filed 2026-10-29 while tracing why a hosts sync rewrote `/etc/hosts` on
the valheim game-server VM (BUG-115 to BUG-117 and BUG-119, since fixed),
against lin-manager's `sync_jobs` and audit rows. BUG-120 and BUG-121
turned up while fixing those, by reading the code.

### Correctness — Medium

- [ ] **BUG-118** `backend/app/hosts_mgmt/merge.py:95`,
      `backend/app/hosts_mgmt/diff.py:34-35` — hosts entries that share an
      address collapse into one line, and the lower-priority names are
      dropped without a word

      Symptom: lin-manager's `add-hosts` group maps both
      `loki.lan.tyresson.se` (priority 100) and `mimir.lan.tyresson.se`
      (priority 101) to 10.10.200.3. nextcloud's effective view and its
      file preview carry only the mimir line, and nothing in the UI says
      why. Its `/etc/hosts` still has both: the file was last written
      2026-08-21, five minutes after LabDog's sync, apparently by hand. The
      next hosts sync there drops loki.

      Root cause: the merge is keyed on the resolved address (`first_wins`
      with the IP as key), so per address only the highest-priority entry
      survives and its names replace the others'. An `/etc/hosts` line may
      carry any number of names, and two lines for one address are valid
      too. The diff and the collector use the same key, so a host file with
      two lines for one address is also read as one.

      Workaround: put every name for an address in one entry, as hostname
      plus aliases.

      Severity Medium: a configured name silently does not resolve.

- [ ] **BUG-120** `backend/app/tasks/builtin_dispatchers.py:565-569` — a
      `_builtin.sync` row records neither its module type nor its modules

      Symptom (by reading): `_sync_async` creates its `SyncJob` with no
      `module_type`, so the column takes its server default `firewall`, and
      no `module_filter`. The modules are passed to the inner run only. If
      the host is busy the job defers, and the queue later re-dispatches it
      from the row: `module_filter_for` reads `firewall`, so a scheduled
      sync of every module applies the firewall alone and reports success.
      The same default makes the insert collide with an operator's pending
      firewall sync on `uq_sync_job_active`, and the run fails with a
      duplicate-key error.

      Severity Medium: a scheduled sync silently does less than it says.

### Correctness — Low

- [ ] **BUG-121** `backend/app/ca_certs/actions.py:72-78` — a CA
      certificate deploy is dispatched before its row is committed

      Symptom (by reading): `enqueue_ca_cert_action_for_host` flushes the
      `SyncJob` and calls `run_ca_cert_action.delay` at once, leaving the
      commit to its five callers — the BUG-37 pattern. A worker that picks
      the task up first cannot see the row. It used to raise and leave the
      row `pending` for good; since the deploy takes its turn in the host
      queue (BUG-119) it returns "deferred", and the row waits for the next
      operation on the host to release it.

      Severity Low: a deploy that should start at once can wait; it is no
      longer lost.

---

## Open — 2026-11-05 assistant session 39

Filed 2026-11-05 from assistant session 39 on lin-manager, where two
questions about missing Mimir metrics on `media` each got a summary of the
first turn. Its Claude Code transcript shows what the model was really sent.

### Correctness — High

- [ ] **BUG-122** `backend/app/tasks/ai_task.py:151`,
      `backend/app/ai/agent_sdk/runner.py:945` — a follow-up on a Claude
      subscription session never reaches the model

      Symptom: the operator's question lands in the transcript, and the
      model is asked the session's mission again. It says it has already
      looked into that, and re-summarises. Session 39 was resumed twice,
      and the CLI's own transcript has the mission three times and neither
      question. Root cause: `run_chat_session` builds the runner without a
      prompt, so `_exchange` falls back to `session.mission`. Only approvals
      pass a prompt. `AgentLoop` replays the stored transcript, so the HTTP
      backends are not affected.

      Severity High: every follow-up on a `claude_agent` provider is lost,
      and the reply reads like an answer.

### Correctness — Medium

- [ ] **BUG-123** `backend/app/ai/agent_sdk/runner.py:1128-1135` — the
      token cap forgets earlier exchanges of a resumed session

      Symptom (by reading): `_cap_hit` compares the cap with the larger of
      the session's booked tokens and this run's live estimate, not their
      sum. A follow-up on a session with 89,925 of 100,000 tokens booked is
      not stopped until the follow-up alone has spent 100,000. BUG-122 hid
      this: with the mission sent again, a follow-up had nothing left to do
      and ended after one response.

      Severity Medium: `ai.max_tokens_total` is documented as the limit for
      a session, and a session can spend about twice that.

- [ ] **BUG-124** `backend/app/api/ai.py:578-623`,
      `frontend/app/(dashboard)/assistant/client-page.tsx:399` — a session
      out of budget takes a follow-up, and answers with a summary

      Symptom: tokens and commands are counted over the whole session, so
      once one is spent every follow-up is accepted, stopped at the model's
      first response, and made to summarise. Session 39 did this at 10:38.
      The summary sits in the transcript as the reply, and the only sign is
      a tag in the page header, which reads the same on a run that stopped
      long ago. `stopped_reason` is also never cleared, so a follow-up that
      finishes properly keeps the old tag.

      Severity Medium: the operator cannot tell that the question was not
      worked on, or why.
