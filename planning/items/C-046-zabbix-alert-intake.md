# C-046: Zabbix alert intake

| | |
|---|---|
| Edition | community |
| Source | decision D-05; board item "Zabbix alert intake" |
| Tier / score | P2 / 4.0 |
| Stage | 7 |
| Effort | M |
| Autonomy | B |
| Depends on | S-005 |

## Goal
A Zabbix server can send its problems to unbork through a webhook media type. Each problem becomes an alert event, deduplicated and matched to a host like a Grafana alert, and starts an action or an investigation under the same labels and severity policy. A recovery marks the event resolved.

## Scope
In scope:
- A `zabbix_webhook` source on the S-005 registry: payload parser, severity mapping, host hints.
- A Zabbix media type definition (importable YAML with the webhook script) shipped under `docs/examples/zabbix/`.
- Docs, and a Zabbix test stack on local Docker on lin-manager-2.

Out of scope:
- Calling the Zabbix API (acknowledging problems, maintenance periods). That is the Enterprise feature "Zabbix write-back".
- Polling Zabbix for missed problems.
- Importing Zabbix hosts into unbork.

## Context
- After S-005, a source is an `AlertSource` registered in `backend/app/alerts/sources.py`, served at `POST /api/webhooks/alerts/{source_slug}`, and everything after parsing is shared (`ingest`, `record`, `resolve_host`, `notify_alert_fired`, `handle_alert`).
- `NormalizedAlert` needs `alertname`, `starts_at` and a `fingerprint`; rows are unique on `(fingerprint, starts_at)` (`uq_alert_events_fingerprint_starts`), and a second notification with the same pair updates `status` and `ends_at` (`backend/app/ai/alerts.py:248`).
- `resolve_host` (`backend/app/ai/alerts.py:205`) reads the labels `labdog_host_id`, `labdog_hostname`, then `nodename`, `hostname`, `host`, `node`, `instance`, and matches `Host.hostname` or `Host.ip_address`.
- `meets_severity` understands only `info`, `warning`, `critical` (`SEVERITY_ORDER`); anything else never meets a threshold.
- The labels that drive behaviour are `labdog_action`, `labdog_ai` and the automate label (`backend/app/alerts/labels.py:59`); C-048 to C-051 rename these with fallbacks, so read the constants, do not hard-code the strings.
- Zabbix sends notifications through media types. A webhook media type runs a JavaScript snippet with parameters filled from macros (`{EVENT.ID}`, `{EVENT.NAME}`, `{EVENT.NSEVERITY}`, `{EVENT.VALUE}`, `{EVENT.DATE}`, `{EVENT.TIME}`, `{EVENT.RECOVERY.DATE}`, `{EVENT.RECOVERY.TIME}`, `{HOST.HOST}`, `{HOST.IP}`, `{TRIGGER.ID}`, `{EVENT.TAGSJSON}`, `{EVENT.OPDATA}`, `{TRIGGER.URL}`) and can set request headers, so it can send the bearer token unbork already expects.
- A recovery notification carries the original problem's `{EVENT.ID}` and `{EVENT.VALUE}` 0, so problem and recovery share an identity.

## Dependencies
- S-005 merged.
- A Zabbix server to test against: the official `zabbix/zabbix-server-pgsql`, `zabbix/zabbix-web-nginx-pgsql` and `postgres` images on local Docker on lin-manager-2 (D-05). No secret from Dennis is needed; the stack's credentials are test fixtures.
- Verify the macro names and the media type YAML format against the documentation of the Zabbix version started (7.0 LTS assumed) before writing the script.

## Implementation steps
1. `backend/app/alerts/zabbix.py`:
   - `from_zabbix_webhook(payload: dict) -> list[NormalizedAlert]`. Expected JSON, produced by our own media type script: `event_id`, `event_name`, `nseverity` (0 to 5), `value` ("1" problem, "0" recovery), `started` (ISO 8601 UTC, built by the script from the date and time macros and the server's time zone parameter), `resolved` (optional), `host`, `host_ip`, `trigger_id`, `tags` (list of `{tag, value}`), `opdata`, `url`, `zabbix_url`.
   - Mapping: `alertname` = `event_name` (trimmed to 255); `fingerprint` = `"zabbix:" + sha256(zabbix_url + ":" + event_id)` so two Zabbix servers cannot collide; `starts_at` = `started`; `status` = resolved when `value == "0"`; `ends_at` = `resolved`.
   - Severity: 0 and 1 to `info`, 2 and 3 to `warning`, 4 and 5 to `critical`. Keep the Zabbix name in a label `zabbix_severity`.
   - Labels: `alertname`, `severity`, `hostname` = `host`, `instance` = `host_ip`, `zabbix_event_id`, `zabbix_trigger_id`, plus every event tag as a label (tag name as key, last value wins). This lets an operator set `labdog_action` or `labdog_ai` as a Zabbix tag on the trigger. Annotations: `summary` = `event_name`, `description` = `opdata`, `url`.
   - Return an empty list for a payload missing `event_id`, `event_name` or `started`, and for update notifications (acknowledgements, comments), which the script marks with `update: "1"`.
2. Register `AlertSource(key="zabbix_webhook", label="Zabbix", webhook_slug="zabbix", parse=from_zabbix_webhook, authenticate=<shared token check from S-005>, token_guarded=True)` in `app/alerts/__init__.py`.
3. `docs/examples/zabbix/unbork-media-type.yaml`: an importable media type "unbork" with parameters `unbork_url`, `unbork_token`, `server_timezone`, and the macro parameters above; its script builds the JSON, sends `Authorization: Bearer <token>`, and throws on a non-2xx answer so Zabbix shows the failure. Add `docs/examples/zabbix/README.md` with the import steps, the user and action setup (problem, recovery and update operations), and the note that the token must be at least 32 characters for unattended fixes.
4. `test-services/zabbix/` in the repository root is not created here; put the compose file under `dev/zabbix/compose.yaml` with a `README.md` (ports, default credentials, how to import the media type, how to raise a test problem with a trapper item and `zabbix_sender`). It runs on lin-manager-2 only.
5. Frontend: nothing beyond what S-005 added; the alerts page lists the Zabbix source and its webhook path from `GET /api/alerts/intake`. Add a Zabbix badge if the page shows the source per row (check `frontend/app/(dashboard)/alerts/client-page.tsx`).
6. Tests in `backend/tests/ai/test_alerts_zabbix.py`, with payload fixtures under `backend/tests/fixtures/zabbix/` captured from the real server in step 4:
   - a problem payload creates one firing event with the mapped severity and the host matched by `host` and by `host_ip`;
   - the same problem sent twice gives one row with `dedup_count` 2;
   - the recovery payload flips the row to resolved and sets `ends_at`;
   - an update notification records nothing;
   - a `critical` problem with investigation enabled is queued for handling and a `warning` one below the threshold is recorded and skipped, through the unchanged policy code (assert on the same outcome fields the Grafana tests use);
   - a tag `labdog_action=<key>` reaches `dispatch_for` as a label;
   - a wrong token gives 401.

## Acceptance criteria
- `POST /api/webhooks/alerts/zabbix` with a valid token and a captured problem payload returns `{"status": "accepted", "recorded": 1, ...}` and creates an `alert_events` row with `source = "zabbix_webhook"`.
- With auto-investigation on and a threshold of `critical`, a Zabbix "High" or "Disaster" problem on a known host starts an investigation, and an "Average" one does not.
- A recovery resolves the same row, and no second row appears.
- No file outside `app/alerts/zabbix.py`, the registration line, tests and docs changes policy code: `git diff --stat` shows no edit to `record`, `resolve_host`, `meets_severity`, `_decide_and_run` or `dispatch_for`.
- The media type imports into the Zabbix on lin-manager-2 and its "Test" button delivers an alert that appears on the unbork alerts page.

## Test plan
- `cd backend && source .venv/bin/activate && pytest tests/ai/test_alerts_zabbix.py tests/ai/test_alert_sources.py tests/ai/test_alerts.py -q`
- Full suite before the stage review: `pytest tests/ -q`; `ruff check . && ruff format --check .`; `bandit -q -r app -b bandit-baseline.json`.
- `cd frontend && npx tsc --noEmit && npm run lint` if the alerts page changed.
- End to end on lin-manager-2: start `dev/zabbix/compose.yaml` and the unbork stack on local Docker, add a host named "test" to both, raise a problem with `zabbix_sender`, watch the alert arrive and the investigation start, then clear the problem and watch it resolve. Use read-only investigation; do not enable unattended fixes on any host that is not named "test". Do not point the Zabbix stack at the live instance on lin-manager.

## Docs and changelog
- `docs/ui/alerts.md`: a "Zabbix" subsection under "Two ways in" (setup, severity mapping table, tags as labels, what a recovery does, troubleshooting). `docs/examples/zabbix/README.md` as above.
- `CHANGELOG.md` `[Unreleased]` / Added: "Zabbix alert intake: a webhook media type sends Zabbix problems to unbork, where they are deduplicated and handled like Grafana alerts."

## Risks and rollback
- Zabbix date and time macros are in the server's local time and format. A wrong `starts_at` breaks deduplication between problem and recovery. The script converts with an explicit time zone parameter, and the fingerprint does not depend on the time; verify with captured payloads.
- Event tags become labels, so a Zabbix user who can tag a trigger can ask for an action. That is the same trust as a Grafana label, and the allow-list in `app/alerts/labels.py` still decides; say so in the docs.
- Rollback: revert the PR, or unregister the source. Existing rows stay as history.

## Assumptions
- [D-05](../DECISIONS.md): intake is Community; the write-back is the Enterprise half.
- Assumption (not yet confirmed): Zabbix 7.0 LTS is the version tested; 6.0 LTS is expected to work because the media type uses long-standing macros, but is not tested. Question for Dennis: which Zabbix versions must be supported.
- Assumption (not yet confirmed): the severity mapping above. "Average" maps to `warning`.
- Assumption (not yet confirmed): Zabbix uses the same shared token as Grafana (see S-005).

## Definition of done
- [ ] Parser, registration, media type YAML, compose file and docs merged to `dev`.
- [ ] `tests/ai/test_alerts_zabbix.py` passes with captured fixtures; full suite green.
- [ ] End-to-end check on lin-manager-2 done and noted in `planning/PROGRESS.md`.
- [ ] `CHANGELOG.md` updated; `planning/roadmap.yaml` status set to done.
