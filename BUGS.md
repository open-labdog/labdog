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

ID counter as of last housekeeping pass: `BUG-44`, `SEC-06`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Medium

- [ ] **BUG-45** `backend/app/crypto/key_management.py:13` — `ENCRYPTION_KEY` decoder silently truncates url-safe base64 input

  Symptom: when `LABDOG_SECURITY__ENCRYPTION_KEY` contains `-` or `_`
  (i.e. url-safe base64 — what `secrets.token_urlsafe()` or
  `base64.urlsafe_b64encode()` produces), startup succeeds and most
  endpoints work, but any operation that encrypts/decrypts via the
  master key fails. Visible failure mode: `POST /api/ssh-keys` returns
  500 with `ValueError: ENCRYPTION_KEY must decode to exactly 32
  bytes, got 30` in the backend logs.

  Root cause: `base64.b64decode(raw)` defaults to `validate=False`, so
  the `-` and `_` characters (not in the standard alphabet) are
  silently dropped, producing a shorter byte string. The length check
  catches the symptom but not the cause, and the error message doesn't
  mention which base64 variant is expected.

  The env var name, `generate_master_key()`, and any docs don't
  specify standard vs url-safe — Python developers commonly reach for
  url-safe by default, so this is easy to hit.

  Fix candidates: (a) pass `validate=True` to `b64decode` so invalid
  chars raise a clear error, (b) accept both alphabets by trying
  `urlsafe_b64decode` as a fallback, or (c) document the standard-
  base64 requirement and reject url-safe input with an explicit
  message at parse time. Worth doing the same audit for `SECRET_KEY`.
