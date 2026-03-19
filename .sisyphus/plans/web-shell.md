# Web Shell — Browser-Based SSH Terminal

## TL;DR

> **Quick Summary**: Add an interactive SSH terminal to Barricade's UI. Users click a host and get a live shell session via xterm.js (frontend) ↔ WebSocket (backend) ↔ asyncssh PTY (remote host). Sessions use stored SSH keys, are tracked in a session registry with per-user/global limits, and log start/end metadata to the audit log.
>
> **Deliverables**:
> - `ssh_user` column on Host model (Alembic migration)
> - WebSocket JWT auth dependency (`get_ws_user`)
> - In-memory session registry with per-user (5) and global (50) limits
> - WebSocket endpoint `/api/ssh-terminal/ws/{host_id}` bridging xterm ↔ asyncssh PTY
> - SSH session audit logging (start/end with duration)
> - Config settings for limits and idle timeout
> - xterm.js terminal component with custom WebSocket hook
> - Drawer on host detail page + full-page `/hosts/{id}/terminal` route
> - Backend + frontend test suites
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: T1 → T2 → T5 → T8 → T10

---

## Context

### Architecture

```
Browser                    Backend                     Remote Host
┌─────────┐  WebSocket   ┌──────────────┐  asyncssh  ┌──────────┐
│ xterm.js │◄────────────►│ /ws/shell/   │◄──────────►│ PTY/bash │
│          │  binary:data  │ {host_id}    │  SSH+PTY   │          │
│          │  text:control │              │            │          │
└─────────┘               └──────────────┘            └──────────┘
```

**Protocol**:
- Binary WebSocket frames = raw terminal data (stdin→host, stdout→browser)
- Text WebSocket frames = JSON control messages:
  - `{"type": "resize", "cols": 120, "rows": 40}`
  - `{"type": "ping"}` → `{"type": "pong"}`

**Auth**: JWT cookie (`barricade_auth`) extracted and verified on WebSocket upgrade. Custom `get_ws_user()` dependency — `fastapi-users` has no WebSocket support.

### Metis Review — Key Directives
- Host model missing `ssh_user` column — add with default `"root"` (Alembic migration)
- `@xterm/addon-attach` INCOMPATIBLE with mixed binary/text protocol — write custom handler
- Session registry with per-user (5) and global (50) limits
- WebSocket `on_disconnect` → close SSH → remove from registry → audit log end
- Idle timeout: 30min no user input → server closes WebSocket with code 4408
- No shadcn `sheet`/drawer component exists — add it
- Audit `log_action()` has zero callers in codebase — this is the first real usage
- Mock `asyncssh.connect()` in all tests — no real SSH hosts needed

---

## Work Objectives

### Definition of Done
- [x] `ssh_user` column on Host model with default "root"
- [x] WebSocket at `/api/ssh-terminal/ws/{host_id}` with JWT cookie auth
- [x] Interactive PTY session bridged to browser via xterm.js
- [x] Terminal resize support (client → server → SSH channel)
- [x] Session registry: per-user limit (5), global limit (50)
- [x] Idle timeout (30min) auto-disconnects
- [x] Audit: session_start and session_end logged with metadata
- [x] Host detail page: "Terminal" button → drawer with embedded terminal
- [x] Full-page route: `/hosts/{id}/terminal`
- [x] Backend tests (auth, registry, endpoint) + Playwright E2E

### Must Have
- `ssh_user` column on Host (default "root", max 32 chars)
- Custom `get_ws_user(websocket)` → User (manual JWT decode from cookie)
- `SessionRegistry` class: track active sessions, enforce limits, cleanup on disconnect
- Config: `SSH_MAX_SESSIONS_PER_USER=5`, `SSH_MAX_TOTAL_SESSIONS=50`, `SSH_IDLE_TIMEOUT_SECONDS=1800`
- asyncssh PTY: `conn.create_process(term_type='xterm-256color', term_size=(cols, rows))`
- Binary/text WebSocket protocol (no `@xterm/addon-attach`)
- Graceful cleanup on disconnect: close SSH process → close connection → deregister → audit
- WebSocket close codes: `4401` (auth failed), `4404` (host not found), `4400` (host no SSH key), `4429` (session limit), `4408` (idle timeout)
- Audit entries: `entity_type="ssh_session"`, `action="session_start"|"session_end"`, `after_state` includes `host_id`, `duration_seconds`
- Frontend: xterm.js + xterm-addon-fit, custom `useTerminalWebSocket` hook, `SshTerminal` component
- shadcn `sheet` component (drawer) for embedded terminal
- `data-testid="ssh-terminal"` on terminal container

### Must NOT Have (Guardrails)
- No terminal content recording (stdin/stdout not logged)
- No `@xterm/addon-attach` (incompatible with mixed protocol)
- No session reconnection/resume (dropped = closed)
- No file upload/download or SFTP
- No SSH agent forwarding or port forwarding
- No jump host / bastion chaining
- No terminal themes/customization
- No multi-tab terminal UI
- No collaborative/shared sessions
- No command filtering/blocking
- No persistent sessions across page navigation

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

- **Automated tests**: YES (tests after)
- **Backend**: pytest with mocked asyncssh
- **Frontend**: Playwright E2E

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 3 parallel):
├── T1: ssh_user column on Host + Alembic migration [quick]
├── T2: WebSocket JWT auth dependency (get_ws_user) [unspecified-high]
└── T3: Session registry + config settings [unspecified-high]

Wave 2 (Backend core — 2 parallel):
├── T4: Ansible-independent SSH connection helper [quick]
└── T5: WebSocket SSH terminal endpoint [deep]

Wave 3 (Backend complete + Frontend start — 3 parallel):
├── T6: Audit logging for SSH sessions [quick]
├── T7: Backend test suite [unspecified-high]
└── T8: Frontend xterm.js component + WebSocket hook [visual-engineering]

Wave 4 (Frontend integration — 2 parallel):
├── T9: Host detail drawer + full-page terminal route [visual-engineering]
└── T10: Playwright E2E tests [unspecified-high]

Wave FINAL (Review — 4 parallel):
├── F1: Plan compliance audit [oracle]
├── F2: Code quality review [unspecified-high]
├── F3: Real manual QA [unspecified-high]
└── F4: Scope fidelity check [deep]

Critical Path: T1 → T2 → T5 → T8 → T9 → F1-F4
Max Concurrent: 3
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | — | T5 | 1 |
| T2 | — | T5 | 1 |
| T3 | — | T5 | 1 |
| T4 | — | T5 | 2 |
| T5 | T1, T2, T3, T4 | T6, T7, T8 | 2 |
| T6 | T5 | T7 | 3 |
| T7 | T5, T6 | F1-F4 | 3 |
| T8 | T5 | T9, T10 | 3 |
| T9 | T8 | T10, F1-F4 | 4 |
| T10 | T8, T9 | F1-F4 | 4 |

---

## TODOs

- [x] 1. Add `ssh_user` Column to Host Model

  **What to do**:
  - Add `ssh_user` column to `Host` model: `String(32)`, default `"root"`, NOT NULL
  - Update `HostCreate`/`HostUpdate` schemas to include `ssh_user` (optional, default "root")
  - Update `HostResponse` schema
  - Update host create/edit forms in frontend to include SSH User field
  - Create Alembic migration: `ALTER TABLE hosts ADD COLUMN ssh_user VARCHAR(32) NOT NULL DEFAULT 'root'`
  - Update `sync/collector.py` to use `host.ssh_user` instead of hardcoded `"root"`
  - Update `sync/diff.py:fetch_current_state()` to pass `ssh_user`

  **Must NOT do**: Do NOT change collector function signatures in a breaking way — add `ssh_user` parameter with default `"root"`

  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 1 | Blocks: T5 | Blocked By: None

  **References**:
  - `backend/app/models/host.py` — Host model
  - `backend/app/schemas/hosts.py` — Host schemas
  - `backend/app/sync/collector.py:28` — hardcoded `ssh_user="root"`

  **Acceptance Criteria**:
  - [ ] Column exists with default "root"
  - [ ] Existing hosts unaffected (migration backfills "root")
  - [ ] Collector uses `host.ssh_user` from DB
  - [ ] Frontend host form has SSH User field

  **Commit**: YES — `feat: add ssh_user column to Host model`

- [x] 2. WebSocket JWT Auth Dependency

  **What to do**:
  - Create `backend/app/auth/ws_auth.py`:
    - `async def get_ws_user(websocket: WebSocket, db: AsyncSession) -> User`:
      1. Extract cookie: `token = websocket.cookies.get("barricade_auth")`
      2. If no cookie → `await websocket.close(code=4401, reason="Not authenticated")` → raise
      3. Decode JWT using same `SECRET_KEY` and algorithm as `JWTStrategy` in `auth/users.py`
      4. Extract `user_id` from `sub` claim
      5. Load user from DB: `select(User).where(User.id == user_id, User.is_active == True)`
      6. If user not found or inactive → close with 4401
      7. Return User object
    - Use `python-jose` or `jwt` library (same as fastapi-users uses internally)

  **Must NOT do**: Do NOT modify `auth/users.py` — this is a parallel auth path for WebSocket only

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 1 | Blocks: T5 | Blocked By: None

  **References**:
  - `backend/app/auth/users.py` — JWT strategy config (`SECRET_KEY`, cookie transport)
  - `backend/app/config.py` — `SECRET_KEY` setting

  **Acceptance Criteria**:
  - [ ] Valid cookie → returns User object
  - [ ] Missing cookie → WebSocket closed with 4401
  - [ ] Expired/invalid JWT → WebSocket closed with 4401
  - [ ] Inactive user → WebSocket closed with 4401

  **Commit**: YES — `feat: add WebSocket JWT auth dependency`

- [x] 3. Session Registry + Config Settings

  **What to do**:
  - Add to `backend/app/config.py`:
    - `SSH_MAX_SESSIONS_PER_USER: int = 5`
    - `SSH_MAX_TOTAL_SESSIONS: int = 50`
    - `SSH_IDLE_TIMEOUT_SECONDS: int = 1800`
  - Create `backend/app/ssh_terminal/session_registry.py`:
    - `SessionRegistry` class (singleton):
      - `_sessions: dict[str, SessionInfo]` — session_id → metadata
      - `SessionInfo` dataclass: `session_id`, `user_id`, `host_id`, `started_at`, `last_activity`, `websocket`, `ssh_process`
      - `register(session_id, user_id, host_id, websocket, ssh_process) -> bool` — returns False if limit exceeded
      - `deregister(session_id)` — remove and cleanup
      - `touch(session_id)` — update last_activity timestamp
      - `get_user_session_count(user_id) -> int`
      - `get_total_session_count() -> int`
      - `get_idle_sessions(timeout_seconds) -> list[str]` — sessions exceeding idle threshold
      - `cleanup_session(session_id)` — close SSH + WebSocket + deregister
    - Thread-safe via asyncio.Lock

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 1 | Blocks: T5 | Blocked By: None

  **Acceptance Criteria**:
  - [ ] Per-user limit enforced (6th session rejected)
  - [ ] Global limit enforced
  - [ ] `touch()` updates timestamp
  - [ ] `get_idle_sessions()` returns sessions past threshold
  - [ ] `cleanup_session()` closes SSH and WebSocket

  **Commit**: YES — `feat: add SSH session registry with limits`

- [x] 4. SSH Connection Helper

  **What to do**:
  - Create `backend/app/ssh_terminal/ssh_connect.py`:
    - `async def open_ssh_shell(host_id: int, db: AsyncSession, initial_cols: int = 80, initial_rows: int = 24) -> tuple[SSHClientConnection, SSHClientProcess]`:
      1. Load host from DB (ip_address, ssh_port, ssh_user, ssh_key_id)
      2. Validate: host exists, has ssh_key_id
      3. Load + decrypt SSH key (reuse `crypto.decrypt_ssh_key` + `crypto.get_master_key`)
      4. `asyncssh.connect(host.ip_address, port=host.ssh_port, username=host.ssh_user, client_keys=[key], known_hosts=None)`
      5. `conn.create_process(term_type='xterm-256color', term_size=(initial_cols, initial_rows))`
      6. Return `(conn, process)`
    - Raise specific exceptions: `HostNotFoundError`, `NoSSHKeyError`, `SSHConnectionError`

  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 2 | Blocks: T5 | Blocked By: None

  **References**:
  - `backend/app/sync/diff.py:53-95` — credential resolution pattern (Host → SSHKey → decrypt)
  - `backend/app/sync/collector.py` — asyncssh.connect pattern

  **Acceptance Criteria**:
  - [ ] Returns connected SSH process with PTY
  - [ ] Raises clear error for missing host / missing key / connection failure
  - [ ] Uses `host.ssh_user` from DB (not hardcoded root)

  **Commit**: YES — `feat: add SSH shell connection helper`

- [x] 5. WebSocket SSH Terminal Endpoint

  **What to do**:
  - Create `backend/app/ssh_terminal/__init__.py` (empty)
  - Create `backend/app/api/ssh_terminal.py`:
    - `router = APIRouter(prefix="/api/ssh-terminal", tags=["ssh-terminal"])`
    - `@router.websocket("/ws/{host_id}")`:
      1. `await websocket.accept()`
      2. Authenticate: `user = await get_ws_user(websocket, db)`
      3. Check session limits via registry
      4. Open SSH shell: `conn, process = await open_ssh_shell(host_id, db)`
      5. Register session in registry
      6. Log audit: `session_start`
      7. Create two async tasks:
         - **reader**: `async for data in process.stdout` → `await websocket.send_bytes(data)`
         - **writer**: loop `message = await websocket.receive()`:
           - If binary: `process.stdin.write(message)` + `registry.touch(session_id)`
           - If text: parse JSON control message (resize → `process.change_terminal_size(cols, rows)`)
      8. On disconnect/error: cancel tasks → cleanup session → audit `session_end`
    - Error handling:
      - Host not found → close 4404
      - No SSH key → close 4400
      - Session limit → close 4429
      - SSH connection failed → close 4502
      - Idle timeout → close 4408
    - Idle timeout: background task checks `registry.get_idle_sessions()` periodically
  - Register router in `app/main.py`

  **Must NOT do**: Do NOT use `@xterm/addon-attach` on the frontend (this is a backend task, but document the protocol contract)

  **Recommended Agent Profile**: `deep`
  **Parallelization**: Wave 2 | Blocks: T6, T7, T8 | Blocked By: T1, T2, T3, T4

  **References**:
  - `backend/app/api/sync.py` — Router pattern
  - `backend/app/main.py` — Router registration

  **Acceptance Criteria**:
  - [ ] WebSocket upgrade succeeds with valid cookie
  - [ ] Binary frames forwarded to/from SSH
  - [ ] Resize JSON message changes terminal size
  - [ ] Session registered and deregistered
  - [ ] All error close codes used correctly

  **Commit**: YES — `feat: add WebSocket SSH terminal endpoint`

- [x] 6. SSH Session Audit Logging

  **What to do**:
  - Wire `log_action()` calls into the WebSocket endpoint:
    - On session start: `log_action(db, action="session_start", entity_type="ssh_session", entity_id=host_id, user_id=user.id, after_state={"host_id": host_id, "hostname": host.hostname, "ssh_user": host.ssh_user})`
    - On session end: `log_action(db, action="session_end", entity_type="ssh_session", entity_id=host_id, user_id=user.id, after_state={"host_id": host_id, "duration_seconds": duration, "disconnect_reason": reason})`
  - Note: This is the FIRST real usage of `log_action()` — verify it works with the actual DB

  **Recommended Agent Profile**: `quick`
  **Parallelization**: Wave 3 | Blocks: T7 | Blocked By: T5

  **References**:
  - `backend/app/audit/logger.py` — `log_action()` function

  **Acceptance Criteria**:
  - [ ] `session_start` entry created with host metadata
  - [ ] `session_end` entry created with duration_seconds
  - [ ] Entries visible in `GET /api/audit-log?entity_type=ssh_session`

  **Commit**: YES — `feat: add SSH session audit logging`

- [x] 7. Backend Test Suite

  **What to do**:
  - Create `backend/tests/test_ssh_terminal.py`:
    - **TestWSAuth**: valid cookie → user returned, missing cookie → 4401, expired JWT → 4401, inactive user → 4401
    - **TestSessionRegistry**: register/deregister, per-user limit (6th rejected), global limit, touch updates timestamp, get_idle_sessions works, cleanup closes resources
    - **TestSSHTerminalEndpoint** (mock asyncssh):
      - Valid connection → 101 upgrade
      - Host not found → 4404
      - Host no SSH key → 4400
      - Session limit exceeded → 4429
      - Binary frame forwarded to mock SSH stdin
      - Resize control message processed
    - **TestAuditLogging**: session_start + session_end entries created with correct metadata

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 3 | Blocks: F1-F4 | Blocked By: T5, T6

  **References**:
  - `backend/tests/conftest.py` — Test fixtures

  **Acceptance Criteria**:
  - [ ] 15+ tests, all passing
  - [ ] asyncssh fully mocked (no real SSH)
  - [ ] All WebSocket close codes tested
  - [ ] Session limits tested

  **Commit**: YES — `test: add SSH terminal backend tests`

- [x] 8. Frontend xterm.js Component + WebSocket Hook

  **What to do**:
  - Install: `npm install @xterm/xterm @xterm/addon-fit @xterm/addon-web-links`
  - Create `frontend/lib/hooks/use-terminal-websocket.ts`:
    - Custom hook: manages WebSocket lifecycle, binary/text message routing
    - Accepts: `hostId`, `onData` callback, `onDisconnect` callback
    - Sends: `sendData(bytes)`, `sendResize(cols, rows)`, `close()`
    - Auto-reconnect: NO (session = one connection)
    - WebSocket URL: `ws://localhost:8000/api/ssh-terminal/ws/{hostId}` (construct from `NEXT_PUBLIC_API_URL`)
  - Create `frontend/components/ssh-terminal.tsx`:
    - Uses xterm.js: `new Terminal({ cursorBlink: true, fontSize: 14, theme: { background: '#1a1b26' } })`
    - Addons: `FitAddon` (auto-resize), `WebLinksAddon` (clickable URLs)
    - On terminal data → `sendData()` (binary)
    - On WebSocket data → `terminal.write(data)`
    - ResizeObserver on container → `fitAddon.fit()` → `sendResize(cols, rows)`
    - `data-testid="ssh-terminal"` on container div
    - Loading state: "Connecting to {hostname}..."
    - Error state: display close reason
    - Disconnected state: "Session ended. [Reconnect] button"
  - Add shadcn `sheet` component: `npx shadcn@latest add sheet`

  **Must NOT do**:
  - Do NOT use `@xterm/addon-attach` (incompatible with mixed binary/text protocol)
  - Do NOT add terminal themes/customization

  **Recommended Agent Profile**: `visual-engineering` + `frontend-ui-ux`
  **Parallelization**: Wave 3 | Blocks: T9, T10 | Blocked By: T5

  **References**:
  - `frontend/lib/api.ts` — API URL pattern
  - `frontend/components/ui/` — shadcn component patterns

  **Acceptance Criteria**:
  - [ ] xterm.js renders in container with `data-testid="ssh-terminal"`
  - [ ] Binary data flows both directions
  - [ ] Resize messages sent on container resize
  - [ ] Loading/error/disconnected states shown
  - [ ] `npm run build` passes

  **Commit**: YES — `feat(ui): add xterm.js terminal component and WebSocket hook`

- [x] 9. Host Detail Drawer + Full-Page Terminal Route

  **What to do**:
  - Modify `frontend/app/(dashboard)/hosts/[id]/page.tsx`:
    - Add "Terminal" button (icon: terminal/console)
    - Button opens shadcn `Sheet` (drawer) from bottom, height ~50vh, resizable
    - Drawer contains `<SshTerminal hostId={id} hostname={host.hostname} />`
    - Drawer header: hostname + "Open in Full Page" link + close button
    - Button disabled if host has no SSH key (tooltip: "No SSH key assigned")
  - Create `frontend/app/(dashboard)/hosts/[id]/terminal/page.tsx`:
    - Full-viewport terminal (no sidebar, minimal header with hostname + close button)
    - `<SshTerminal hostId={id} hostname={host.hostname} />`
    - Back link to `/hosts/{id}`

  **Recommended Agent Profile**: `visual-engineering` + `frontend-ui-ux`
  **Parallelization**: Wave 4 | Blocks: T10, F1-F4 | Blocked By: T8

  **References**:
  - `frontend/app/(dashboard)/hosts/[id]/page.tsx` — Host detail page

  **Acceptance Criteria**:
  - [ ] "Terminal" button visible on host detail
  - [ ] Drawer opens with embedded terminal
  - [ ] "Open in Full Page" navigates to `/hosts/{id}/terminal`
  - [ ] Full-page terminal fills viewport
  - [ ] Button disabled when no SSH key

  **Commit**: YES — `feat(ui): add terminal drawer and full-page route`

- [x] 10. Playwright E2E Tests

  **What to do**:
  - Create `frontend/e2e/ssh-terminal.spec.ts`:
    - Test "Terminal" button renders on host detail page
    - Test button disabled when host has no SSH key
    - Test drawer opens on button click (assert `[data-testid="ssh-terminal"]` visible)
    - Test "Open in Full Page" link navigates to `/hosts/{id}/terminal`
    - Test full-page terminal renders xterm container
    - Note: actual SSH connection won't work without running backend — test UI rendering and WebSocket attempt

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: Wave 4 | Blocks: F1-F4 | Blocked By: T8, T9

  **References**:
  - `frontend/e2e/hosts.spec.ts` — Host E2E test pattern

  **Acceptance Criteria**:
  - [ ] Terminal button visibility tested
  - [ ] Drawer open/close tested
  - [ ] Full-page route renders
  - [ ] `data-testid="ssh-terminal"` asserted

  **Commit**: YES — `test: add SSH terminal E2E tests`

---

## Final Verification Wave

- [x] F1. **Plan Compliance Audit** — `oracle`
  Verify: all Must Have present, all close codes implemented, session limits enforced, audit entries created, ssh_user used.

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run pytest + ruff + `npm run build`. Check: no terminal content logging, no `@xterm/addon-attach`, asyncssh mocked in tests, no hardcoded "root".

- [x] F3. **Real Manual QA** — `unspecified-high` (+ `playwright`)
  Login → navigate to host → click Terminal → assert drawer opens with xterm → verify full-page route.

- [x] F4. **Scope Fidelity Check** — `deep`
  Verify: no session recording, no SFTP, no port forwarding, no themes, no multi-tab, no reconnection.

---

## Commit Strategy

| Task | Message | Key Files |
|------|---------|-----------|
| T1 | `feat: add ssh_user column to Host model` | `models/host.py`, `schemas/hosts.py`, `alembic/` |
| T2 | `feat: add WebSocket JWT auth dependency` | `app/auth/ws_auth.py` |
| T3 | `feat: add SSH session registry with limits` | `app/ssh_terminal/session_registry.py`, `app/config.py` |
| T4 | `feat: add SSH shell connection helper` | `app/ssh_terminal/ssh_connect.py` |
| T5 | `feat: add WebSocket SSH terminal endpoint` | `app/api/ssh_terminal.py`, `app/main.py` |
| T6 | `feat: add SSH session audit logging` | update `app/api/ssh_terminal.py` |
| T7 | `test: add SSH terminal backend tests` | `tests/test_ssh_terminal.py` |
| T8 | `feat(ui): add xterm.js terminal component` | `components/ssh-terminal.tsx`, `lib/hooks/` |
| T9 | `feat(ui): add terminal drawer and full-page route` | `hosts/[id]/page.tsx`, `hosts/[id]/terminal/` |
| T10 | `test: add SSH terminal E2E tests` | `e2e/ssh-terminal.spec.ts` |

---

## Success Criteria

```bash
cd backend && pytest tests/test_ssh_terminal.py -v
cd frontend && npm run build
cd backend && alembic upgrade head && alembic downgrade -1
```

### Final Checklist
- [x] All Must Have present
- [x] All Must NOT Have absent
- [x] All close codes implemented (4401, 4404, 4400, 4429, 4408, 4502)
- [x] Session limits enforced
- [x] Audit entries created (session_start + session_end)
- [x] `ssh_user` used from DB (no hardcoded "root")
- [x] No terminal content recorded anywhere
