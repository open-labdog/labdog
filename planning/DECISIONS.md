# Decisions

Open questions first, then the log of decisions made. Each open question
lists its options, a recommendation and the items it blocks. An item that
depends on an open decision is not scheduled until the decision is logged
below. Questions about Enterprise items (D-05, D-06, D-08 to D-12, D-15,
D-16) are in the Enterprise repository's `planning/DECISIONS.md`.

## Open

### D-01 Contributor terms: which agreement, signed to whom
Blocks: S-011.
CONTRIBUTING.md says "We don't use a CLA".
- A. A CLA (Apache-style individual agreement) checked on every PR by a
  CLA bot, granted to Dennis personally with a clause that lets him assign
  it to a company later.
- B. DCO sign-off only. Lighter, but grants no right to relicense.
- C. Wait until a company exists.

Recommendation: A. The board marks this P0 because one merged outside PR
under today's terms cannot be undone, and B does not solve that.

### D-02 How far the rename goes before the first release
Blocks: C-001, and proposal PR-02.
The UI, logo and links say unbork. The internals do not: the env prefix
is `LABDOG_` (`backend/app/config.py`), the Python distribution is
`labdog-backend` (`backend/pyproject.toml`), data lives in
`/var/lib/labdog` and config in `/etc/labdog` (`Dockerfile`,
`packaging/`), the README heading and the docs site title still say
LabDog. 459 files mention the old name.
- A. Rename everything now, reading the old names as fallbacks for at
  least one major version (old env prefix, old paths, old package names
  replaced on upgrade).
- B. Rename only what users see (README, docs site, CLI output) and keep
  internal names for good.
- C. Release as is and rename later.

Recommendation: A. The first release is the cheapest moment, because
every existing install has to go through an upgrade step anyway. C means
two disruptive upgrades.

### D-03 Version number of the first unbork release
Blocks: C-002.
`VERSION` is 1.1.0. Options: 1.2.0, or 2.0.0.
Recommendation: 2.0.0. The image name changed, roles replaced the
superuser flag, and (with D-02 A) so do paths and variables. Semantic
versioning calls that a major.

### D-04 The Claude Code binary in the image
Blocks: S-009, C-026. Details and the edition question are in the
Enterprise repository.
- A. Leave it out of the image and install it on first use when an admin
  turns that provider on.
- B. Publish two images, one with and one without.
- C. Keep it.

Recommendation: A.

### D-07 Copyright holder line
Blocks: S-012.
LICENSE names no holder; the README says "Dennis Tyresson and
contributors". Options: Dennis personally now and the company later, or
wait for the company. Recommendation: Dennis personally now.

### D-19 Memory restore: snapshot with RAM, or Proxmox Backup Server
Blocks: C-045. Shapes C-044.
Dennis asked for rollback through Proxmox Backup with memory restore as
the main goal. As far as the planning session knows, a Proxmox backup
(vzdump, to PBS or elsewhere) holds disks and configuration but not the
VM's RAM, while a Proxmox *snapshot* can include RAM (`vmstate`). This
was not verified against a Proxmox host in this session.
- A. Add RAM to the existing snapshots (C-044) and treat PBS as a
  separate, disk-only fallback (C-045).
- B. PBS only, accepting that memory is not restored.
- C. Both, with C-044 first.

Recommendation: C, after confirming on a test VM what a PBS restore
brings back. Snapshots with RAM are slower and need space for the memory
image, so C-044 should be a per-action or per-host choice.

## Assumptions made without asking

- A-01. "Harden the webhook" (C-022) is taken from the board text as
  written; the spec will quote it and add nothing.
- A-02. C-015 is planned although its code is in the private pack
  repository, because the board item names those two actions.
- A-03. Board P3 items stay parked, except C-029, C-030 and C-037, which
  were raised to P2. The ranking table says why for each.
- A-04. Stage runs test on local Docker on lin-manager-2, and on hosts
  named "test" when a real host is needed. The live instance on
  lin-manager is semi-production and other hosts must never break.

## Log

| Date | Decision | By |
|---|---|---|
| 2026-11-14 | D-13: propose_action hands off. It creates the ActionRun and ends the turn with a new `waiting_action` session status; the session resumes when the run finishes; `host_lock.py` is unchanged. Approval uses the existing `AIApprovalRequest` cards with frozen arguments. `allowed_action_keys` limits the tool at every level, and under full_auto a listed key runs without asking. The approver needs `ai.approve` and `actions.run` on the target. No AI snapshot. Write-up: https://claude.ai/artifact/AVhU8tSKwe6LeeQmfzE4AG. C-009 becomes class B. | Dennis, as relayed by the D-13 thread |
| 2026-11-14 | D-14: a new `ai.policy.manage` permission, admins only, checked at global scope, with a `PLUGIN_API` minor bump. Replaces the 2026-10-02 "any signed-in user" decision. Unblocks C-019. | Dennis, as relayed by the D-14 thread |
| 2026-11-14 | D-17: keep `planning/` on dev and amend the rule; `plans/` stays branch-scoped scratch. To amend in Phase 6: CONTRIBUTING.md (the `plans/` section and the release checklist line), CLAUDE.md, `.github/PULL_REQUEST_TEMPLATE/release.md`, and `.dockerignore` (add `planning/`). | Dennis, on the card in the D-17 thread |
| 2026-11-14 | D-18: customer-facing Enterprise feature titles appear in the public plan with an Enterprise tag and a no-commitment line; scores, specs, decisions and internal items stay private. | Dennis |
