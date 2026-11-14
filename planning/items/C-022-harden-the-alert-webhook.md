# C-022: Harden the alert webhook

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Alert remediation hardening: take the host lock, harden the webhook" (second half) |
| Tier / score | P0 / 12.0 |
| Stage | 1 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal

An operator can limit `POST /api/webhooks/grafana-alerts` to named source addresses, and
can require a signature over the request body, so that the shared token is no longer the
only thing between the network and a run on a host.

## Scope

The board text, quoted in full, is the scope
([A-01](../DECISIONS.md)): "Harden the webhook as a root trigger. Full auto now needs a
token of at least 32 characters. Still worth having: a source-IP allowlist, and an HMAC
over the body where the sender's Grafana version can sign webhook requests."

In scope:
- A source-address allowlist for the alert webhook.
- An optional HMAC-SHA256 check over the raw body.
- Showing on the Intake card which of the two are on.

Out of scope:
- The first half of the board item (taking the host lock), which is C-021.
- The GitHub, GitLab and Gitea webhooks in the same file. They already verify a
  per-repository secret.
- A severity floor for full auto. The board records it as considered and not added.
- Making either check a condition for full auto or for alert-started actions.
- Rate limiting and body size limits. The board text does not ask for them.

## Context

- `backend/app/api/webhooks.py`: `grafana_alerts_webhook` (line 278) calls `_authorized`
  (line 243), which compares `settings.alerts.webhook_token` in constant time against a
  Bearer header or either half of Basic auth. An unset token refuses everything with 401.
  The body is read with `request.json()` after the token check.
- `backend/app/config.py`: `AlertsConfig` (line 129) has one field, `webhook_token`. It is
  a file-level setting on purpose: `/api/settings` is readable by every role.
- `backend/app/main.py`: `_get_client_ip` (line 172) and `_is_trusted_proxy` (line 135)
  resolve the real client address from `X-Forwarded-For` only when the peer is in
  `settings.server.trusted_proxies`, and accept CIDR entries. They are private to
  `main.py`, and `webhooks.py` cannot import `main` without a cycle.
- `backend/app/api/alerts.py` (line 282) builds the intake status the Intake card reads;
  its schema is in `backend/app/schemas/alerts.py` (line 75, `webhook_path`).
- `tests/test_permissions.py` (line 59) lists the route as public. That stays true.
- Tests that post to the webhook: `backend/tests/ai/test_alert_labels.py`,
  `test_notifications.py`, `test_remediation.py`, `test_alert_intake_status.py`.
- Docs: `docs/ui/alerts.md` (token at line 50, contact point at line 65),
  `docs/security-hardening.md` (line 370 onward, "the webhook token is the trigger's only
  lock").
- Grafana's webhook contact point can sign requests in recent versions: an HMAC-SHA256 of
  the body in a header (default name `X-Grafana-Alerting-Signature`), with an optional
  timestamp header that is then included in the signed text as `timestamp:body`.

## Dependencies

- None on other items. S-005 (alert source seam) later builds on this endpoint and must
  keep both checks in front of every source.
- A Grafana version that can sign webhooks, for the manual check only. The automated
  tests compute signatures themselves.

## Implementation steps

1. Before coding the HMAC check, read Grafana's current documentation for the webhook
   contact point's HMAC settings and confirm the header names, the hex encoding, and the
   exact signed text with and without a timestamp header. Record the Grafana version in
   the docs change. If it differs from the Context above, follow Grafana.
2. Move `_is_trusted_proxy` and `_get_client_ip` from `main.py` into a new
   `backend/app/client_ip.py` as `is_trusted_proxy` and `get_client_ip`, and import them
   back into `main.py` under the old names so nothing else changes.
3. Extend `AlertsConfig`:
   - `allowed_sources: list[str] = []`: addresses or CIDRs. Empty means any source, as
     today.
   - `webhook_hmac_secret: str = ""`: empty means no signature check.
   - `webhook_hmac_header: str = "X-Grafana-Alerting-Signature"`.
   - `webhook_hmac_timestamp_header: str = ""`: when set, the header is required and is
     part of the signed text.
   - `webhook_hmac_max_skew_seconds: int = 300`: used only with a timestamp header.
   Add a validator that rejects an `allowed_sources` entry `ipaddress.ip_network(...,
   strict=False)` cannot parse, at startup, so a typo fails loudly instead of locking the
   webhook silently.
4. In `grafana_alerts_webhook`, check in this order, before anything touches the database:
   1. Source: if `allowed_sources` is set and `get_client_ip(request)` matches no entry,
      answer 403 `{"detail": "Source address not allowed"}`.
   2. Token: unchanged, 401.
   3. Signature: if `webhook_hmac_secret` is set, read `await request.body()`, compute
      the HMAC, and compare with `hmac.compare_digest`. Missing or wrong signature, a
      missing or non-numeric timestamp, or a timestamp outside the skew: 401
      `{"detail": "Invalid or missing signature"}`.
   Parse the JSON from the bytes already read, so the body that was verified is the body
   that is used.
5. Log each refusal once at warning level with the resolved client address and the
   reason. Never log the body, the token or the signature.
6. Add two booleans to the intake status schema and endpoint, `source_allowlist` and
   `body_signature`, and show them on the Intake card next to the existing token state.
   Find the card from the `webhook_path` field in `frontend/`.
7. Add commented examples for the new keys to the `[alerts]` section of the sample
   config under `packaging/etc/` and to `dev/labdog.toml` if it has that section.

## Acceptance criteria

- With `allowed_sources = ["10.0.0.0/8"]`, a request from `192.0.2.1` gets 403 and records
  nothing, even with a valid token; a request from `10.1.2.3` with a valid token is
  accepted.
- Behind a proxy listed in `server.trusted_proxies`, the forwarded client address is the
  one checked. From a peer that is not a trusted proxy, `X-Forwarded-For` is ignored.
- With `webhook_hmac_secret` set: a correct signature is accepted; a missing one, a wrong
  one, and a correct signature over a different body each get 401 and record nothing.
- With a timestamp header configured: a timestamp older or newer than the skew gets 401;
  a signature computed without the timestamp gets 401.
- With all new keys unset, every existing webhook test passes unchanged.
- An unparsable `allowed_sources` entry stops startup with a message naming the entry.
- `GET` of the intake status reports both booleans correctly.
- A refusal writes one warning log line containing the client address and no secret.

## Test plan

- New file `backend/tests/test_alert_webhook_hardening.py`. Set the config with
  `monkeypatch.setattr(settings.alerts, ...)`, post through the `client` fixture, and set
  the peer and `X-Forwarded-For` as the existing rate-limit tests do.
- `cd backend && source .venv/bin/activate && pytest tests/test_alert_webhook_hardening.py -q`
- `pytest tests/ai/test_alert_labels.py tests/ai/test_notifications.py tests/ai/test_alert_intake_status.py tests/test_webhook_csrf.py tests/test_permissions.py -q`
- `ruff check . && ruff format --check . && bandit -q -r app -b bandit-baseline.json`
- Frontend, for the Intake card: `cd frontend && npx tsc --noEmit && npm run lint`.
- Manual check on local Docker on lin-manager-2: point a Grafana contact point with HMAC
  enabled at the stack and send a test notification, once with the right secret and once
  with a wrong one. No managed host is involved. Do not change the alert configuration of
  the live instance on lin-manager.

## Docs and changelog

- `docs/ui/alerts.md`: a short "Restricting who may send alerts" part after the token
  section, with the keys, the matching Grafana contact point fields and the minimum
  Grafana version.
- `docs/security-hardening.md`: replace "the webhook token is the trigger's only lock"
  with the three layers, and recommend the allowlist whenever full auto is on.
- `docs/ui/settings.md` if it lists `[alerts]` keys.
- `CHANGELOG.md`, `[Unreleased]`, under "Security": "The alert webhook can be limited to
  listed source addresses (`[alerts] allowed_sources`) and can require an HMAC signature
  over the body (`[alerts] webhook_hmac_secret`). Both are off until set."

## Risks and rollback

- An operator who sets `allowed_sources` behind a reverse proxy without
  `server.trusted_proxies` will allow or refuse the proxy's address, not Grafana's. The
  docs must say so, and the refusal log line shows the address that was checked.
- Reading the raw body before the token check would let an unauthenticated sender make
  the server buffer a body. Keep the order in step 4.
- The webhook endpoints are covered by the versioning promise in `docs/upgrade.md`. This
  change only refuses requests when the operator has set a new key, so existing senders
  keep working.
- Rollback: unset the keys to switch the checks off; revert the commit to remove them.

## Assumptions

- [A-01](../DECISIONS.md): the board text is the whole scope.
- Assumption (not yet confirmed): the token stays required when a signature is
  configured. Both must pass. Question for Dennis: should a valid signature be allowed to
  replace the token?
- Assumption (not yet confirmed): the keys are file-level, like `webhook_token`, and are
  not editable on the Settings page.
- Assumption (not yet confirmed): 403 for a refused source and 401 for a bad signature.

## Definition of done

- [ ] Allowlist and signature check implemented in the order given, with the config keys.
- [ ] `tests/test_alert_webhook_hardening.py` passes; existing webhook tests unchanged.
- [ ] Intake card shows both states; `tsc` and lint clean.
- [ ] `docs/ui/alerts.md`, `docs/security-hardening.md`, sample config and `CHANGELOG.md`
      updated.
- [ ] `planning/roadmap.yaml`: C-022 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
