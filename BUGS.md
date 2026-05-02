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

ID counter as of last housekeeping pass: `BUG-41`, `SEC-06`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### High

### Low

- [ ] **SEC-06** `backend/app/sync/orchestrator.py:163` — orchestrator exception messages may leak the tmpfs SSH-key path back to the API caller

  When the orchestrator raises (e.g., the SSH-key file open fails with
  `OSError`, or any later step throws while `ssh_key_path` is part of
  the exception's traceback), the Celery wrapper at
  `app/tasks/host_sync_orchestrator.py:563` captures
  `str(exc) or exc.__class__.__name__` and writes it verbatim to
  `SyncJob.error_message` and every per-module
  `HostModuleStatus.error_message`. Both columns are returned by
  authenticated GET endpoints (`/api/sync/jobs/{id}` and the host
  detail / module-status views) to any active user. The leaked path
  takes the form `/dev/shm/labdog-sync-XXXXXXXX/id_ssh` — non-secret
  on its own (the file is 0o600 inside a 0o700 directory the same
  user already controls), but it discloses (a) the use of `/dev/shm`
  for SSH key staging, (b) the `labdog-sync-` prefix convention, and
  (c) the per-run random suffix. None of these are credentials, but
  they are operational details that a hardened deployment would
  prefer not to surface to non-superuser users via the jobs API.
  Note: with the SEC-03 fix tightening the bulk endpoint to
  superuser, the disclosure surface for *bulk-triggered* failures is
  restricted to admins. The per-tab endpoints
  (`/api/sync/hosts/{id}/sync` etc.) already required superuser
  pre-audit, so the same applies there. Worth filing because
  exception traces are a common path for accidental secret leakage,
  and the next-tier failure mode (e.g., a `subprocess.CalledProcessError`
  whose `cmd` attribute carries command-line arguments) could leak
  more sensitive data without any code change here.
  Severity: Low.
  Trigger: any orchestrator failure during a bulk or per-tab sync —
  e.g., decrypt with a rotated `encryption_key` (raises `InvalidTag`),
  point ansible-runner at an unreachable host, or any I/O error on
  the SSH-key write.
  Design tradeoff (why not fixed inline): the right fix is a
  redaction layer at the Celery wrapper (sanitise
  `orchestrator_error` before persisting it), but the redaction
  rules need design — strip paths under `/dev/shm`, strip anything
  resembling a private-key PEM block, etc. A naïve "hide all error
  details" would harm debuggability, which is the whole point of the
  per-module `error_message` column. Best treated as a follow-up
  pass that touches both the Celery wrapper and the Pydantic
  response models that surface `error_message` to API consumers.
