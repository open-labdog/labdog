# C-026: Make the AI assistant removable from a build or install

| | |
|---|---|
| Edition | community |
| Source | roadmap ("Make the AI assistant removable") |
| Tier / score | P1 / 4.7 |
| Stage | 2 |
| Effort | L |
| Autonomy | B |
| Depends on | S-009 |

## Goal

An operator can run unbork with the assistant absent: one file-level setting on an install, or one build argument for an image. In that configuration there are no AI routes, no AI pages, no AI providers, no AI tasks and no AI settings, and everything else, including alert-triggered actions, works.

## Scope

In scope:
- A file-level switch, read at process start, that decides whether the assistant is loaded.
- Backend: routers, Celery tasks and beat entries, settings, permissions shown to the UI, AI branches in alerts, actions, verify steps and notifications.
- Frontend: navigation, pages, and AI controls embedded in other pages.
- A Docker build argument that produces an image without the assistant and without the Agent SDK.
- Running the test suite in the assistant-off configuration in CI.

Out of scope:
- Deleting the `backend/app/ai/` package from the source tree or dropping its tables. Non-AI code depends on parts of it (see Context); the tables stay, empty.
- The existing runtime setting `ai.enabled` (a database setting an admin can flip). It keeps its meaning when the assistant is present.
- Default-deny command rules, which already ship.

## Context

- Router: `backend/app/main.py:28,815` includes `app.api.ai.router` under `/api` unconditionally. All its routes declare `require("ai.use")` or `require("ai.approve")` (`backend/app/api/ai.py`).
- Tasks: `backend/app/tasks/__init__.py:314-321` always includes `app.tasks.ai_task`, `ai_approvals`, `ai_snapshots`, `ai_sessions`, `ai_alerts`, `ai_remediation`; routes at lines 73-77. Beat entries are registered through `backend/app/tasks/beat_registry.py`, checked by `tests/test_beat_registry.py`.
- Settings: 26 `ai.*` keys in `backend/app/settings_service.py:234-520`, which imports `app.ai.alert_autonomy`, `alert_mission` and `full_auto_rules` at module scope (lines 10-17). `ai.enabled` is checked in `backend/app/ai/service.py:105`.
- Non-AI code that imports `app.ai`: `AlertEvent` and `AIRollback` live in `backend/app/ai/models.py` and are used by `api/alerts.py:27-30`, `api/webhooks.py:291`, `tasks/alert_handling.py`, `tasks/host_lock.py:291,420`, `alerts/labels.py:55,94,134`; `app.ai.redaction.redact` is used by `notifications/service.py:27` and `ssh_terminal/transcript.py:23`; `app.ai.run_log` by `api/actions.py:16`; `workflows/steps/ai_verify.py` and `workflows/steps/verify.py:241` run the AI verify step; `api/actions.py:590-712` starts investigations of a failed run. `backend/app/models/__init__.py:54-61` registers the AI models.
- Permissions `ai.use` and `ai.approve` are in `backend/app/auth/permissions.py:93-94`; `tests/test_permissions.py` walks every route.
- Frontend: zone "assistant" in `frontend/components/shell/zones.ts:98-103,141,149`; pages `frontend/app/(dashboard)/assistant/` and `frontend/app/(dashboard)/ai-providers/`; the AI section in `frontend/app/(dashboard)/settings/settings-editor.tsx:74-89,248`; AI hints in `hosts/[id]/_tabs/overview.tsx:33`, `overview/_tabs/upcoming.tsx`, alert pages and `components/action-run-detail.tsx`. The frontend is a static export.
- Image: `Dockerfile:54` installs `--extra agent`. `backend/app/ai/agent_sdk/__init__.py` already guards a missing SDK.
- 49 files under `backend/tests/ai/`, plus AI cases elsewhere.

## Dependencies

- S-009 (the image no longer needs the program, so "without the assistant" is only about code paths and the SDK).
- S-005 (alert source seam, later stage) will move `AlertEvent` out of `app/ai`; this item must not block it and must not move the model itself.
- C-048 for the `UNBORK_` prefix of the new setting.

## Implementation steps

1. Config: add `AssistantConfig(enabled: bool = True)` as `Settings.assistant` in `backend/app/config.py` (`[assistant] enabled`, `UNBORK_ASSISTANT__ENABLED`). File-level on purpose, like `MetricsConfig`: removing a subsystem must need server access. Add `backend/app/features.py` with `assistant_enabled() -> bool`.
2. `main.py`: include `ai_router` only when enabled. Add `features: {"assistant": bool}` to the public `GET /api/version` response (`backend/app/api/version.py`), so the login page and shell know before sign-in.
3. Tasks: build the include list and the route map in `tasks/__init__.py` from a base list plus an AI list added only when enabled. Make `beat_registry` skip the AI modules and delete their RedBeat entries when disabled, so a schedule left by an earlier run does not fire into a missing task. Extend `tests/test_beat_registry.py` and `tests/test_task_routing.py` to both configurations.
4. Settings: when disabled, `settings_service` omits every `ai.*` key from the registry it serves and validates (`GET /api/settings` and `PATCH` refuse them as unknown). Move the module-scope `app.ai` imports in `settings_service.py` into the AI branch.
5. Permissions: when disabled, `GET /api/users/me` leaves `ai.use` and `ai.approve` out of `permissions` and `permissions_anywhere`. The catalog itself is unchanged, so `PLUGIN_API` does not move and an extension that names those permissions still loads.
6. Guard each cross-module AI branch with `assistant_enabled()`: `alerts/labels.py::wants_ai` returns False; `tasks/alert_handling.py` skips the AI policy after the alert action; `api/alerts.py` reports `ai_enabled=False` and returns no AI fields; the investigate route in `api/actions.py` is not registered; `workflows/steps/verify.py` and `ai_verify.py` treat an action that asks for AI verify as "AI verify unavailable" using the existing fail-closed path; `notifications/service.py` skips AI event types. `redact` and `AlertEvent` stay importable.
7. Frontend runtime: add `frontend/lib/features.ts` with `useFeatures()` reading `/api/version`. `zones.ts` drops the assistant zone and the `/ai-providers` entry; both pages render the app's not-found view; the settings editor hides the AI section; the embedded AI hints listed in Context are hidden.
8. Build: `Dockerfile` gains `ARG WITH_ASSISTANT=1`. When 0: the backend builder omits `--extra agent`; the frontend builder removes `app/(dashboard)/assistant` and `app/(dashboard)/ai-providers` before `npm run build` (first make sure nothing outside those directories imports from them, moving shared pieces to `frontend/components/` if needed); the final stage sets `ENV UNBORK_ASSISTANT__ENABLED=false`. `packaging/etc/unbork.toml` gains a commented `[assistant]` block.
9. Tests: add a `needs_assistant` pytest marker in `backend/pyproject.toml`, applied to `backend/tests/ai/` through a `conftest.py` there and by hand to AI cases elsewhere; `backend/tests/conftest.py` skips marked tests when `UNBORK_ASSISTANT__ENABLED=false`. Add `backend/tests/test_assistant_removed.py`, which builds the app with the switch off and asserts: no route path contains `/ai`, no registered task name starts with `app.tasks.ai_`, no `ai.*` setting is served, `/api/version` says `assistant: false`, and an alert carrying an action label still dispatches its action.
10. CI: add a job (or matrix leg) in `.github/workflows/ci.yml` that runs the backend suite with `UNBORK_ASSISTANT__ENABLED=false`, and builds the image with `--build-arg WITH_ASSISTANT=0` to check it starts and passes `/health/ready`.

## Acceptance criteria

- With `[assistant] enabled = false`: `GET /openapi.json` lists no path under `/api/ai`; `celery -A app.tasks inspect registered` lists no `app.tasks.ai_*` task; `GET /api/settings` returns no key starting `ai.`; the navigation has no Assistant zone; `/assistant` and `/ai-providers` show not-found.
- `UNBORK_ASSISTANT__ENABLED=false pytest tests/ -q` passes, with AI tests reported as skipped and `test_assistant_removed.py` passing.
- An image built with `WITH_ASSISTANT=0` has no `claude_agent_sdk` package (`python -c "import claude_agent_sdk"` fails), no assistant page files under the static frontend directory, and passes `/health/ready`.
- With the switch off, a Grafana alert with an action label still runs its action, and a sync and an action run complete.
- With the switch on (default), behaviour and the full test suite are unchanged.
- Turning the switch off and on again loses no data: providers and sessions reappear.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_assistant_removed.py tests/test_permissions.py tests/test_beat_registry.py tests/test_task_routing.py -q`
- `UNBORK_ASSISTANT__ENABLED=false pytest tests/ -q` and `pytest tests/ -q` (both, once, before the stage review).
- `cd frontend && npx tsc --noEmit && npm run lint`; `npx playwright test` for the shell and settings specs in both configurations.
- `docker build --build-arg WITH_ASSISTANT=0 -t unbork:noai .` on `lin-manager-2`, start it with the dev compose stack, run one sync and one action against a host named "test".

## Docs and changelog

- `docs/ui/assistant.md`: a section "Running without the assistant" (setting, build argument, what disappears, what stays). `docs/production-deploy.md` and `docs/security-hardening.md`: one paragraph each. `docs/ui/settings.md`: the AI section is absent when the assistant is.
- `CHANGELOG.md` `[Unreleased]`, Added: "The assistant can be left out: `[assistant] enabled = false` on an install, or `--build-arg WITH_ASSISTANT=0` for an image. No AI routes, pages, providers, tasks or settings are loaded."

## Risks and rollback

- A missed AI branch raises at runtime when the switch is off. The CI leg in step 10 runs the whole suite in that configuration to catch it.
- An action whose manifest requires AI verify now fails closed where it used to run. This is the existing behaviour for "AI unavailable" and is documented.
- Stale RedBeat entries for AI tasks. Step 3 removes them.
- Rollback: set the switch back to true, or revert the PR. No schema change is involved.

## Assumptions

- Assumption (not yet confirmed): "removable" means not loaded, by a file-level switch and a build argument. The Python package and the tables remain, because `AlertEvent`, `AIRollback` and `redact` are used by non-AI features. Question for Dennis: is that enough, or must an artefact exist with the code physically absent? That would first need those three moved out of `app/ai`.
- Assumption (not yet confirmed): the `.deb`, `.rpm` and tarball use the setting only; no second package variant is built.
- Assumption (not yet confirmed): the feature flag is exposed on the public `GET /api/version`. It reveals only that the assistant is off.
- [D-04](../DECISIONS.md) for the image side; S-009 delivers it.

## Definition of done

- [ ] Switch, guards, frontend and build argument merged to `dev`.
- [ ] Assistant-off CI leg green; default suite green.
- [ ] Docs and changelog updated.
- [ ] Check on `lin-manager-2` recorded in `planning/PROGRESS.md`.
- [ ] `planning/roadmap.yaml` status set to done.
