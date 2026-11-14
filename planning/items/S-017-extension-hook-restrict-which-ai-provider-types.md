# S-017: Extension hook: restrict which AI provider types may be configured

| | |
|---|---|
| Edition | shared |
| Source | roadmap, found while writing the stage 3 specs (no board item) |
| Tier / score | P0 / 10.0 |
| Stage | 2 |
| Effort | S |
| Autonomy | B |
| Depends on | S-014 |

## Goal

An installed extension can limit which AI provider types an install accepts. A refused
type cannot be created, tested or used, the form does not offer it, and a provider of
that type stored earlier shows why it no longer runs. With no extension, every type
works as it does after S-014.

## Scope

In scope:
- `set_provider_type_policy()` and `provider_type_refusal()` in a new
  `app.ai.providers.policy` module.
- Enforcement at the API (create, update, test) and where a session picks its provider,
  in the API process and in workers.
- `refused_reason` on the provider response, and a `GET /api/ai/provider-types` route.
- The provider form reading that route.
- A `PLUGIN_API` minor bump, `CLAUDE.md`, the plugin loader's docstring.

Out of scope:
- Any policy. Community ships none and refuses nothing.
- Deleting, disabling or converting stored providers. A refused provider stays in the
  list until an operator changes or deletes it.
- Restricting models, endpoints or credentials within an allowed type.
- The token field and the sign-in flow (S-014), labels (S-008), the binary (S-009).

## Context

- Provider types: `backend/app/ai/schemas.py`, line 20,
  `ProviderType = Literal["openai_compat", "anthropic", "claude_cli", "claude_agent"]`;
  `backend/app/ai/models.py`, line 58, `PROVIDER_TYPES`.
- API: `backend/app/api/ai.py`. `create_provider` (line 133), `update_provider` (line
  204) and `test_provider` (line 278) all declare `require("ai.use")`. `test_provider`
  turns `LLMProviderError` into `AIProviderTestResponse(ok=False, message=...)`.
- Every session resolves its provider through `resolve_provider()` in
  `backend/app/ai/service.py` (line 103), which raises `AIDisabledError` with a message
  for the operator when AI is off, the provider is missing or disabled, or cloud use is
  not allowed. Its callers are `backend/app/api/ai.py` (lines 544, 577, 862),
  `backend/app/tasks/ai_task.py` (lines 164, 286, 443), `backend/app/tasks/ai_alerts.py`
  (line 178) and `backend/app/ai/verify.py` (line 168). `build_runner()` in `ai_task.py`
  (line 74) then picks `AgentLoop` or `AgentSDKRunner`.
- Response shape: `AIProviderResponse` (`backend/app/ai/schemas.py`, line 100), built at
  line 165 from capability functions in `backend/app/ai/providers/factory.py`
  (`supports_tools`, `sends_data_offsite`, `uses_subscription`).
- Form: `frontend/app/(dashboard)/ai-providers/client-page.tsx`. `TYPE_LABEL` (line 18)
  lists the types, the `<select id="provider_type">` is at line 482, and a provider card
  shows `TYPE_LABEL[provider.provider_type]` at line 404. `AIProviderType` and the
  provider type are in `frontend/lib/types.ts` (lines 1139 and 1156).
- Nothing under `backend/app/ai/` consults an extension today. Extensions load in the API
  process and in Celery workers, so a policy set in `register(app)` is present wherever a
  session starts.
- S-014 changes `create_provider` and `update_provider` (credential rules for the two
  program-backed types) and the same form. This item builds on the merged result.

## Dependencies

- S-014 merged, because both edit the same handlers and form.
- No new package.

## Implementation steps

1. Create `backend/app/ai/providers/policy.py`:
   - `ProviderTypePolicy = Callable[[str], str | None]`: given a provider type, return
     `None` to allow it, or a sentence for the operator to refuse it.
   - `set_provider_type_policy(policy: ProviderTypePolicy | None)`; `None` restores the
     default (allow everything).
   - `provider_type_refusal(provider_type: str) -> str | None`. It returns `None` when no
     policy is set. A policy that raises is logged with `logger.exception` and treated as
     a refusal with a fixed message ("This provider type is not available: the install's
     provider policy failed. See the server log."), so a broken policy never widens what
     is accepted.
2. `backend/app/api/ai.py`:
   - `create_provider`: after the existing checks, a refusal answers 400 with the message
     as `detail`.
   - `update_provider`: the same, checked against the type the row will have after the
     update (`AIProviderUpdate.provider_type` may change it, `schemas.py` line 68). So
     an update that moves a refused provider to an allowed type passes, and any other
     update of a refused provider is refused. Deleting stays allowed.
   - `test_provider`: a refusal returns `AIProviderTestResponse(ok=False, message=...)`.
   - New route `GET /api/ai/provider-types`, `Depends(require("ai.use"))`, returning one
     row per entry of `PROVIDER_TYPES`: `{type, allowed, reason}`.
3. `backend/app/ai/service.py`, `resolve_provider()`: after the provider is found and
   before the off-site check, raise `AIDisabledError(reason)` when its type is refused.
   This covers chat sessions, investigations, alert handling and verification in one
   place, for both runners.
4. `backend/app/ai/schemas.py`: add `refused_reason: str | None = None` to
   `AIProviderResponse` and fill it where the response is built (line 165).
5. Frontend: add `refused_reason` to the provider type in `frontend/lib/types.ts`. In
   `client-page.tsx`, fetch `/api/ai/provider-types` with TanStack Query, leave refused
   types out of the type `<select>` for a new provider, and show `refused_reason` on the
   card of an existing provider, with its Test button disabled. If the form's default
   type (`openai_compat`, line 174) is refused, start on the first allowed type.
6. Bump `PLUGIN_API` to the next free minor and add the comment line:
   "`app.ai.providers.policy`: `set_provider_type_policy`, `provider_type_refusal`
   (lets an extension limit which provider types an install accepts)."
7. `CLAUDE.md`, section "Extensions (plugins) and directory sign-in": one bullet for the
   hook, saying where it is enforced and that it never deletes a provider. Update the
   `backend/app/plugins.py` docstring's list of hooks.
8. Tests, in a new `backend/tests/ai/test_provider_type_policy.py`. A stand-in extension
   in the test module (a `register(app)` with `requires_plugin_api`, loaded with
   `load_plugins(app, [EntryPoint(...)])` as in `tests/test_external_auth.py`, line 315)
   sets a policy that refuses `claude_cli`. A fixture calls
   `set_provider_type_policy(None)` before and after each test.
9. Ask for review before merge (class B). The reviewer checks the fail-closed rule in
   step 1 and that the PR text stays neutral: it describes the hook, not any policy.

## Acceptance criteria

- With the stand-in policy: `POST /api/ai/providers` with `provider_type: "claude_cli"`
  returns 400 and the policy's message; the same body with `openai_compat` returns 201.
- A `claude_cli` provider inserted before the policy was set is listed with
  `refused_reason` set; `POST /api/ai/providers/{id}/test` answers `ok: false` with the
  message; `PATCH` with `provider_type: "openai_compat"` and a base URL returns 200 and
  clears `refused_reason`; `DELETE` still returns 204.
- `resolve_provider()` raises `AIDisabledError` with the message for that provider, by
  id and as the default, and creating a session with it returns the same error the API
  gives for a disabled provider.
- `GET /api/ai/provider-types` lists all four types, with `allowed: false` and the
  reason for `claude_cli` only. A viewer gets 403.
- A policy that raises refuses every type with the fixed message and logs one error.
- With no policy set, every existing test in `backend/tests/ai/` passes unchanged and
  `refused_reason` is `null` everywhere.
- In the form, a refused type is absent from the picker and an existing refused provider
  shows the reason.
- `PLUGIN_API` is one minor higher (or more), and `is_compatible("1.2")` is still true.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/ai/test_provider_type_policy.py tests/ai/test_providers.py tests/ai/test_provider_credentials.py tests/test_permissions.py -q`
  (`test_provider_credentials.py` comes from S-014).
- `ruff check . && ruff format --check .`
- `cd frontend && npx tsc --noEmit && npm run lint && npx playwright test ai-provider`
- The full backend suite runs once before the stage review.
- Nothing here needs a managed host. The form check with a policy installed runs on
  local Docker on lin-manager-2 with a small test plugin, by the stage review.

## Docs and changelog

- `CLAUDE.md` and the `app/plugins.py` docstring as in step 7. There is no extension
  developer page under `docs/` at the time of writing; if one exists by then, add the
  hook there. `docs/ui/assistant.md`: one sentence that an install may not offer every
  provider type, and that a provider can show a reason why it is unavailable.
- `CHANGELOG.md`, `[Unreleased]`, under "Added": "Extension hook that lets an extension
  limit which AI provider types an install accepts. A Community install accepts every
  type, as before. Plugin API minor bump."

## Risks and rollback

- A refused default provider stops every session until another default is chosen. The
  error names the reason, and the card shows it.
- Failing closed on a broken policy can take the assistant away from an install whose
  extension has a bug. That is the safer side, and the log names the cause.
- The new list route adds one request to the providers page. It is small and cached by
  the query client.
- Rollback: revert the commit and set `PLUGIN_API` back. An extension built against the
  new minor is then skipped at load with a log line, and every type is accepted again.

## Assumptions

- Assumption (not yet confirmed): one policy per install (a setter, not a list), like
  `set_permission_resolver()`. A second extension setting a policy replaces the first.
  Question for Dennis: is a list of policies, where any refusal wins, worth the extra
  code now?
- Assumption (not yet confirmed): a policy sees the provider type only, not the base URL
  or model.
- Assumption (not yet confirmed): a refused stored provider is kept and shown, never
  hidden, so the operator can see what to change.
- The minor bump follows the rule in `CLAUDE.md`: additions bump the minor.

## Definition of done

- [ ] `app/ai/providers/policy.py`, the API and `resolve_provider` checks, the response
      field, the list route and the form change are in place.
- [ ] `tests/ai/test_provider_type_policy.py` passes; the AI and permission tests still
      pass; frontend type check and lint pass.
- [ ] `PLUGIN_API` bumped with its comment; `CLAUDE.md`, the `plugins.py` docstring,
      `docs/ui/assistant.md` and `CHANGELOG.md` updated.
- [ ] Reviewed before merge.
- [ ] `planning/roadmap.yaml`: S-017 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
