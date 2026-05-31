# BUG-52 — Per-node Proxmox CA certificate trust

Branch-scoped plan (`fix/bug-52`). Delete `plans/` before opening the PR
(see [CONTRIBUTING.md](../CONTRIBUTING.md)). Planning only — no
implementation has landed yet.

## Summary

Today every Proxmox node is reached with
`httpx.AsyncClient(verify=node.verify_ssl)`, where `verify_ssl=False`
(the BUG-45 workaround) disables *all* TLS validation. This change adds a
nullable **plaintext** `ca_cert_pem` column to `proxmox_nodes` so an
operator can upload a PEM CA certificate per node; when present (and
`verify_ssl=True`), labdog verifies the node's TLS certificate against
that CA instead of disabling verification. The existing `verify_ssl`
boolean is kept (additive model). The crux — wiring the PEM through to
httpx's `verify=` — is solved with an **in-memory `ssl.SSLContext`** (no
temp file), which sidesteps the SEC-04 temp-file lifecycle problem
entirely.

### Verify behavior matrix

| `verify_ssl` | `ca_cert_pem` | httpx `verify=` | Meaning |
|---|---|---|---|
| `False` | any (NULL or set) | `False` | No verification (current BUG-45 escape hatch) |
| `True` | set | `ssl.SSLContext` from the PEM | Verify against uploaded private CA |
| `True` | NULL | `True` | Verify against system trust store (current default) |

## Design decisions

### Locked (from maintainer)
- **Additive**: keep the `verify_ssl` boolean, add a nullable
  `ca_cert_pem TEXT`. Matrix above.
- **Plaintext storage**: `ca_cert_pem` is a plaintext `TEXT` column. CA
  certs are public, not secrets — no encryption, no `bytea`, no
  `encrypt_ssh_key`/`decrypt_ssh_key`. The token secret stays AES-GCM
  encrypted as today; only the CA cert is plaintext.

### Recommendations on the open design problems

**1. Temp-file lifecycle → use an in-memory `ssl.SSLContext`.**
httpx accepts an `ssl.SSLContext` for `verify=`. Build it from the PEM
string with `ssl.create_default_context(cadata=pem)` (`cadata` accepts a
PEM string with one or more `BEGIN CERTIFICATE` blocks). Benefits:
- No temp file is ever written → the SEC-04 temp-file concern and the
  `os.open(..., O_EXCL, 0o600)` hygiene note become moot.
- No new `close()`/`__del__` lifecycle required.
- The instantiation sites only pass the new PEM argument; the per-request
  client builder selects `verify=` from the matrix.

Build the context lazily and cache it on the instance (building per
request is correct but wasteful). Wrap the build defensively and surface
malformed-PEM failures as `ProxmoxError` to guard against manually-edited
DB rows (validation at the API boundary makes this rare).

**2. PEM validation → reuse `app.ca_certs.pem_utils`.**
A vetted validator already exists at
[backend/app/ca_certs/pem_utils.py](../backend/app/ca_certs/pem_utils.py):
- `validate_pem_content(pem) -> str` strips/validates, raising
  `ValueError` (→ Pydantic 422) on bad input.
- `parse_pem_certificate` / `compute_fingerprint` extract
  fingerprint/subject/issuer/validity.

Add a `field_validator("ca_cert_pem")` on Create/Update that validates
only non-blank values, plus an explicit size cap (e.g. reject > 64 KB)
before parsing.

> **Caveat (see open question):** `pem_utils.parse_pem_certificate`
> *rejects* certs whose `basicConstraints` is missing or `CA:FALSE`. That
> is correct for "upload a CA", but an operator pasting a self-signed
> *leaf* node cert (common BUG-45 scenario) would be rejected with "not a
> CA certificate".

**3. Response exposure → metadata only, not the raw PEM.**
Add to `ProxmoxNodeResponse`:
- `has_ca_cert: bool` — `node.ca_cert_pem is not None`
- `ca_cert_fingerprint: str | None` — `compute_fingerprint(...)` when set
- optionally `ca_cert_subject` / `ca_cert_not_after` for display

`from_attributes` won't synthesize these — build the response via a small
`to_response(node)` helper or `@computed_field`.

**4. Clearing the CA → tri-state on PUT (empty string = clear).**
Mirror `token_secret`'s "omitted/None = leave unchanged" plus an explicit
clear sentinel:
- omitted / `None` → leave unchanged
- `""` (empty/whitespace-only) → set column `NULL` (clear)
- non-empty → validate and set

The validator must let `""` through; the PUT handler distinguishes the
three cases.

**5. Frontend UX → PEM paste `<textarea>` under the verify_ssl checkbox.**
Shown only when `verify_ssl` is checked (CA is ignored when verification
is off). On edit, prefill nothing but show current state from response
metadata ("CA configured: `<fingerprint>` — paste a new PEM to replace,
or Clear to remove"). Provide a "Clear CA" control that sends
`ca_cert_pem: ""`. Paste-only for v1; file upload is optional polish.

**6. Auth → keep `current_active_user`.**
All proxmox endpoints currently use `current_active_user` (not
superuser). Keep it so this change doesn't silently alter the auth
posture. Tightening all write paths to superuser is a separate decision
(see open questions).

## Implementation steps (ordered)

### DB / model
1. [backend/app/proxmox/models.py](../backend/app/proxmox/models.py) —
   class `ProxmoxNode`: add
   `ca_cert_pem: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)`.
   Add `Text` to the `sqlalchemy` import. Docstring: note the CA is
   plaintext (public) and explicitly NOT encrypted, contrasting with the
   token secret.

### Migration
2. New `backend/alembic/versions/0010_proxmox_node_ca_cert_pem.py`
   mirroring 0009's style:
   - `revision = "0010_proxmox_node_ca_cert_pem"`,
     `down_revision = "0009_vm_mapping_type"`.
   - `upgrade()`: `op.execute("ALTER TABLE proxmox_nodes ADD COLUMN ca_cert_pem TEXT")`
     (nullable, no default — existing rows become `NULL` = current
     behavior).
   - `downgrade()`: `op.execute("ALTER TABLE proxmox_nodes DROP COLUMN ca_cert_pem")`.

### Client
3. [backend/app/proxmox/client.py](../backend/app/proxmox/client.py):
   - `__init__`: add `ca_cert_pem: str | None = None`; store it; init a
     cache slot `self._ssl_context: ssl.SSLContext | None = None`. Add
     `import ssl`.
   - Client builder: choose `verify` per the matrix —
     `False` when `not verify_ssl`; else a cached `_get_ssl_context()`
     when `ca_cert_pem`; else `True`.
   - Add `_get_ssl_context()` that lazily builds + caches
     `ssl.create_default_context(cadata=self.ca_cert_pem)` and re-raises
     failures as `ProxmoxError("Invalid CA certificate: ...")`.
   - Update the class docstring/Args for `ca_cert_pem`.
   - Note: client.py:34's docstring already shows
     `async with ProxmoxClient(...) as client:` — confirm the existing
     context-manager shape when editing.

### Instantiation sites — SIX (verified)
All build from a node/`pn`/`proxmox_node` record already in scope; each
gets one added kwarg `ca_cert_pem=<record>.ca_cert_pem`:
4. [backend/app/workflows/snapshot_cleanup.py:113](../backend/app/workflows/snapshot_cleanup.py#L113)
5. [backend/app/workflows/snapshot_cleanup.py:231](../backend/app/workflows/snapshot_cleanup.py#L231)
6. [backend/app/tasks/action_host.py:340](../backend/app/tasks/action_host.py#L340)
7. [backend/app/tasks/action_group.py:782](../backend/app/tasks/action_group.py#L782)
8. [backend/app/api/proxmox_nodes.py:180](../backend/app/api/proxmox_nodes.py#L180) (POST `/test`)
9. [backend/app/proxmox/discovery.py:58](../backend/app/proxmox/discovery.py#L58)

> Sites 6 and 7 are the destructive action snapshot/rollback paths.
> Missing them would silently leave those paths on system-trust-only
> verification — a correctness gap. (`test_action_*.py` use a
> `_FakeProxmoxClient` and need no change.)

### Schemas / validation
10. [backend/app/proxmox/schemas.py](../backend/app/proxmox/schemas.py):
    - Import `validate_pem_content`, `compute_fingerprint`,
      `parse_pem_certificate` from `app.ca_certs.pem_utils`.
    - `ProxmoxNodeCreate`: add `ca_cert_pem: str | None = None` +
      `field_validator` (blank → unchanged; else size-check +
      `validate_pem_content`).
    - `ProxmoxNodeUpdate`: same field + validator, but allow `""`
      through (clear sentinel) — validate only non-blank values.
    - `ProxmoxNodeResponse`: add `has_ca_cert: bool` and
      `ca_cert_fingerprint: str | None` (optionally subject/not_after).
      Build via a `to_response(node)` helper (or `@computed_field`).

### API endpoints
11. [backend/app/api/proxmox_nodes.py](../backend/app/api/proxmox_nodes.py):
    - `create_proxmox_node`: pass `ca_cert_pem=body.ca_cert_pem` into the
      `ProxmoxNode(...)` constructor (stored verbatim, already validated).
      Token encryption unchanged.
    - `update_proxmox_node`: add the tri-state block near the
      `verify_ssl` update —
      `if body.ca_cert_pem is not None: node.ca_cert_pem = body.ca_cert_pem or None`.
      Add CA presence (not the PEM) to the audit `after_state`.
    - All endpoints returning a node switch to `to_response(node)` so
      `has_ca_cert`/fingerprint populate.
    - Keep `current_active_user` auth.

### Frontend
12. [frontend/lib/types.ts:114-122](../frontend/lib/types.ts#L114-L122): add
    `has_ca_cert: boolean` and `ca_cert_fingerprint?: string | null` to
    `ProxmoxNode`.
13. [frontend/app/(dashboard)/settings/proxmox/client-page.tsx](../frontend/app/\(dashboard\)/settings/proxmox/client-page.tsx):
    - `NodeFormState` + `emptyForm`: add `ca_cert_pem: ""`.
    - `openEdit`: leave `ca_cert_pem: ""` (write-only); display from
      `has_ca_cert`/fingerprint.
    - Dialog body (after the verify_ssl block): conditional CA paste
      textarea + "Clear CA" control + current-CA status line.
    - `handleSave`: include `ca_cert_pem` in POST when non-empty; in PUT,
      send `""` only on explicit clear, otherwise the pasted value or
      omit.
    - Optional: surface CA status in the nodes DataTable.

### Tests (none exist for proxmox today)
14. New `backend/tests/test_proxmox_client.py` — matrix-driven `verify=`
    assertions.
15. New `backend/tests/test_proxmox_nodes_api.py` —
    create/update/clear/response-shape/validation.
Follow the conftest savepoint-session + dependency-override pattern
(CLAUDE.md); mirror `test_action_host.py` for client mocking.

## Testing plan

**Client matrix** — patch `app.proxmox.client.httpx.AsyncClient`, assert
the `verify=` kwarg per row:
- `verify_ssl=False`, no CA → `verify is False`.
- `verify_ssl=False`, CA set → `verify is False` (CA ignored).
- `verify_ssl=True`, CA NULL → `verify is True`.
- `verify_ssl=True`, CA set → `isinstance(verify, ssl.SSLContext)`; assert
  the uploaded CA is trusted (compare DER from
  `ctx.get_ca_certs(binary_form=True)` to a fixture-built test CA).
- Context cached: two `_request` calls build it once.
- Malformed stored PEM (bypassing validator) → `_get_ssl_context` raises
  `ProxmoxError`, not raw `ssl.SSLError`.

**PEM validation** — valid CA accepted (stored stripped); garbage /
private key / non-CA leaf → `ValidationError` → 422; oversized → rejected;
whitespace-only → clear on update / `None` on create.

**API** — POST with CA → `has_ca_cert=True`, correct fingerprint; POST
without → `False`/`None`; PUT omit → unchanged; PUT new → replaced; PUT
`""` → cleared; GET never returns raw PEM; `/test` builds a client
carrying the PEM.

**Backward compat** — a pre-migration row (CA `NULL`) behaves exactly as
today.

## Edge cases & risks

- **BUG-45 interaction**: `verify_ssl=False` still fully bypasses
  verification regardless of CA — escape hatch preserved; no change for
  existing `verify_ssl=False` nodes.
- **Key rotation**: `test_rotate_encryption_key.py` rotates the AES key
  protecting `encrypted_token_secret`; plaintext `ca_cert_pem` is
  untouched — confirmed unaffected (optionally assert it survives
  rotation).
- **Migration / compat**: nullable, no default; existing rows `NULL` →
  current behavior; downgrade drops cleanly.
- **Multi-request lifecycle**: client builds a new `AsyncClient` per
  request; caching the SSLContext on the instance reuses one parsed CA
  with no temp files.
- **Hostname/SAN mismatch**: `create_default_context` enables hostname
  checking. If the Proxmox cert CN/SAN doesn't match the `api_url` host,
  verification fails even with the right CA — correct/secure but a likely
  support question. Document that the cert needs a SAN matching the host.
- **CA:TRUE enforcement**: reusing `pem_utils` rejects non-CA certs (see
  open question).
- **Cert expiry**: an expired uploaded CA fails at request time as
  `ProxmoxError`; could warn in the UI via `ca_cert_not_after`.

## Out of scope

- Client-certificate / mTLS auth to Proxmox (we verify only the *server*
  cert).
- Per-node certificate/public-key pinning beyond CA-based trust.
- UI certificate inspection/diff/expiry dashboards (beyond
  fingerprint + optional expiry).
- File-upload widget for the PEM (paste textarea only in v1).
- Changing or deprecating the `verify_ssl=False` BUG-45 behavior.
- Tightening proxmox endpoint auth to superuser.
- Reworking `ProxmoxClient` to reuse a single `AsyncClient` across
  requests.

## Open questions for the maintainer

- **CA:TRUE requirement**: require a real CA cert (reuse
  `pem_utils.validate_pem_content`, which rejects `CA:FALSE`/leaf certs),
  or accept any parseable X.509 cert so self-signed *node* certs work
  directly? *(Recommendation: require CA; document that self-signed node
  certs should set `CA:TRUE` or upload the cluster CA.)*
- **Response exposure**: metadata-only (`has_ca_cert` + fingerprint
  [+ subject/expiry]) vs returning the full PEM. *(Recommendation:
  metadata-only.)*
- **Auth**: keep `current_active_user`, or move all proxmox write paths
  to superuser as part of this work? *(Recommendation: keep; treat
  superuser as separate.)*
- **Audit**: record CA presence/fingerprint in the `proxmox_node` audit
  `after_state`? *(Recommendation: yes — presence + fingerprint, never
  the PEM body.)*
