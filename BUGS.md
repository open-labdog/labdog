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

- [ ] **BUG-46** `LABDOG_PLAYBOOKS_REF` + `Dockerfile:39` / `packaging/Makefile:26` / `.github/workflows/ci.yml:157,245` / `dev/dev.sh:111` — bundled-pack build fails because pinned SHA doesn't exist on the default upstream

  Symptom: `docker build .` (and packaging/build.sh, and the github CI jobs that fetch the bundled pack) fails when the Stage 2b clone tries to `git checkout e6e73728…` against the default URL `https://github.com/open-labdog/labdog-playbooks.git`. The pinned SHA only exists on the maintainer's local gitlab mirror (`gitlab.lan.tyresson.se/dennis/labdog-playbooks.git`); the github public mirror is stale (old flat-layout playbooks, no alloy-install). Root cause: when the build-time-fetch migration landed (commit `8fb167d`), the github mirror had not yet been synced from gitlab. The labdog repo committed the gitlab SHA in `LABDOG_PLAYBOOKS_REF` because that's what the in-repo bundled mirror reflected; defaulting the URL to gitlab would couple public labdog to a private hostname. Severity: **High** — every build path is broken without an env override (`LABDOG_PLAYBOOKS_REPO` or `LABDOG_PLAYBOOKS_LOCAL`). Resolution: push gitlab `labdog-playbooks/main` to github `open-labdog/labdog-playbooks`, then bump `LABDOG_PLAYBOOKS_REF` to whatever SHA github lands on. See `TODO.md` "Mirror gitlab labdog-playbooks to github" for the full procedure.

- [x] **BUG-45** `backend/app/proxmox/client.py:88` — Proxmox API requests fail with SSL certificate verification error

  Symptom: `httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1010)` raised from `ProxmoxClient._request` when the `discover_host_vm_mapping` endpoint calls `client.list_nodes()` → `GET /api2/json/nodes`. Seen live in the `labdog` container on `lin-manager` (2026-05-15). Root cause: httpx performs strict SSL verification by default; Proxmox nodes typically use self-signed or privately-issued TLS certificates whose CA is not in the system trust store. Severity: **High** — Proxmox VM discovery is completely broken for any host using a private CA or self-signed cert. **Resolution:** `verify_ssl` toggle is fully wired end-to-end (model → API → frontend checkbox "Verify SSL certificate" in Settings → Proxmox). Uncheck the field when editing the Proxmox node to disable verification. Superseded by BUG-52 for the proper CA cert solution.

- [ ] **BUG-52** Proxmox CA certificate trust — allow uploading a PEM CA certificate per Proxmox node so labdog can verify the node's TLS certificate without disabling SSL verification entirely.

  The `verify_ssl=False` workaround (BUG-45) bypasses all certificate validation. The proper fix is to accept a PEM CA bundle per node, store it encrypted in the DB, write it to a temp file at request time, and pass it as `verify=ca_cert_path` to `httpx.AsyncClient`. This lets nodes with self-signed or private-CA certificates be verified correctly without weakening security. Scope: new `ca_cert_pem` column on `proxmox_nodes`, schema and API changes, frontend PEM upload field, and `ProxmoxClient` changes to write the cert to a temp path.
