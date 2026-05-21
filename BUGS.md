# Bug Registry

Open bugs in LabDog. New entries are added as bugs are surfaced.

## Convention: open-only

**Only open bugs belong in this file.** When a bug is fixed:

1. Land the fix and write a descriptive commit message that references
   the bug ID (e.g. `fix(sync): BUG-37 — dispatch Celery tasks after
   commit`). That commit message is the canonical record (symptom,
   root cause, fix).
2. Delete the entry from this file in the same commit. Do **not** mark
   bugs `[x]` and leave them here — fixed entries belong in git history,
   not in the registry.

To retrace a historical bug ID referenced elsewhere
(`BUG-NN`, `SEC-NN`, `TYPE-NN`, `DEAD-NN`), search the commit log:

```
git log --grep BUG-37
git log -- backend/app/api/sync.py
```

## How to file an entry

Format each entry as:

    - [ ] **BUG-NN** `path/to/file.ext:LINE` — one-line summary

      Symptom, root cause, severity tier (Critical / High / Medium /
      Low). If reproduced from a specific scenario, note it. Group
      related bugs under the same severity heading.

ID counter as of last housekeeping pass: `BUG-51`, `SEC-19`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Security findings — High

Filed 2026-05-21 from a `security-auditor` whitebox source-level
review (commit `1108d67` as the audit baseline). Each entry was
spot-checked against current HEAD before filing.

- [ ] **SEC-08** `backend/app/api/ssh_terminal.py:26` + `backend/app/api/host_state.py:77` + `backend/app/api/actions.py:108` — any active (non-superuser) user gets root SSH on every managed host

  Symptom: WebSocket SSH terminal, `POST /api/actions/runs`, `POST /api/hosts/{id}/collect-state`, and several other host-mutating endpoints gate on `current_active_user` rather than `current_superuser`. The auth helper `get_ws_user` at `backend/app/auth/ws_auth.py:14` only checks `is_active`; no per-host RBAC exists. A superuser creating any second user account hands that user effective root on every host in the fleet via the SSH terminal alone. Severity: **High** — privilege boundary is doc-only (see `docs/security-hardening.md:239-242` which claims "assigned scope"; no code implements it). Fix: either gate all host-mutating endpoints on `current_superuser` until a `user_host_group` join table + enforcement exists, or implement the documented scope model.

- [ ] **SEC-09** `backend/app/api/ssh_terminal.py:74-87,176-191` — SSH terminal does not audit executed commands

  Symptom: only `session_start` / `session_end` audit rows are written. Bytes flowing from the user (`ws_to_ssh` at line 110-131) are forwarded directly to `process.stdin` with no transcript capture. The only audit trace of `rm -rf /` is the session-open and session-close pair. Pairs with SEC-08: even after SEC-08 lands and only superusers can open terminals, there's still no record of what they ran. Severity: **High**. Fix: buffer outgoing bytes per session, flush periodically into a new `ssh_session_transcript` table on newline boundaries; cap per-session transcript size and apply retention.

- [ ] **SEC-10** `backend/app/schemas/scans.py:17-23` + `backend/app/tasks/scan_run.py:134` — scheduled ScanConfigs bypass the loopback / metadata blocklist enforced on ad-hoc scans

  Symptom: `_validate_cidr` only calls `ipaddress.ip_network(...)`; it does not check `BLOCKED_NETWORKS` (`127.0.0.0/8`, `169.254.0.0/16`, multicast, reserved) which the ad-hoc `POST /api/discovery/scan` enforces via `app/discovery/scanner.py:61-88`. The Celery executor `scan_run._async_run` calls `scan_network(cidr, ...)` directly with no re-validation. Combined with `create_scan_config` being `current_active_user` (not superuser), any active non-superuser user can scan loopback, cloud instance-metadata IPs, internal services. The `verify_ssh` error string (`PendingHostResponse.ssh_error`) leaks the remote service banner. Severity: **High**. Fix: move the `BLOCKED_NETWORKS` check into a shared helper, call from both schema validator and Celery task. Gate `POST /api/scans*` on `current_superuser`. Coarsen `ssh_error` to `unreachable|auth_failed|refused`.

- [ ] **SEC-12** `backend/app/packs/service.py:65` + `backend/app/packs/schemas.py:34` — action-pack `path` field allows directory traversal

  Symptom: `effective_path_for(pack)` builds the on-disk location as `checkout / pack.path.strip("/")`. The strip only removes leading/trailing slashes; `..` segments are not rejected. `ActionPackCreate.path` is a raw `str` with no field validator. A superuser POSTing `{"path": "../../../etc/labdog"}` causes the loader to walk arbitrary filesystem locations for `actions/*/manifest.yml` and execute any `playbook.yml` it finds under the labdog UID. Severity: **High** (superuser-only, but it escapes the documented `packs_root_dir` boundary which is part of the security model in `docs/security-hardening.md`). Fix: reject `..`, leading `/`, NUL bytes, backslashes in `path` at the schema layer. At runtime, resolve the combined path and assert `.is_relative_to(checkout.resolve())`.

- [ ] **SEC-13** `backend/app/packs/schemas.py:35` + `backend/app/packs/service.py:62` — local-source action-pack `local_path` has no containment

  Symptom: same vector as SEC-12 but the operator sets the absolute path directly. `{"source_type":"local", "local_path":"/root/.ssh"}` makes labdog walk that location for action manifests. Severity: **High** (superuser-only). Fix: constrain `local_path` to an admin-configured allow-list (e.g. `settings.ansible.local_packs_roots: list[str]`). Reject paths under `/proc`, `/sys`, `/dev`, `/etc`.

### Security findings — Medium

- [ ] **SEC-14** `backend/app/api/ssh_keys.py` (entire file) + `backend/app/api/git_repos.py` (entire file) — SSH-key and Git-repo CRUD emit zero audit-log rows

  Symptom: `app.audit.logger.log_action` is never imported or invoked in either router. Creating, updating, or deleting an SSH private key — the most sensitive resource type in the system — produces no audit row. Same for git repos which carry encrypted HTTPS PATs. Action-packs (`action_packs.py:175,238,278`) and proxmox nodes (`proxmox_nodes.py:55,123,148`) DO log; this is an inconsistency. Severity: **Medium**. Fix: add `await log_action(db, action="create"|"update"|"delete", entity_type="ssh_key"|"git_repo", entity_id=..., user_id=user.id, after_state={name, ssh_user, is_default, ...})` in each handler. Never include the private key or PAT in the payload.

- [ ] **SEC-15** `backend/app/sync/post_run.py:106-197` — `dispatch_post_run_register` bypasses the audit trail

  Symptom: when an action manifest declares `post_run_register`, the helper inserts rows into seven desired-state tables via `db.add(row)` but never emits audit rows. The equivalent REST endpoints (`api/linux_users.py:181-210`, etc.) DO emit audit. A malicious git-pack contributor can declare `post_run_register: {linux-users: [{username: "x", authorized_keys: [...]}]}`; after a superuser pins and runs the action, every targeted host gets a backdoor SSH key with no audit trace beyond the action run. Severity: **Medium** (requires malicious pack + superuser action-run). Fix: emit `log_action(action="post_run_register", entity_type=<module>, entity_id=row.id, user_id=triggered_by_user_id, after_state=validated)` per insert. Consider denying `authorized_keys` from `post_run_register` and forcing operator confirmation for SSH-grant operations.

- [ ] **SEC-16** `backend/app/ssh_utils.py:68` + every asyncssh call site — no SSH host-key verification

  Symptom: `ssh_connect()` defaults to `asyncssh.connect(known_hosts=None)` and none of the call sites override (host_state.py:114,461,501,649; hosts.py:127; discovery/verify.py:62; tasks/facts.py:115; workflows/steps/{reboot,preflight,verify,rollback}.py). Every SSH connection labdog opens accepts whatever key the remote peer presents. The Ansible inventory generator sets `StrictHostKeyChecking=accept-new` (TOFU), but the asyncssh paths have no verification at all. On any LAN where labdog's traffic can be redirected (ARP spoofing, rogue DHCP), an attacker can MITM and feed bogus state into the diff engine. Severity: **Medium** (LAN-adjacent attacker required). Fix: persist host keys on first successful connect (labdog-owned file), pass `known_hosts=<path>` to `asyncssh.connect`, gate subsequent connects on key match. Brand-new hosts behind a per-host "accept new key" UI toggle.

- [ ] **SEC-17** `backend/app/auth/users.py:55-71` — first-user-becomes-superuser promotion has a race window

  Symptom: `on_after_register` opens a fresh `AsyncSessionLocal()` and runs after `user_manager.create()` returns. The advisory lock at `8675309` correctly serialises concurrent registrations, but if the post-create callback throws between user-create and superuser-flip (DB hiccup), the first user persists without `is_superuser=True` and the registration endpoint then refuses further sign-ups — leaving the install with no superuser, recoverable only via manual SQL. Severity: **Medium** (rare, but recovery cost is high). Fix: subclass `UserManager.create()` so user-creation and superuser-promotion happen in the same transaction.

- [ ] **SEC-18** `backend/app/config.py:56` + `backend/app/main.py:279-285` — default `allowed_origins` includes `http://localhost:3000` in production

  Symptom: the config-file default for `allowed_origins` is `["http://localhost:3000"]`. No startup check rejects this when `cookie_secure=True` or `force_https=True`. Mostly mitigated by `SameSite=lax` (browser refuses to send a `Secure` cookie cross-scheme), but the CORS preflight still allows any localhost dev server to mount the API. Severity: **Medium** (defence-in-depth). Fix: in `_validate_required`, reject `localhost`/`127.0.0.1` in `allowed_origins` when `cookie_secure=True`. Document the override per environment.

- [ ] **SEC-19** (cross-cutting) — CSRF posture relies solely on `SameSite=lax` on the auth cookie

  Symptom: cookie-based JWT with no double-submit token, no Origin/Referer header validation on state-changing endpoints. `SameSite=lax` blocks most cross-origin POSTs but accepts forged top-level navigations (GET-based state changes are not the labdog model, so the practical blast radius is small) and accepts cross-origin requests from same-site iframes. Severity: **Medium** (defence-in-depth; SameSite-lax is a reasonable baseline but not sufficient as the sole defence for a tool that holds SSH keys). Fix: add a double-submit-cookie pattern (separate `csrf_token` cookie + matching `X-CSRF-Token` header required on POST/PUT/DELETE), or migrate from cookie-based JWT to `Authorization: Bearer` (better fit for the SPA).

### Hardening — Low

- [ ] **BUG-47** `backend/app/packs/schemas.py:34,35` — `pack.path` and `local_path` have no length cap or NUL-byte rejection

  Symptom: Pydantic `str` accepts arbitrary length and embedded NULs. Both fields flow into `Path(...)` operations and feed the SEC-12/SEC-13 vectors. Severity: **Low** (alone). Fix: `Field(max_length=512)` + `field_validator` rejecting `\x00` and backslashes on both fields.

- [ ] **BUG-48** `backend/app/schemas/hosts.py:8-14` — `Host.ip_address` accepts arbitrary string with no IP validation

  Symptom: `HostCreate.ip_address: str` accepts `"localhost"`, `"0.0.0.0"`, `"127.0.0.1"`, `"::1"`, or arbitrary garbage. Flows into `asyncssh.connect(host=...)` and into Ansible inventory's `ansible_host`. Superuser-only but a typo can enrol labdog into managing itself. Severity: **Low**. Fix: `field_validator` running `ipaddress.ip_address(v)` and rejecting loopback/unspecified ranges unless an explicit `allow_loopback=true` override is set.

- [ ] **BUG-49** `backend/app/schemas/ssh_keys.py:9` + `backend/app/schemas/hosts.py:12` — `ssh_user` lacks character allow-list

  Symptom: `ssh_user: str = "root"` with no regex. Safe at the asyncssh + Ansible boundaries (parameter-passed, not shell-interpolated), but arbitrary characters (including newlines / control chars) flow into audit log payloads and SSE channel messages, landing in the UI. `user_mgmt/schemas.py:14` already uses `^[a-z_][a-z0-9_-]{0,31}$` for the same shape; reuse it. Severity: **Low**. Fix: add the same field_validator.

- [ ] **BUG-50** `.dockerignore` (missing entry) — `dev/` not excluded

  Symptom: `.gitignore` excludes `dev/.env` from commits but `.dockerignore` doesn't exclude `dev/`. The current `Dockerfile` happens not to `COPY dev/`, but a future careless `COPY . .` would ship the dev secrets into the build context (and into layers if copied). Severity: **Low** (defence-in-depth). Fix: add `dev/` to `.dockerignore`.

- [ ] **BUG-51** `Dockerfile` (missing) — no HEALTHCHECK

  Symptom: container orchestrators can't autodetect a stuck process. Severity: **Low**. Fix: add `HEALTHCHECK CMD curl -fsS http://localhost:8000/api/version || exit 1`.

---

### Pre-existing (filed prior to the security review)

- [ ] **BUG-46** `LABDOG_PLAYBOOKS_REF` + `Dockerfile:39` / `packaging/Makefile:26` / `.github/workflows/ci.yml:157,245` / `dev/dev.sh:111` — bundled-pack build fails because pinned SHA doesn't exist on the default upstream

  Symptom: `docker build .` (and packaging/build.sh, and the github CI jobs that fetch the bundled pack) fails when the Stage 2b clone tries to `git checkout e6e73728…` against the default URL `https://github.com/open-labdog/labdog-playbooks.git`. The pinned SHA only exists on the maintainer's local gitlab mirror (`gitlab.lan.tyresson.se/dennis/labdog-playbooks.git`); the github public mirror is stale (old flat-layout playbooks, no alloy-install). Root cause: when the build-time-fetch migration landed (commit `8fb167d`), the github mirror had not yet been synced from gitlab. The labdog repo committed the gitlab SHA in `LABDOG_PLAYBOOKS_REF` because that's what the in-repo bundled mirror reflected; defaulting the URL to gitlab would couple public labdog to a private hostname. Severity: **High** — every build path is broken without an env override (`LABDOG_PLAYBOOKS_REPO` or `LABDOG_PLAYBOOKS_LOCAL`). Resolution: push gitlab `labdog-playbooks/main` to github `open-labdog/labdog-playbooks`, then bump `LABDOG_PLAYBOOKS_REF` to whatever SHA github lands on. See `TODO.md` "Mirror gitlab labdog-playbooks to github" for the full procedure.

- [ ] **BUG-45** `backend/app/proxmox/client.py:88` — Proxmox API requests fail with SSL certificate verification error

  Symptom: `httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1010)` raised from `ProxmoxClient._request` when the `discover_host_vm_mapping` endpoint calls `client.list_nodes()` → `GET /api2/json/nodes`. Seen live in the `labdog` container on `lin-manager` (2026-05-15). Root cause: httpx performs strict SSL verification by default; Proxmox nodes typically use self-signed or privately-issued TLS certificates whose CA is not in the system trust store. Severity: **High** — Proxmox VM discovery is completely broken for any host using a private CA or self-signed cert.
