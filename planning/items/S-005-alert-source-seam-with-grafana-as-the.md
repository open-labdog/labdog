# S-005: Alert source seam, with Grafana as the first source

| | |
|---|---|
| Edition | shared |
| Source | roadmap, board item "Zabbix alert intake" (the groundwork), decision D-05 |
| Tier / score | P1 / 5.0 |
| Stage | 7 |
| Effort | M |
| Autonomy | B |
| Depends on | C-022 |

## Goal
Alert intake has one registry of sources. Each source only turns its sender's payload into normalised alerts and says how its requests are authenticated; recording, deduplication, host matching, notifications and the severity policy stay in one place. Grafana is the first registered source and behaves exactly as today.

## Scope
In scope:
- An `AlertSource` interface and registry in `backend/app/alerts/sources.py`.
- One generic intake function used by the webhook route, with Grafana moved onto it.
- Replacing the `source == "grafana_webhook"` string checks with a property of the source.
- A `PLUGIN_API` minor bump so an extension can register a source.

Out of scope:
- Any new source (Zabbix is C-046).
- Changing dedupe, host matching, the severity policy or full-auto rules.
- The Alertmanager poller's fetch logic (`backend/app/tasks/ai_alerts.py`); it only gets its source name from the registry.
- Writing results back to a sender.

## Context
- The only webhook intake is `grafana_alerts_webhook` (`backend/app/api/webhooks.py:273`). It checks the shared token with `_authorized()` (line 242, bearer or basic, constant-time), checks the `alerts.intake_enabled` setting, parses with `from_grafana_webhook`, calls `record(db, alert, source="grafana_webhook")`, notifies with `notify_alert_fired`, commits, and queues `app.tasks.alert_handling.handle_alert` for each new firing alert.
- Normalisation lives in `backend/app/ai/alerts.py`: `NormalizedAlert` (line 67), `_normalize` (113), `from_grafana_webhook` (150), `from_alertmanager_v2` (175), `resolve_host` (205), `record` (248, upsert on `uq_alert_events_fingerprint_starts`), `meets_severity` (304) with `SEVERITY_ORDER = ("info", "warning", "critical")`.
- The second producer is the poller: `backend/app/tasks/ai_alerts.py:262` records with `source="alertmanager_poll"`.
- Source names are a tuple, `ALERT_SOURCES` in `backend/app/ai/models.py:416`; `alert_events.source` is `String(32)` with no database constraint (verify in the migration that created it).
- Three places compare the source string: `backend/app/ai/alert_autonomy.py:155` and `backend/app/alerts/labels.py:139` (a webhook-sourced alert needs a token of at least `MIN_WEBHOOK_TOKEN_CHARS` before it may change a host), and `backend/app/api/alerts.py:293` (`last_webhook_alert_at`, `last_poll_alert_at`, and a fixed `webhook_path`).
- The token is `settings.alerts.webhook_token` (`AlertsConfig`, `backend/app/config.py:129`), file-level on purpose.
- `/api/webhooks/` is CSRF-exempt by prefix (`backend/app/middleware/csrf.py:78`) and each webhook route is listed in `PUBLIC` in `backend/tests/test_permissions.py:40`.
- C-022 (stage 1) hardens this webhook first (source-IP allowlist, optional HMAC). Read its spec and the merged code before starting: the seam must carry those checks, not bypass them.
- Existing tests: `backend/tests/ai/test_alerts.py`, `test_alert_intake_status.py`, `test_alert_labels.py`, `test_remediation.py`.

## Dependencies
- C-022 merged.
- C-046 is the first consumer and the proof that the seam is sufficient.

## Implementation steps
1. Create `backend/app/alerts/sources.py`:
   - `@dataclass(frozen=True) class AlertSource` with: `key` (the `alert_events.source` value, at most 32 characters), `label` (shown in the UI), `webhook_slug` (path segment under `/api/webhooks/alerts/`, or None for a pulled source), `parse: Callable[[Any], list[NormalizedAlert]]`, `authenticate: Callable[[Request, bytes], bool]`, and `token_guarded: bool` (True when the sender is authenticated only by the shared token, which is what the two length checks care about).
   - `register_alert_source(source)`, `get_alert_source(key)`, `alert_source_by_slug(slug)`, `alert_sources()`, `reset_alert_sources()` for tests. Registering a duplicate key or slug raises `ValueError`.
   - `async def ingest(db, source, payload) -> IngestResult`: the loop now in the route (`record`, `notify_alert_fired`, commit, queue `handle_alert`), returning recorded and new-firing counts.
2. Move `NormalizedAlert`, `_normalize`, `_parse_time` and `compute_fingerprint` to `backend/app/alerts/normalize.py` and re-export them from `app/ai/alerts.py`, so existing imports and tests keep working. Make `normalize(...)` public for sources.
3. Register the two built-in sources at import of `app/alerts/__init__.py`: `grafana_webhook` (slug `grafana`, `parse=from_grafana_webhook`, `authenticate` wrapping `_authorized` and the C-022 checks, `token_guarded=True`) and `alertmanager_poll` (no slug, `token_guarded=False`).
4. Routes in `backend/app/api/webhooks.py`:
   - Add `POST /api/webhooks/alerts/{source_slug}`: look up the source (404 if unknown), read the body once, call `source.authenticate(request, body)` (401 on failure), check `alerts.intake_enabled`, parse JSON (400 as today), call `ingest`. No `require()`: the sender has no session. Say so in a comment and add `("POST", "/api/webhooks/alerts/{source_slug}")` to `PUBLIC` in `tests/test_permissions.py`.
   - Keep `POST /api/webhooks/grafana-alerts` as a thin alias that calls the same code with the Grafana source, so existing contact points keep working.
5. Replace the string checks: in `alert_autonomy.py:155` and `alerts/labels.py:139` use `get_alert_source(event.source).token_guarded` (an unknown source counts as guarded, the safe side).
6. `GET /api/alerts/intake` (`backend/app/api/alerts.py:249`): keep the existing fields and add `sources: [{key, label, webhook_path, last_alert_at}]` built from the registry. Add the type in `frontend/lib/types.ts`; the alerts page (`frontend/app/(dashboard)/alerts/client-page.tsx`, `frontend/components/alerts/alert-handling.tsx`) lists the sources from it.
7. Replace `ALERT_SOURCES` in `app/ai/models.py` with a comment pointing at the registry, or derive it from the registry if something still reads it (grep first).
8. Bump `PLUGIN_API`'s minor (`backend/app/auth/permissions.py`) and add to the comment: "`app.alerts.sources`: `AlertSource`, `register_alert_source`, `normalize`". Add a paragraph to `CLAUDE.md`.
9. Tests in `backend/tests/ai/test_alert_sources.py`:
   - a fake source registered in the test receives a payload on `/api/webhooks/alerts/fake`, and its alerts are recorded, deduplicated on a second post, matched to a host and queued for handling, with no change to `record` or the policy code;
   - a failing `authenticate` gives 401 and records nothing; an unknown slug gives 404;
   - intake disabled returns the same "ignored" body as today;
   - the legacy Grafana path and the new `/alerts/grafana` path produce identical rows;
   - `token_guarded` drives the two length checks for a guarded and an unguarded fake source.

## Acceptance criteria
- The existing alert tests pass unchanged (`tests/ai/test_alerts.py`, `test_alert_intake_status.py`, `test_alert_labels.py`, `test_remediation.py`).
- A source registered from a test, with no edit to `record`, `resolve_host`, `meets_severity`, `app/tasks/ai_alerts.py::_decide_and_run` or `app/alerts/labels.py::dispatch_for`, produces alert events that follow the same dedupe and severity policy.
- `grep -rn '"grafana_webhook"' backend/app` shows the registration only (and the status endpoint's legacy field).
- A Grafana contact point pointed at the old URL keeps working with no configuration change.
- `PLUGIN_API`'s minor is one higher.

## Test plan
- `cd backend && source .venv/bin/activate && pytest tests/ai/test_alert_sources.py tests/ai/test_alerts.py tests/ai/test_alert_intake_status.py tests/ai/test_alert_labels.py tests/ai/test_remediation.py tests/test_permissions.py tests/test_webhook_csrf.py tests/test_unauthenticated_surface.py -q`
- Full suite before the stage review: `pytest tests/ -q`; `ruff check . && ruff format --check .`; `bandit -q -r app -b bandit-baseline.json`.
- `cd frontend && npx tsc --noEmit && npm run lint`.
- On the local Docker stack on lin-manager-2: send a saved Grafana payload with `curl` to both paths and confirm one alert row with `dedup_count` 2.

## Docs and changelog
- `docs/ui/alerts.md`, section "Two ways in": the generic webhook path, and that the old Grafana path still works.
- `CHANGELOG.md` `[Unreleased]` / Changed: "Alert intake goes through a registry of sources; Grafana is the first. `/api/webhooks/alerts/grafana` is the new path and the old one still works."

## Risks and rollback
- An authentication regression on an endpoint that can start unattended changes is the main risk. Keep `_authorized` and the C-022 checks as they are and call them from the source; the tests for a bad token must pass on both paths.
- Moving `NormalizedAlert` could break imports; the re-exports and the unchanged existing tests cover it.
- Rollback: revert the PR. No schema change.

## Assumptions
- [D-05](../DECISIONS.md): Zabbix intake is a Community adapter on this seam.
- [A-01](../DECISIONS.md): webhook hardening is C-022's scope; this item only carries it.
- Assumption (not yet confirmed): all webhook sources share `alerts.webhook_token` unless a source brings its own `authenticate`. Question for Dennis: should each source have its own token in the config file?
- Assumption (not yet confirmed): the new path is `/api/webhooks/alerts/{source_slug}`.

## Definition of done
- [ ] Registry, generic route, Grafana and poller registered, string checks replaced.
- [ ] `tests/ai/test_alert_sources.py` passes; existing alert tests unchanged and green.
- [ ] `PLUGIN_API` bumped with its comment; `CLAUDE.md`, `docs/ui/alerts.md` and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set to done; one line in `planning/PROGRESS.md`.
