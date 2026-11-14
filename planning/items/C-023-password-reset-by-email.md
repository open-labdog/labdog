# C-023: Password reset by email

| | |
|---|---|
| Edition | community |
| Source | roadmap, "Password reset by email" |
| Tier / score | P1 / 11.0 |
| Stage | 4 |
| Effort | S |
| Autonomy | B |
| Depends on | none |

## Goal

A user with a local password who has forgotten it can ask for a reset on the login page and receives an email with a link that works once, expires, and lets them set a new password. Nobody learns from the request whether an account exists.

## Scope

In scope:

- Mounting the forgot-password and reset-password routes.
- Sending the link by email through the existing SMTP settings.
- Ending the user's sessions and writing an audit row after a reset.
- Two frontend pages and a link on the login form.

Out of scope:

- Accounts without a local password (directory sign-in). They get no email.
- Email verification of new accounts.
- Changing the admin reset (`POST /api/admin/users/{user_id}/reset-password`) or the CLI (`python -m app.auth.users_cli reset-password`). Both stay as the recovery path when email is off.
- HTML email.

## Context

- `backend/app/auth/users.py::UserManager` (line 65) sets `reset_password_token_secret = settings.security.secret_key`. `on_after_forgot_password` (line 237) only logs `User %d forgot password.` and drops the token.
- The routes do not exist: `backend/app/main.py::_include_auth_routers` (line 749) mounts `get_auth_router` and `get_users_router`, not `fastapi_users.get_reset_password_router()`. Yet `backend/app/middleware/csrf.py` (lines 18, 19, 54, 55) already exempts `/api/auth/forgot-password` and `/api/auth/reset-password`.
- fastapi-users (`>=15`, `backend/pyproject.toml`): `forgot_password` builds a JWT with the user id, a fingerprint of the current password hash (`password_fgpt`) and the audience, valid for `reset_password_token_lifetime_seconds` (default 3600), then calls `on_after_forgot_password(user, token, request)`. `reset_password` rejects a token whose fingerprint no longer matches, which makes a token single-use once the password has changed, runs `validate_password`, and calls `on_after_reset_password`. The forgot route answers 202 whether or not the account exists.
- `users.hashed_password` is nullable (directory-only accounts). `forgot_password` would hash `None` for such a user and fail.
- `UserManager.on_after_update` (line 208) clears `must_change_password` and calls `bump_token_version(user)` after a password change. `reset_password` does not go through `update`, so that does not run for a reset.
- Email: `backend/app/notifications/email.py` (`OutgoingEmail`, `send(config, emails)`), `backend/app/notifications/service.py` (`load_smtp`, `is_usable`, `smtp_config`, `public_url`). `backend/app/notifications/urls.py` explains why the link base is the configured `notifications.public_url` and never the request's Host header. The outbox (`notify()`, the `notifications` table, `delivery.py::compose`) is subscription-based and keeps message bodies in the database.
- Login limiter: `backend/app/main.py::_LoginRateLimitMiddleware` (line 521) guards the paths in `backend/app/auth/login_paths.py` and keys its buckets on the `email` or `username` in the body (`login_identity`, line 275).
- `backend/tests/test_permissions.py` has an explicit `PUBLIC` set (line 40) of routes that carry no `require()`, and fails on stale entries.
- Frontend: `frontend/app/(auth)/login/login-form.tsx`, `frontend/app/(auth)/register/`. There is no forgot or reset page.

## Dependencies

- None in code. For the final check: SMTP reachable from local Docker on `lin-manager-2` (a local mail catcher is enough) and `notifications.public_url` set there.

## Implementation steps

1. `backend/app/auth/users.py`, `UserManager`:
   - Set `reset_password_token_lifetime_seconds = 3600` explicitly, with a comment.
   - Override `forgot_password`: return without doing anything when `user.hashed_password` is empty, then call the parent.
   - `on_after_forgot_password`: build `{public_url}/reset-password?token={token}`. If SMTP is not usable or `public_url` is empty, log one warning without the token and return. Otherwise send one plain-text `OutgoingEmail` to the user's address: what was asked, the link, that it works once and for one hour, and that nothing changes if they ignore it. Never log or store the token or the link.
   - Send off the request path: schedule the blocking `send` with `asyncio.to_thread` inside a task that the module keeps a reference to until it finishes, and catch and log its errors with `email.describe`. The response must not wait for SMTP, so its timing does not show whether the account exists.
   - Add `on_after_reset_password`: clear `must_change_password`, call `bump_token_version(user)`, and write `log_action(session, "password_reset", "user", user.id, user.id, after_state={"via": "email"})`, following how `on_after_update` reaches the session.
2. New `backend/app/auth/password_reset.py` for the message text and the send helper, so `users.py` stays short. It opens its own session (`AsyncSessionLocal`) to read the SMTP row and `public_url`, because the hook has no session of its own to share safely.
3. `backend/app/main.py::_include_auth_routers`: `app.include_router(fastapi_users.get_reset_password_router(), prefix="/api/auth", tags=["auth"])`. These two routes run before sign-in and carry no `require()`; say so in a comment.
4. `backend/app/auth/login_paths.py`: add `/api/auth/forgot-password` to the built-in password login paths so the per-account buckets apply (its JSON body has `email`). Check that `reset_password_login_paths()` and the tests that count the paths still hold. `/api/auth/reset-password` has no account name in its body, so it relies on the general API limiter; confirm in a test that it is limited at all.
5. `backend/tests/test_permissions.py`: add `("POST", "/api/auth/forgot-password")` and `("POST", "/api/auth/reset-password")` to `PUBLIC`, each with a short reason comment.
6. Frontend:
   - `frontend/app/(auth)/forgot-password/page.tsx` with a form component: an email field, `POST /api/auth/forgot-password`, then always the same message ("If that address has an account with a password, a link is on its way. No email? Ask an administrator to reset it.").
   - `frontend/app/(auth)/reset-password/page.tsx` with a form component: reads `token` from the query string on the client (the app is a static export), asks for the new password twice, `POST /api/auth/reset-password`, shows the password-policy message from a 400, shows "This link is no longer valid. Ask for a new one." for `RESET_PASSWORD_BAD_TOKEN`, and on success sends the user to `/login` with a notice.
   - `login-form.tsx`: a "Forgot password?" link under the password field.
   - Make sure both pages are reachable without a session (check `frontend/app/(auth)/layout.tsx` and any redirect logic in `frontend/lib/auth.ts`).
7. Check the response headers for the reset page: if no `Referrer-Policy` is set by the backend's security headers middleware, set `no-referrer` (or `same-origin`) so the token in the URL does not leak to another site.
8. Tests in `backend/tests/test_password_reset.py`, with `send` replaced by a recorder: a request for a known address sends one email whose link starts with the configured public URL; an unknown address, an inactive account and a directory-only account all return 202 and send nothing; with SMTP off or no public URL nothing is sent and the response is still 202; the link's token resets the password, the old password stops working and the new one works; the same token a second time returns 400; a token past its lifetime returns 400 (set the lifetime to a negative number or freeze time); a weak password is refused by the policy; after a reset the old session cookie is rejected, `must_change_password` is false and one `password_reset` audit row exists; the token appears in no log record (`caplog`).
9. Playwright: extend `frontend/e2e/auth.spec.ts` with the forgot page (same message for any address) and the reset page with a bad token.

## Acceptance criteria

- `POST /api/auth/forgot-password` returns 202 for any address, and sends an email only to an active account that has a local password, only when SMTP and `notifications.public_url` are set.
- The emailed link opens the reset page; submitting a valid new password signs nobody in, changes the password, and ends the account's existing sessions.
- The link works once: a second use returns 400. It stops working after one hour.
- The token is not in the logs, the audit log or any table.
- Repeated requests for one address are throttled by the login limiter.
- `tests/test_permissions.py` passes with the two routes in `PUBLIC`.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_password_reset.py tests/test_auth.py tests/test_permissions.py tests/test_unauthenticated_surface.py -q`; full suite once before the stage review; `ruff check . && ruff format --check .`; `bandit -q -r app -b bandit-baseline.json`.
- `cd frontend && npx tsc --noEmit && npm run lint && npx playwright test auth.spec.ts`.
- Running stack: on local Docker on `lin-manager-2` with a mail catcher as the SMTP server, request a reset for a test account, open the link from the caught mail, set a password, sign in, and try the link again. Not on `lin-manager`.

## Docs and changelog

- `docs/ui/admin.md` (accounts): how a user resets a password, what must be configured, and that the admin reset and the CLI remain. `docs/ui/notifications.md`: that SMTP and the public URL also serve password resets. `docs/security-hardening.md`: token lifetime, single use, sessions ended.
- `CHANGELOG.md` `[Unreleased]`, Added: "Password reset by email: a \"Forgot password?\" link on the login page sends a single-use link that expires after one hour. Needs SMTP and the public URL to be configured."

## Risks and rollback

- Account recovery is an attack surface. The mitigations are: no account enumeration (same answer, send off the request path), a link built only from the configured URL, a short single-use token, throttling, sessions ended on reset, and no token in logs or storage. This item is class B so that a person reviews it.
- The reset token is signed with the same secret as sessions (`security.secret_key`) but a different audience. That is fastapi-users' design and is kept.
- A user whose mailbox is compromised loses the account. That is inherent to email reset; the admin can turn it off by leaving SMTP unconfigured.
- Back out by removing the router include; the pages then get 404 from the API and the admin reset and CLI still work. No schema change.

## Assumptions

- Assumption (not yet confirmed): the link is valid for one hour (the library default). Question for Dennis if another lifetime is wanted.
- Assumption (not yet confirmed): the email is sent directly, not through the notification outbox, because the outbox stores bodies and would keep a live token in the database.
- Assumption (not yet confirmed): the "Forgot password?" link is always shown, and the page text covers the case where email is not configured. Showing it only when email works would need a public flag that reveals the mail setup.
- Assumption (not yet confirmed): there is no separate setting to turn the feature off; unconfigured email is the off switch.
- Assumption (not yet confirmed): a reset does not change the account's role or lift a deactivation.

## Definition of done

- [ ] Router mounted, manager hooks, send helper, limiter path and frontend pages merged.
- [ ] Backend tests and the Playwright spec green; ruff, bandit, tsc and lint clean.
- [ ] Docs and `CHANGELOG.md` updated.
- [ ] Checked once end to end on `lin-manager-2`, noted in `planning/PROGRESS.md`; `planning/roadmap.yaml` status set.
