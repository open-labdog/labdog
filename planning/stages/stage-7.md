# Stage 7: Integrations

Goal: unbork fits into the tools an organisation already runs: scripts authenticate with tokens, secrets can live outside the database, and alerts arrive from more than Grafana.

## Entry criteria

- Stage 6 has passed its exit criteria, or Dennis has said to start this one anyway.

## Items, in execution order

| # | ID | Item | Class | Effort | Depends on |
|---|---|---|---|---|---|
| 1 | [C-030](../items/C-030-rest-api-tokens.md) | REST API tokens | B | M | none |
| 2 | [S-004](../items/S-004-secret-provider-seam-for-stored-credentials.md) | Secret provider seam for stored credentials | B | M | none |
| 3 | [C-060](../items/C-060-bind-stored-secrets-to-their-row-sec.md) | Bind stored secrets to their row (SEC-50) | B | M | S-004 |
| 4 | [S-005](../items/S-005-alert-source-seam-with-grafana-as-the.md) | Alert source seam, with Grafana as the first source | B | M | C-022 |
| 5 | [C-046](../items/C-046-zabbix-alert-intake.md) | Zabbix alert intake | B | M | S-005 |
| 6 | | Review and hardening: follow `.claude/skills/review-stage/SKILL.md` for stage 7 | | | all of the above |

Enterprise items in this stage, in the Enterprise repository (listed for transparency, with no dates or commitments): E-005 SAML sign-in; E-006 Secrets from HashiCorp Vault; E-007 Zabbix write-back: acknowledge problems with the run result, sync maintenance periods.

## Exit criteria

- [ ] Backend: `pytest tests/ -q` green; `ruff check .` and `ruff format --check .` clean; `bandit -q -r app -b bandit-baseline.json` shows nothing new.
- [ ] Frontend: `npx tsc --noEmit` clean; `npm run lint` with no errors and no more warnings than the previous stage; unit tests green once C-061 has landed.
- [ ] `docs/` and `CHANGELOG.md` (`[Unreleased]`) updated for every user-visible change; `BUGS.md` entries fixed here are deleted.
- [ ] `planning/roadmap.yaml` and `planning/PROGRESS.md` are up to date.
- [ ] The behaviours listed below are verified.
- [ ] `/review-stage 7` is done, `planning/reviews/stage-7.md` exists, and no critical or high finding is open.

Behaviours to verify:

- [ ] A request with a valid API token is authorised with the token's permissions; a revoked or expired token is refused; tokens are stored hashed.
- [ ] A stored credential is resolved through the secret provider seam, and the default provider behaves exactly as today.
- [ ] A ciphertext copied to another row no longer decrypts; existing rows are migrated; SEC-50 is deleted from `BUGS.md`.
- [ ] A second alert source can be registered without touching dedupe or severity policy.
- [ ] A Zabbix webhook creates an alert event and, under the severity policy, an investigation.

## Where to test

- Automated tests run in the session's own environment.
- Anything that needs a running stack goes on local Docker on lin-manager-2.
- Anything that needs a real managed host uses hosts named "test", which are free to break. No other host may break.
- The live unbork on lin-manager (`ssh lin-manager`) is semi-production: use it only for a final check of something already proven elsewhere, and never for a destructive run against a host that is not named "test".
