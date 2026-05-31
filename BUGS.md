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

ID counter as of last housekeeping pass: `BUG-52`, `SEC-19`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Security findings — High

Filed 2026-05-21 from a `security-auditor` whitebox source-level
review (commit `1108d67` as the audit baseline). Each entry was
spot-checked against current HEAD before filing.

---

### Pre-existing (filed prior to the security review)

- [ ] **BUG-52** Proxmox CA certificate trust — allow uploading a PEM CA certificate per Proxmox node so labdog can verify the node's TLS certificate without disabling SSL verification entirely.

  The `verify_ssl=False` workaround (BUG-45) bypasses all certificate validation. The proper fix is to accept a PEM CA bundle per node, store it encrypted in the DB, write it to a temp file at request time, and pass it as `verify=ca_cert_path` to `httpx.AsyncClient`. This lets nodes with self-signed or private-CA certificates be verified correctly without weakening security. Scope: new `ca_cert_pem` column on `proxmox_nodes`, schema and API changes, frontend PEM upload field, and `ProxmoxClient` changes to write the cert to a temp path.
