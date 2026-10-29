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

ID counter as of last housekeeping pass: `BUG-115`, `SEC-38`,
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

Filed 2026-10-29 while tuning the valheim game-server VM. Confirmed
against the host's syslog and the renderer at `3c2fe450`.

### Correctness — Medium

- [ ] **BUG-115** `backend/app/hosts_mgmt/merge.py:47-55`,
      `backend/app/hosts_mgmt/merge.py:126` — a synced `/etc/hosts` has no
      line for the host's own name, so the host cannot resolve itself

      Symptom: the hosts sync on valheim (2026-10-25 04:08) replaced the
      Debian `/etc/hosts` with the LabDog header and the two localhost
      lines, dropping `127.0.1.1 valheim`. The host's resolver is 1.1.1.1,
      which has no answer for the name either. From then on every `sudo`
      printed "unable to resolve host valheim", waited on a DNS lookup,
      and mailed root a "problem parsing sudoers" security notice — over
      90 in five days, LabDog's own runs among them, since it connects as
      a sudo user. `hostname -f` and anything else that resolves the local
      name fail the same way.

      Root cause: `SYSTEM_ENTRIES` seeds only `127.0.0.1 localhost` and
      `::1 localhost …`, nothing adds the host's own name, and
      `render_hosts_file` writes the merged entries as the whole file. The
      Debian installer and cloud-init's `manage_etc_hosts` both write
      `127.0.1.1 <fqdn> <short>`; that is the line the sync removes. A host
      whose own name resolves in DNS does not show the bug, which is why it
      surfaced on a host with a public resolver.

      Workaround: a host-level hosts entry `127.0.1.1 <hostname>`, or
      `libnss-myhostname` on the host (applied on valheim 2026-10-29).

      Severity Medium: every synced host whose own name is not in DNS
      loses self-resolution; no data at risk.
