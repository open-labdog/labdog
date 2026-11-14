# Stage 11: kennel, a VM operations service

Goal: kennel exists as its own AGPL service: it leases VMs, proves backups by restoring them in an isolated network, and gives unbork surge nodes for upgrades. It starts only after Dennis has created the repository.

## Entry criteria

- Stage 10 has passed its exit criteria, or Dennis has said to start this one anyway.
- Class H items (C-028, C-057) are skipped until their decision is logged in `DECISIONS.md`.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | [C-028](../items/C-028-kennel-repository-service-skeleton-and-proxmox-pool.md) | kennel: repository, service skeleton and Proxmox pool token | H | L | S-003 |
| 2 | [C-058](../items/C-058-kennel-per-vm-readiness-api.md) | kennel: per-VM readiness API | B | M | C-028 |
| 3 | [C-054](../items/C-054-kennel-leases-and-the-sweeper.md) | kennel: leases and the sweeper | B | L | C-028 |
| 4 | [C-057](../items/C-057-kennel-isolated-sandbox-network-with-a-jump.md) | kennel: isolated sandbox network with a jump host | H | L | C-028 |
| 5 | [C-045](../items/C-045-kennel-pbs-restore-points-restored-beside-the.md) | kennel: PBS restore points, restored beside the original | B | L | C-028, C-054 |
| 6 | [C-055](../items/C-055-kennel-automatic-backup-restore-tests.md) | kennel: automatic backup restore tests | B | L | C-054, C-057 |
| 7 | [C-059](../items/C-059-unbork-talks-to-kennel-snapshot-provider-and.md) | unbork talks to kennel: snapshot provider and surge nodes for upgrades | B | L | S-003, C-054, C-058 |
| 8 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 11 | | | all of the above |

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 11` is done, `planning/reviews/stage-11.md` exists, and no critical or high finding is open.

Behaviours to verify:

- [ ] kennel starts from its own image with its own database and answers a health check.
- [ ] Every VM kennel creates has a lease with an owner and an expiry, and the sweeper removes what expires or is orphaned.
- [ ] A restore test restores a PBS backup beside the original in the sandbox network, runs its check, records the result and cleans up.
- [ ] A sandboxed VM cannot reach a production address; unbork reaches it through the jump host.
- [ ] The readiness API answers, per VM: snapshot possible, with RAM, restore point available, free space.
- [ ] unbork uses kennel as a snapshot provider behind the S-003 interface, and k8s-upgrade can request and release a surge node.

## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
