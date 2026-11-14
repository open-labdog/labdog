# Proposals

Work that is on neither roadmap. Nothing here is scheduled until Dennis
accepts it; an accepted proposal becomes a normal item marked
`source: proposed`. Status is one of open, accepted, rejected, deferred.
Proposals about the Enterprise repository (PR-08 to PR-10) are in its
`planning/PROPOSALS.md`. Ranked across both files: PR-01, PR-02, PR-03,
PR-08, PR-09, PR-07, PR-06, PR-04, PR-10, PR-05.

| ID | Title | Raised by | When | From | Status |
|---|---|---|---|---|---|
| PR-01 | Audit rows for firewall rule changes | Claude | 2026-11-14 | planning | accepted 2026-11-14, now C-047 |
| PR-02 | Finish the rename under the hood, with fallbacks | Claude | 2026-11-14 | planning | accepted (D-02), now C-048 to C-051 |
| PR-03 | Test the upgrade from 1.1.0 in packaging-smoke | Claude | 2026-11-14 | planning | accepted 2026-11-14, now C-052 |
| PR-04 | Bind stored secrets to their row | Claude | 2026-11-14 | planning | accepted 2026-11-14, now C-060 |
| PR-05 | Bring CLAUDE.md up to date | Claude | 2026-11-14 | planning | accepted 2026-11-14, now C-051 |
| PR-06 | A unit-test harness for the frontend | Claude | 2026-11-14 | planning | accepted 2026-11-14, now C-061 |
| PR-07 | Measure coverage in CI and record a load-test baseline | Claude | 2026-11-14 | planning | accepted 2026-11-14, now C-062, C-063 |

## PR-01 Audit rows for firewall rule changes
- **Why:** BUG-147 in `BUGS.md` (Medium): `backend/app/api/rules.py` has
  no `log_action` call, so creating, editing, deleting or reordering a
  firewall rule leaves no audit row. Every other module audits through
  `RuleAudit` in `backend/app/api/_scoped_rules.py`. C-008 closes the
  same kind of hole for action runs, and S-001 forwards the audit stream,
  so a missing module there is a missing module downstream.
- **Edition and effort:** Community, S.
- **Where:** stage 0, next to C-008.

## PR-02 Finish the rename under the hood, with fallbacks
- **Why:** see D-02. `LABDOG_` prefix (`backend/app/config.py:276`),
  `labdog-backend` distribution (`backend/pyproject.toml`),
  `/var/lib/labdog` (`Dockerfile:73`), `title: 'LabDog'`
  (`website/docusaurus.config.ts:11`), README heading. The board's
  release item asks to check "the upgrade path for an existing LabDog
  install (data, config path, service names)" but lists no rename work.
- **Edition and effort:** Community, L (would be split: config and env,
  paths and packaging, docs and README).
- **Where:** stage 1, before C-001. Only if D-02 is answered A or B.

## PR-03 Test the upgrade from 1.1.0 in packaging-smoke
- **Why:** `packaging/tests/run-smoke.sh` install-tests a clean machine
  only; nothing installs the released 1.1.0 package and upgrades it. The
  first release under a new name is where an upgrade is most likely to
  break, and C-001 would otherwise verify it by hand once.
- **Edition and effort:** Community, M.
- **Where:** stage 1, as part of C-001.

## PR-04 Bind stored secrets to their row
- **Why:** SEC-50 in `BUGS.md` (Low): `context_id` is never passed in
  `backend/app/crypto/encryption.py:21,35`, so a ciphertext copied to
  another row decrypts. S-004 reworks the same call sites; doing both in
  one pass avoids touching 52 files twice.
- **Edition and effort:** Community, M (needs a re-encrypting migration).
- **Where:** stage 5, with S-004.

## PR-05 Bring CLAUDE.md up to date
- **Why:** it is headed "LabDog Development Guide", says "Backend (326
  tests)" where the suite has more than 4,700, and autonomous sessions read
  it first.
- **Edition and effort:** Community, S.
- **Where:** stage 0, or with PR-02.

## PR-06 A unit-test harness for the frontend
- **Why:** `frontend/package.json` has no unit-test script; the only
  frontend tests are Playwright specs that need the full stack. The
  board's refactor item (C-043) notes that each refactor needed a
  throwaway harness for that reason, and stage reviews cannot reproduce a
  frontend bug with a failing test without one.
- **Edition and effort:** Community, M.
- **Where:** stage 0.

## PR-07 Measure coverage in CI and record a load-test baseline
- **Why:** `pytest-cov` is a dev dependency but `backend-test` in
  `.github/workflows/ci.yml` runs plain `pytest`, so coverage is unknown
  between sessions. `dev/loadtest/` exists but its last numbers are in PR
  #270 and it is not run on any schedule. Stage reviews are asked to
  compare performance and coverage with the previous stage and have
  nothing recorded to compare with.
- **Edition and effort:** Community, S for coverage, M for a recorded
  load-test run on lin-manager-2.
- **Where:** stage 0.

## Other work Dennis asked to add

Added 2026-11-14. These are normal items with `source: user`; they get
scores, a stage and specs in Phases 4 and 5.

- **C-044 Keep the VM's memory in the rollback snapshot.** Dennis's goal:
  a rollback restores the running machine, memory included.
  `ProxmoxClient.create_snapshot` (`backend/app/proxmox/client.py:229`)
  sends only `snapname` and `description`, so today's snapshots hold
  disks only and a rollback leaves the VM as if it had lost power.
  Proxmox takes a `vmstate` flag on that same call. Community, M, class B.
  Fits stage 2, after S-003.
- **C-045 PBS restore points through kennel.** Parked with C-028 by decision D-19. Original wording: take a backup
  before a destructive run and restore from it on failure, for hosts or
  storage where a snapshot is not possible, and keep restore points
  beyond the run. Not specified until kennel is decided.
- **Coupling with kennel (Dennis, 2026-11-14).** C-044, C-045 and S-003
  are to be designed together with the possible intermediate VM service
  called kennel (C-028, the board's "Companion VM layer"), which would
  own Proxmox communication and VM state for automatic backup restore
  tests and Kubernetes surge nodes during upgrades. Kennel is future
  work; how far it can be taken is being explored separately. Until that
  comes back: S-003's interface must be one kennel could sit behind, and
  C-045 is not specified, because restoring from backups may belong in
  kennel instead of unbork.
- **Future kennel use cases (Dennis, 2026-11-14: note them, decide
  later).** Parked ideas, not items: Kubernetes autoscaling on Proxmox (a
  Cluster Autoscaler external gRPC provider), CI runners, preview
  environments, multi-VM disaster-recovery drills, forensic copies,
  Proxmox node maintenance, and pack tests on real VMs. Listed under
  "Future use cases" in https://claude.ai/artifact/MJo5wKpzFdKspSeMNDrL3S
- A third item from the same message is Enterprise and is recorded in
  that repository.
