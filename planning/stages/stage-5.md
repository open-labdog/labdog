# Stage 5: Governance and the directory

Goal: an organisation can govern unbork from its directory and its change calendar: audit entries leave for a SIEM, runs respect maintenance windows, and servers, groups and rights follow LDAP.

## Entry criteria

- Stage 4 has passed its exit criteria, or Dennis has said to start this one anyway.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | [S-006](../items/S-006-frontend-extension-seam-navigation-entries-pages-host.md) | Frontend extension seam: navigation entries, pages, host and group panels | B | S | none |
| 2 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 5 | | | all of the above |

Enterprise items in this stage, in the Enterprise repository (listed for transparency, with no dates or commitments): E-002 Send the audit log to a SIEM (syslog/CEF and HTTPS webhook); E-003 Maintenance and change-freeze windows: model, API and enforcement; E-004 Maintenance windows: calendar UI on Settings, host and group pages; E-023 LDAP sync: read servers and groups from the directory, with a preview; E-030 LDAP sync: apply on a schedule, flag servers that disappear; E-031 LDAP sync: settings and review UI; E-024 Refresh LDAP group memberships between sign-ins and bind them to synced groups; E-029 (internal Enterprise item).

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 5` is done, `planning/reviews/stage-5.md` exists, and no critical or high finding is open.

Behaviours to verify:

- [ ] An extension can add a navigation entry, a page and a panel on host and group pages through `frontend/lib/extensions.ts`; with empty lists every page renders as before.

## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
