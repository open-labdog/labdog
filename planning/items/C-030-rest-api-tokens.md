# C-030: REST API tokens

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "REST API tokens and a command-line tool" (the token half; the CLI is C-031) |
| Tier / score | P2 / 6.5 |
| Stage | 7 |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal
A script or CI job can call the API with `Authorization: Bearer <token>` instead of a browser session. A token acts as the account that made it, never with more rights than that account, can be limited to fewer permissions, can expire, and can be revoked.

## Scope
In scope:
- An `api_tokens` table, tokens stored as a hash, shown once at creation.
- Bearer authentication on every `require()` route, with the CSRF check skipped for bearer requests that carry no session cookie.
- Routes to create, list and revoke your own tokens, and for an admin to list and revoke anyone's.
- A "API tokens" section in the frontend, docs, changelog.

Out of scope:
- The command-line tool (C-031, parked).
- Tokens for the SSH terminal websocket (`backend/app/auth/ws_auth.py` stays cookie-only).
- Service accounts that are not a user, OAuth, and token scoping to hosts or groups (a token inherits its owner's scope).

## Context
- Authentication is one fastapi-users backend: `auth_backend` (`backend/app/auth/users.py:52`), a `CookieTransport` named `labdog_auth` with `VersionedJWTStrategy` (`backend/app/auth/token_version.py`). `current_active_user` (`users.py:253`) is what `require()` depends on (`backend/app/auth/permissions.py:223`, inner `_check` at about line 233).
- Permissions come from `permissions_for(user, scope)` (`permissions.py`, about line 285), which calls the registered resolver. `authorize()` and `require_on()` both go through it, and so does `GET /api/users/me`.
- `CSRFMiddleware.__call__` (`backend/app/middleware/csrf.py:93`) refuses every POST, PUT, PATCH and DELETE that has no CSRF cookie and header, except `_EXEMPT_PATHS`, `_EXEMPT_PREFIXES` and `is_password_login_path()`. A bearer client has neither, so it is refused today.
- `tests/test_permissions.py` fails on any route without a `require()` unless it is in its `PUBLIC` set (line 40), and checks that each route refuses a role lacking its permission.
- `require()` returns 403 `password_change_required` for a user with `must_change_password` (`permissions.py:236`).
- Nothing in `backend/app/auth/` knows about bearer tokens (searched for `Bearer`, `api_token`). The only `Bearer` handling is the alert webhook's shared token (`backend/app/api/webhooks.py:242`).
- Latest migration is `backend/alembic/versions/0060_schedule_owner.py`.

## Dependencies
- None. C-031 and any integration that calls the API depend on this.
- The Enterprise RBAC resolver is unaffected: the token limit is applied after the resolver answers.

## Implementation steps
1. Model `ApiToken` in `backend/app/models/api_token.py`, table `api_tokens`: `id`, `user_id` (FK `users.id`, `ON DELETE CASCADE`, indexed), `name` (String 100), `token_prefix` (String 12, shown in lists), `token_hash` (String 64, unique index), `permissions` (JSONB list, NULL means "everything the owner has"), `expires_at` (nullable), `last_used_at` (nullable), `revoked_at` (nullable), `created_at`. Register it in `app/models/__init__.py` (`import_all_models`).
2. Migration `backend/alembic/versions/0061_api_tokens.py` (use the next free number if 0061 is taken). Additive only, with a `downgrade()` that drops the table.
3. `backend/app/auth/api_tokens.py`:
   - `generate_token() -> tuple[str, str, str]`: plaintext `unb_` + `secrets.token_urlsafe(32)`, its prefix, and `hashlib.sha256(plaintext).hexdigest()`. The token has 256 bits of entropy, so a fast hash is correct here; do not use the password hasher.
   - `ApiTokenStrategy` implementing fastapi-users' `Strategy.read_token(token, user_manager)`: hash the presented value, select the row by `token_hash`, refuse if missing, `revoked_at` set, or `expires_at` in the past; load the user; set `user._api_token = row`; update `last_used_at` at most once a minute. `write_token` and `destroy_token` raise `NotImplementedError` (tokens are not issued through a login route).
   - `api_token_backend = AuthenticationBackend(name="api_token", transport=BearerTransport(tokenUrl="auth/jwt/login"), get_strategy=...)`.
4. In `backend/app/auth/users.py`, pass both backends to `FastAPIUsers(...)`. Do not include an auth router for the new backend. Check that `current_active_user` tries the cookie first and then the bearer header.
5. In `permissions_for()` (`permissions.py`), after the resolver answers: if `getattr(user, "_api_token", None)` has a non-NULL `permissions` list, return the intersection. This keeps `authorize`, `require_on` and `/api/users/me` consistent.
6. Add `Permission.TOKENS_MANAGE = "tokens.manage"` to the catalog and to `_VIEWER` (so all three roles hold it; a token never exceeds its owner). Bump `PLUGIN_API`'s minor and add a line to the comment above it: "API tokens: `tokens.manage`, and `permissions_for` narrows by the token in use". Update `frontend` permission lists and `docs/ui/admin.md`.
7. CSRF: in `CSRFMiddleware.__call__`, skip the check when the request has an `Authorization: Bearer` header and no `labdog_auth` cookie (read the cookie name from `cookie_transport`). A browser cannot be made to send that header cross-site without a CORS preflight, and a request that also carries the session cookie keeps the full check.
8. Routes in a new `backend/app/api/api_tokens.py`, included in `create_app()` (`backend/app/main.py`, next to `admin_users_router`):
   - `GET /api/users/me/tokens`, `POST /api/users/me/tokens`, `DELETE /api/users/me/tokens/{token_id}`: `Depends(require("tokens.manage"))`. `POST` returns the plaintext once. Requested `permissions` must be a subset of the caller's current global permissions, else 422.
   - `GET /api/admin/users/{user_id}/tokens`, `DELETE /api/admin/tokens/{token_id}`: `Depends(require("users.manage"))`.
   - Every handler refuses a request that was itself authenticated by a token (403 `token_cannot_manage_tokens`), so a leaked token cannot mint another. Apply the same refusal to `PATCH /api/users/me`.
   - Write `log_action` rows `api_token_created` and `api_token_revoked` (name and prefix only, never the token).
9. Frontend: an "API tokens" section under Settings (`frontend/app/(dashboard)/settings/client-page.tsx`), gated with `useCan("tokens.manage")`: list (name, prefix, permissions, expiry, last used), create dialog (name, expiry 30/90/365 days or none, optional permission checklist), a copy-once panel, revoke with confirmation. On the Users page edit dialog, an admin sees the account's tokens and can revoke them. Add types to `frontend/lib/types.ts`.
10. Tests in `backend/tests/test_api_tokens.py` (see Acceptance criteria), and extend `tests/test_webhook_csrf.py` or a new CSRF case for step 7.

## Acceptance criteria
- `curl -H "Authorization: Bearer $T" /api/hosts` returns 200 for a viewer's token; the same token on `POST /api/sync/hosts/1/bulk` returns 403.
- A token created with `permissions=["hosts.view"]` by an operator is refused on a `sync.run` route although its owner is allowed.
- A revoked token, an expired token, a token of a deactivated user and a garbage string all return 401.
- The database row holds no plaintext: `token_hash` is 64 hex characters and the plaintext appears in no column and in no log line or audit row.
- A bearer `POST` with no cookies succeeds without a CSRF header; a cookie-authenticated `POST` without the CSRF header is still refused, also when it adds a bogus `Authorization: Bearer` header.
- A token cannot create, list or revoke tokens, and cannot change its owner's password.
- A token of a user with `must_change_password` gets 403 `password_change_required`.
- Deleting the user deletes their tokens.
- `tests/test_permissions.py` passes with the new routes and permission.

## Test plan
- `cd backend && source .venv/bin/activate && pytest tests/test_api_tokens.py tests/test_permissions.py tests/test_permission_scopes.py tests/test_webhook_csrf.py tests/test_auth.py -q`
- Full suite once before the stage review: `pytest tests/ -q`; `ruff check . && ruff format --check .`; `bandit -q -r app -b bandit-baseline.json`.
- `cd frontend && npx tsc --noEmit && npm run lint`, and `npx playwright test` for the settings spec that covers the new section (stack on local Docker on lin-manager-2).
- Manual check on the lin-manager-2 stack: create a token in the UI, list hosts with `curl`, revoke it, see 401. No managed host is touched.

## Docs and changelog
- New `docs/api-tokens.md` (create, use with curl, limit, expire, revoke, what a token cannot do); link it from `docs/README.md` and `docs/ui/admin.md`. A paragraph in `docs/security-hardening.md` on token hygiene.
- `CHANGELOG.md` `[Unreleased]` / Added: "API tokens: call the API with `Authorization: Bearer`, limited to the permissions you choose, with expiry and revocation."

## Risks and rollback
- The CSRF exemption is the sensitive part. The rule must require the bearer header and the absence of the session cookie; the test with both present guards it.
- A second authentication backend changes the order in which credentials are read. Keep the cookie first so browser behaviour is unchanged.
- `last_used_at` writes on every request would add load; the one-minute throttle avoids that.
- Rollback: revert the PR and run `alembic downgrade -1`. Tokens stop working; nothing else is affected.

## Assumptions
- Assumption (not yet confirmed): a token belongs to a user and acts as that user. No separate service-account object. Question for Dennis: is that enough for the integrations in mind, or is a user-less service account wanted?
- Assumption (not yet confirmed): every role may create tokens (`tokens.manage` on viewer, operator and admin). Question for Dennis: should token creation be admin-only instead?
- Assumption (not yet confirmed): changing a password does not revoke the account's tokens; revoking is explicit. Deactivating the account stops them.
- Assumption (not yet confirmed): the token prefix is `unb_`.
- [A-03](../DECISIONS.md): this item was raised from board P3 to P2.

## Definition of done
- [ ] Model, migration, strategy, CSRF rule, routes and frontend merged to `dev`.
- [ ] `tests/test_api_tokens.py` and the CSRF cases pass; full backend suite green.
- [ ] `docs/api-tokens.md` written; `CHANGELOG.md` updated; `PLUGIN_API` comment updated.
- [ ] `planning/roadmap.yaml` status set to done; one line in `planning/PROGRESS.md`.
