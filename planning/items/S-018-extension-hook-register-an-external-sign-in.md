# S-018: Extension hook: register an external sign-in callback path

| | |
|---|---|
| Edition | shared |
| Source | roadmap, found while writing the stage 7 specs (no board item) |
| Tier / score | P2 / 9.0 |
| Stage | 7 |
| Effort | S |
| Autonomy | B |
| Depends on | none |

## Goal

An installed extension can name one exact path on which an identity provider posts the
result of a sign-in back to unbork, and that POST reaches the extension's handler
although it comes from another site and carries no CSRF token. Every other path keeps
its CSRF check, and a Community install registers no such path. Enterprise SAML sign-in
will be the first user.

## Scope

In scope:
- `register_external_callback_path()`, `is_external_callback_path()` and a reset
  function for tests in `backend/app/auth/login_paths.py`.
- `CSRFMiddleware` exempting a POST to a registered path.
- Keeping these paths out of the login rate limiter and its 8 KB body cap.
- A `PLUGIN_API` minor bump, `CLAUDE.md`, the plugin loader's docstring.

Out of scope:
- Any sign-in protocol, parser or route. Those belong to the extension.
- Prefixes or path templates. Exact paths only, as for password logins.
- Changes to `resolve_external_user()`, `issue_session()` or the login page.
- A new rate limiter. The general API limit still applies to these paths.

## Context

- `backend/app/middleware/csrf.py`: `CSRFMiddleware.__call__` (line 93) refuses every
  POST, PUT, PATCH and DELETE that lacks a matching CSRF cookie and `X-CSRF-Token`
  header, except `_EXEMPT_PATHS` (line 46), `_EXEMPT_PREFIXES` (line 75, the webhooks)
  and paths for which `is_password_login_path()` is true (line 107).
- `backend/app/auth/login_paths.py` is the existing extension seam:
  `register_password_login_path()` (line 25) accepts an exact `/api/` path with no `?`
  and no `{`, `is_password_login_path()` (line 32), `reset_password_login_paths()`
  (line 36). Its docstring says two middlewares read it and must agree.
- The second reader is `_LoginRateLimitMiddleware` (`backend/app/main.py`, line 521). For
  a POST to a password login path it buffers the body to find the account name
  (`_buffered_receive`, line 305) and answers 413 above `_MAX_LOGIN_BODY = 8192` bytes
  (line 254, SEC-42). A sign-in response posted by an identity provider is a signed
  document that is usually larger than that, and it names no account to throttle on.
- So neither existing registration fits: a password login path is rate limited per
  account and capped at 8 KB, and no other exemption is open to an extension.
- The general limiter (`_WSafeSlowAPI`, added in `_add_middleware`, `main.py` line 728,
  with `settings.rate_limit.api`) applies to every HTTP path.
- After a successful callback an extension calls `resolve_external_user()` and
  `issue_session()` (`backend/app/auth/external.py`), and on refusal redirects to
  `/login?sso_error=<code>`. Those exist since plugin API 1.1.
- Tests for the existing seam: `backend/tests/test_external_auth.py`,
  `TestPasswordLoginPaths` (line 362) with the `login_paths` fixture (line 354), and
  `backend/tests/test_webhook_csrf.py`, `tests/test_csrf.py`,
  `tests/test_login_rate_limit_keys.py`.
- `PLUGIN_API = "1.2"` (`backend/app/auth/permissions.py`, line 71); earlier stages add
  minors.

## Dependencies

- No item dependency and no new package.

## Implementation steps

1. `backend/app/auth/login_paths.py`:
   - `_callback_paths: set[str]`, empty in Community.
   - `register_external_callback_path(path: str) -> None`, with the same exact-path rule
     as `register_password_login_path` (share one private validator). It raises
     `ValueError` when the path is already a password login path, and
     `register_password_login_path` raises when the path is already a callback path: a
     path is one or the other.
   - `is_external_callback_path(path: str) -> bool`.
   - `reset_external_callback_paths()` for tests.
   - Extend the module docstring with the rules a handler on such a path must follow:
     it must authenticate the request from its body alone (a signed response it
     verifies), it must never act on the caller's session cookie, it must bound the body
     it reads, and it must answer with a redirect, never with data.
2. `backend/app/middleware/csrf.py`: in the guarded branch, also skip the check when
   `method == "POST" and is_external_callback_path(path)`. PUT, PATCH and DELETE to the
   same path stay guarded. Add the case to the module docstring's list of exemptions.
3. `backend/app/main.py`: no behaviour change. Add one sentence to the comment above
   `_MAX_LOGIN_BODY` (line 248) saying callback paths are deliberately not in the login
   limiter, so nobody "fixes" the difference later.
4. Bump `PLUGIN_API` to the next free minor and add the comment line:
   "`app.auth.login_paths`: `register_external_callback_path` (a CSRF-exempt POST path
   for a sign-in response from an identity provider; not in the login limiter)."
5. `CLAUDE.md`, section "Extensions (plugins) and directory sign-in": extend the
   `app/auth/login_paths.py` bullet with the new function and the handler rules from
   step 1. Update the `backend/app/plugins.py` docstring's list of hooks.
6. Tests, added to `backend/tests/test_external_auth.py` as `TestExternalCallbackPaths`
   next to `TestPasswordLoginPaths`, with the `login_paths` fixture extended to reset
   both sets. A stand-in extension in the test module (a `register(app)` with
   `requires_plugin_api`, loaded with `load_plugins(app, [_ep(...)])` as `TestPlugins`
   does at line 319) registers `/api/auth/test-idp/acs` and adds a POST route there that
   echoes the body length.
7. Ask for review before merge (class B). The reviewer checks that the exemption is
   exact, POST only, and that the handler rules are written down.

## Acceptance criteria

- Before registration, a POST to `/api/auth/test-idp/acs` without a CSRF token returns
  403 from the middleware. After the stand-in registers it, the same POST reaches the
  route.
- A 20 000 byte form body posted to the registered path reaches the route (no 413).
- PUT, PATCH and DELETE to the registered path without a CSRF token still return 403.
- A POST without a CSRF token to another path of the same stand-in router returns 403.
- `register_external_callback_path` raises `ValueError` for
  `/api/auth/saml/acs?provider=x`, `/api/auth/{slug}/acs`, `/acs` and `api/x`, and for
  `/api/auth/jwt/login`.
- `is_password_login_path()` is false for a callback path, so the login limiter does not
  run for it (assert with a body over 8 KB and the limiter enabled in that one test).
- With nothing registered, `is_external_callback_path()` is false for every path and
  `tests/test_csrf.py`, `tests/test_webhook_csrf.py` and
  `tests/test_login_rate_limit_keys.py` pass unchanged.
- `PLUGIN_API` is one minor higher (or more), and `is_compatible("1.2")` is still true.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_external_auth.py tests/test_csrf.py tests/test_webhook_csrf.py tests/test_login_rate_limit_keys.py tests/test_permissions.py -q`
- `ruff check . && ruff format --check . && bandit -q -r app`
- The full suite runs once before the stage review.
- Nothing here needs a running stack or a managed host. The end-to-end check with a real
  identity provider belongs to the extension that uses the hook.

## Docs and changelog

- `CLAUDE.md` and the `app/plugins.py` docstring as in step 5. There is no extension
  developer page under `docs/` at the time of writing; if one exists by then, add the
  hook there. `docs/security-hardening.md` does not mention CSRF today; add two
  sentences: state-changing requests need the CSRF token, and an installed extension may
  register a sign-in callback path that is exempt, which the startup log names (see
  Risks).
- `CHANGELOG.md`, `[Unreleased]`, under "Added": "Extension hook for sign-in callbacks.
  An extension can register one exact path that an identity provider posts a sign-in
  response to. A Community install registers none. Plugin API minor bump."

## Risks and rollback

- A CSRF-exempt path is only safe if its handler ignores the session cookie and trusts
  nothing but a verified body. The hook cannot enforce that; step 1 writes it into the
  contract, and the exemption is limited to one exact path and one method.
- An unauthenticated POST with a large body is a way to make the server read data. The
  handler must cap what it reads (the contract says so), and the general API rate limit
  applies. Community adds no cap of its own here; see Assumptions.
- An operator cannot see from the UI which paths are exempt. Log each registered
  callback path once at info level when it is registered.
- Rollback: revert the commit and set `PLUGIN_API` back. An extension built against the
  new minor is then skipped at load with a log line, and its callback path is guarded
  again.

## Assumptions

- Assumption (not yet confirmed): the hook sets no body limit and leaves that to the
  handler, because the right limit depends on the protocol. Question for Dennis: should
  Community enforce a ceiling (for example 1 MiB) on these paths anyway?
- Assumption (not yet confirmed): callback paths are not throttled per account, because
  the body names no account before it is verified. The extension counts refusals itself
  if it needs to.
- Assumption (not yet confirmed): the functions live in `app/auth/login_paths.py` beside
  the password login seam instead of a new module, so the two middlewares keep one place
  to read.
- The minor bump follows the rule in `CLAUDE.md`: additions bump the minor.

## Definition of done

- [ ] The three functions, the CSRF middleware change and the comment in `main.py` are
      in place.
- [ ] `TestExternalCallbackPaths` passes; the CSRF, webhook and login limiter tests
      still pass.
- [ ] `PLUGIN_API` bumped with its comment; `CLAUDE.md`, the `plugins.py` docstring,
      `docs/security-hardening.md` and `CHANGELOG.md` updated.
- [ ] Reviewed before merge.
- [ ] `planning/roadmap.yaml`: S-018 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
