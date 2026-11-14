# C-001: Verify and document the upgrade from LabDog 1.1.0 to unbork

| | |
|---|---|
| Edition | community |
| Source | roadmap, split from the board item "Release dev to main"; decisions D-02 and D-03 |
| Tier / score | P0 / 9.5 |
| Stage | 3 |
| Effort | M |
| Autonomy | B |
| Depends on | C-048, C-049, C-050, C-052 |

## Goal

An operator running LabDog 1.1.0 can follow "Upgrading to 2.0.0" in `docs/upgrade.md`
step by step and end up on unbork 2.0.0 with config, data, hosts and schedules intact.
The section has been followed literally on test installs of every install type before the
release PR opens.

## Scope

In scope:
- A written "Upgrading to 2.0.0" section, and the rewording of the "Versioning" section
  that D-03 asks for.
- Fixing `docs/production-deploy.md`, which pins `unbork/unbork:1.0.0`, a tag that does
  not exist.
- Manual upgrades of four test installs: Docker, `.deb`, `.rpm`, tarball.
- One managed host named "test" taken through the first sync after the upgrade, on
  iptables and on nftables.
- Fixing or filing whatever the verification finds.

Out of scope:
- The rename itself (C-048 to C-051) and the automated gate (C-052).
- Bumping `VERSION`, the changelog section and the release PR (C-002).
- Upgrading the live instance on lin-manager. That happens after the release, by Dennis
  or with his go-ahead.

## Context

- `docs/upgrade.md`: "Compatibility" (line 29), "Versioning" (line 42, written for 1.x:
  "every earlier release upgrades to every 1.x release"), "Upgrading to 1.0.0" (line 75),
  the general procedure ("Pre-upgrade" 325, "Docker: check the environment variables"
  358, "Upgrading" 412, "Verifying the upgrade" 480, "Rolling back" 513).
- `/mnt/project-files/release-version/first-unbork-version.md` records what 1.x promised
  and what changed since 1.1.0. Its findings to carry into the section: config keys only
  added (`max_session_seconds`, `drift_concurrency`, `actions_concurrency`, the role
  setting); migrations 0056 to 0060 are additive; 0058 maps superusers to `admin` and
  everyone else to `operator`; 0057 repoints the seeded pack URL only when it is the old
  default; sessions are capped at 12 hours; `/docs` and `/openapi.json` are off by
  default; the image is published as `unbork/unbork`, with copies to the old repository
  while `LEGACY_DOCKER_HUB_REPO` is set; two "for the life of 1.x" deprecations exist
  (the `/api/ai/alerts` aliases in `backend/app/api/ai.py`, and `is_superuser` on the
  admin users API).
- D-02 ([DECISIONS.md](../DECISIONS.md)): a full rename with the old names as fallbacks,
  including on-host markers, alert labels, metrics and pack variables; host checks
  recognise both names and the first sync removes the old chains and files. The specs of
  C-048, C-049 and C-050 in this directory say exactly what moved and what still works.
  Read all three before writing; this section is their operator-facing summary.
- D-04: Claude Code is installed on first use and the subscription token field is gone
  (S-009, S-014). An existing provider that relied on either needs a note.
- `docs/production-deploy.md` line 84 has `image: unbork/unbork:1.0.0    # pin a real
  version`; the compose example around it uses `LABDOG_*` variables and `labdog` volume
  names, which C-048, C-049 and C-051 may already have changed.
- 1.1.0 is available as the GitHub release `v1.1.0` (packages) and as
  `openlabdog/labdog:1.1.0` on Docker Hub.

## Dependencies

- C-048, C-049 and C-050 merged to `dev`; C-052 green, so the package path is already
  proven by machine.
- S-009 and S-014 merged, or their effect on existing providers known.
- Local Docker on lin-manager-2, and two managed hosts or VMs named "test": one using
  iptables, one using nftables. Dennis supplies them if none exist.
- A candidate build of `dev`: the `:dev-<sha>` image from CI and packages from
  `packaging/build.sh`.

## Implementation steps

1. Draft the section from the specs, before testing. Order it by what the operator must
   do, then what changed without action, as "Upgrading to 1.0.0" does. It must cover:
   - Before you start: back up the database and the encryption key (link to
     "Pre-upgrade"), and that the upgrade is one-way once the first sync has renamed
     on-host markers, except through the documented rollback.
   - Docker: the new image name and tag, that `LABDOG_*` variables and old volume mount
     paths keep working and log one deprecation warning, and the exact compose edits for
     an operator who wants the new names now.
   - Packages: the new package name, the command to install it over `labdog`, what is
     moved (config, data, logs, the service user keeping its uid) and that the old paths
     and `labdog.service` name keep resolving. Tarball: the `install.sh` path.
   - Signing in again once, because the session cookies were renamed.
   - Hosts: the first sync after the upgrade replaces the old firewall chains and
     managed-file markers with the new names; what an operator sees in the plan; that
     drift checks before that sync do not report the old names as drift.
   - Alerts and metrics: old and new alert labels are both read; which metric names
     changed and how long the old ones are emitted, as C-050 decided; Grafana dashboards
     and alert rules to update.
   - Packs: the seeded pack's new name; old pack variables still accepted.
   - Roles: every non-admin account becomes `operator`; where to change that.
   - The assistant: Claude Code is fetched on first use and needs outbound access to do
     so; the subscription token field is gone.
   - The two 1.x deprecations: state whether 2.0.0 still serves them. Read the code at
     the time; do not remove them in this item.
   - Rollback from 2.0.0 to 1.1.0 for each install type, including the alembic downgrade
     target and what on a host does not roll back.
2. Reword "Versioning": the promise now covers 2.x; give 2.x examples; say that the
   `LABDOG_*` names, old paths, old labels and old metric names are deprecated fallbacks
   that a later major release may remove; change the upgrade-path line to "every release
   from 1.0.0 upgrades to every 2.x release by the procedure on this page".
3. Fix `docs/production-deploy.md`: pin `unbork/unbork:2.0.0` and make the example match
   the names C-048 and C-049 introduced. Check every other tag in `docs/` with
   `grep -rn "unbork/unbork:\|openlabdog/labdog:" docs/ README.md`.
4. Verify on lin-manager-2, following the draft literally and correcting it wherever a
   step is wrong or missing. For each of Docker, `.deb` (Ubuntu 24.04 VM or container
   with systemd), `.rpm` (Rocky 9) and tarball:
   1. Install 1.1.0 and configure it as its own docs say.
   2. Create data: an admin and a second user, two groups, the two "test" hosts with
      firewall rules, a package rule, a cron job, an SSH key, a schedule, one pack action
      run, an AI provider if a key is available, a Grafana alert through the webhook.
   3. Sync both hosts so the old markers are on them.
   4. Upgrade by the draft. Run "Verifying the upgrade". Then check: sign-in, every piece
      of data above, the deprecation warning text, a drift check (no false drift), a
      sync (old chains and markers replaced, connectivity kept), the schedule firing, the
      webhook with the old labels, `/metrics`.
   5. Roll back by the draft and confirm 1.1.0 starts with its data.
5. Record the runs in `planning/reviews/stage-3-upgrade.md`: install type, versions,
   commit, what was checked, what failed, and the fix or the BUGS.md id.
6. Fix what the runs find in the item that owns it (a rename bug goes back to C-048 to
   C-050 as a fix commit that names it). A finding that cannot be fixed before the
   release becomes a `BUGS.md` entry and a "Known issues" line in the section, and is
   named to Dennis in the review request.
7. Ask Dennis to read the section before C-002 opens the release PR (class B).

## Acceptance criteria

- `docs/upgrade.md` has an "Upgrading to 2.0.0" section above "Upgrading to 1.0.0", and
  `cd website && npm run build` succeeds with no broken-link error.
- `planning/reviews/stage-3-upgrade.md` shows one completed upgrade and one completed
  rollback for each of the four install types, each done from the written section.
- After the upgrade on each install: `GET /api/version` reports the candidate; all seeded
  data is present; a sync of each "test" host succeeds and leaves no `LABDOG`-named chain
  or marker behind while SSH to the host keeps working.
- A drift check run between the upgrade and the first sync reports no drift caused only
  by the rename.
- `grep -rn "unbork/unbork:1.0.0" docs/` prints nothing.
- The "Versioning" section no longer says "1.x" where it means the current line.
- No critical or high finding from the runs is open, or Dennis has accepted it in writing.

## Test plan

- The verification in step 4 is the test. It runs on local Docker and VMs on
  lin-manager-2 and against hosts named "test" only. Those hosts may break; no other host
  may be added to these installs.
- Docs build: `cd website && npm ci && npm run build`.
- If a fix touches code: the owning item's tests, then
  `cd backend && source .venv/bin/activate && pytest tests/ -q` once.
- The live instance on lin-manager is not upgraded in this item.

## Docs and changelog

- `docs/upgrade.md` and `docs/production-deploy.md` as above; `docs/README.md` if it
  links to an upgrade anchor that moved.
- `CHANGELOG.md`, `[Unreleased]`: no new entry of its own. Add "See Upgrading to 2.0.0"
  with the link to the top of the section, where C-002 will turn it into the release
  heading.

## Risks and rollback

- The first sync after the upgrade rewrites firewall chains on every managed host. A
  mistake there cuts SSH. This is why both backends are tested on "test" hosts first, and
  why the section tells operators to sync one host and check it before the rest.
- A systemd-only failure (sandboxing paths in the new unit) is invisible to C-052; the
  `.deb` and `.rpm` runs here must use real systemd.
- Writing the section from the specs and not from what the code does would repeat any
  spec error. Step 4 corrects the text against real runs.
- Rollback of this item is a docs revert. Rollback of an upgraded install is part of
  what the item documents and tests.

## Assumptions

- [D-02](../DECISIONS.md) and [D-03](../DECISIONS.md) are settled; this item adds no
  rename decision of its own.
- Assumption (not yet confirmed): 2.0.0 keeps the two 1.x deprecations working and says
  they will go in a later release. Question for Dennis: remove them in 2.0.0 instead?
- Assumption (not yet confirmed): two "test" hosts, one per firewall backend, exist or
  can be created on the Proxmox behind lin-manager-2.
- Assumption (not yet confirmed): a rollback after the first sync is supported by
  syncing again from 1.1.0, which re-creates the old chains. If C-050's spec says
  otherwise, the section follows C-050.

## Definition of done

- [ ] "Upgrading to 2.0.0" and the reworded "Versioning" section are merged to `dev`.
- [ ] `docs/production-deploy.md` pins a real tag.
- [ ] Four upgrade and rollback runs recorded in `planning/reviews/stage-3-upgrade.md`.
- [ ] Findings fixed, or filed in `BUGS.md` and listed as known issues.
- [ ] Dennis has read the section.
- [ ] `planning/roadmap.yaml`: C-001 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
