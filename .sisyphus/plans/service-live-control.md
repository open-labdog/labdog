# Service Live Control — Inventory & Ad-Hoc Commands

## TL;DR

> **Quick Summary**: Add live service inventory (list all systemd services on a host via SSH) and ad-hoc start/stop/restart commands with audit logging and protected service warnings.
> 
> **Deliverables**:
> - Backend: SSH inventory function + command executor, 2 new API endpoints
> - Frontend: Inventory table with filter + action buttons with confirmation dialogs
> - Tests: Schema, SSH mock, API integration, frontend build
> 
> **Estimated Effort**: Small-Medium
> **Parallel Execution**: YES — 3 waves
> **Critical Path**: T1/T2 → T3 → T4/T5

---

## Context

### Original Request
Extend service management UI to display current services on a host and issue start/stop/restart commands.

### Interview Summary
- **Listing scope**: All systemd services (full inventory), with client-side filtering
- **Execution method**: Direct SSH (`systemctl`), not Ansible pipeline
- **Protected services**: Allow with confirmation warning (not hard-blocked like ServiceRules)
- **Audit**: All ad-hoc commands logged via `log_action()`

### Metis Review
**Identified Gaps** (addressed):
- SSH timeout needed: 30s via `asyncio.wait_for()` — incorporated
- Service name in request body, not URL path: avoids `@`, `.`, `:` encoding issues — incorporated
- Structured command response with `is_protected` flag — incorporated
- `shlex.quote()` for defense in depth on SSH commands — incorporated
- No auto-polling inventory: manual refresh only — incorporated
- Transitional service states (`activating`, `deactivating`) need UI handling — incorporated

---

## Work Objectives

### Core Objective
Enable operators to view all systemd services on managed hosts and issue ad-hoc start/stop/restart commands with safety guardrails.

### Concrete Deliverables
- `GET /api/hosts/{host_id}/services/inventory` — list all systemd services via SSH
- `POST /api/hosts/{host_id}/services/command` — execute start/stop/restart via SSH
- Host detail "Services" tab extended with inventory sub-section and action buttons

### Definition of Done
- [ ] Inventory endpoint returns 50+ services from a running host
- [ ] Ad-hoc command returns structured result with exit code
- [ ] Protected service shows warning, non-protected shows simple confirm
- [ ] All commands audit-logged
- [ ] Frontend build passes

### Must Have
- All ad-hoc commands use `current_active_user` auth (matches existing service CRUD)
- Service name validated with regex `^[a-zA-Z0-9_@:.-]+$` AND `shlex.quote()` in SSH command
- Action type validated as `Literal["start", "stop", "restart"]`
- SSH timeout of 30s via `asyncio.wait_for()`
- Host SSH key check before any SSH operation (return 400 if missing)
- Structured response: `{success, exit_code, stdout, stderr, is_protected}`
- `.service` suffix stripped from inventory results for consistency
- Audit log entry for every command: `entity_type="service_command"`, `action="service_start|service_stop|service_restart"`
- Manual refresh button for inventory — no auto-polling
- Client-side filter/search for inventory table
- Confirmation dialog for protected services (custom Dialog with warning text)
- Simple confirm for non-protected services
- Action buttons disabled during pending operation
- Loading state on inventory fetch

### Must NOT Have (Guardrails)
- No `enable`/`disable`/`mask`/`unmask`/`daemon-reload` — only `start`/`stop`/`restart`
- No `--force` flag on any systemctl command
- No batch operations (one service, one action per request)
- No server-side filtering/pagination — client-side only
- No persistent state or caching for inventory data
- No auto-polling/websocket for live updates
- No `journalctl` integration or log viewing
- No service unit file viewing/editing
- No service name in URL path — always in request body
- Do NOT reuse `ServiceRuleCreate` validator for ad-hoc commands (it hard-blocks protected; ad-hoc needs soft-warn)

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Tests-after
- **Framework**: pytest (backend), build check (frontend)

### QA Policy
Every task includes agent-executed QA scenarios.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — schemas + SSH functions):
├── Task 1: Schemas + types [quick]
└── Task 2: SSH inventory + command functions [unspecified-high]

Wave 2 (After Wave 1 — API endpoints):
└── Task 3: API endpoints + router registration + audit [unspecified-high]

Wave 3 (After Wave 2 — frontend + tests):
├── Task 4: Frontend inventory table + action buttons [visual-engineering]
└── Task 5: Test suite [quick]

Wave FINAL (After ALL tasks):
└── Task F1: Code quality + compliance review [unspecified-high]

Critical Path: T1/T2 → T3 → T4/T5 → F1
```

### Dependency Matrix
- **T1**: — → T3
- **T2**: — → T3
- **T3**: T1, T2 → T4, T5
- **T4**: T3 → F1
- **T5**: T3 → F1

---

## TODOs

- [ ] 1. Schemas + Types

  **What to do**:
  - Create `backend/app/services/live_schemas.py` with:
    - `ServiceInventoryItem(BaseModel)`: `unit: str`, `load_state: str`, `active_state: str`, `sub_state: str`, `description: str`, `is_managed: bool` (True if matching a ServiceRule), `is_protected: bool`
    - `ServiceCommandAction = Literal["start", "stop", "restart"]`
    - `ServiceCommandRequest(BaseModel)`: `service_name: str` (regex validated `^[a-zA-Z0-9_@:.-]+$`, max 100 chars, strip `.service` suffix), `action: ServiceCommandAction`
    - `ServiceCommandResponse(BaseModel)`: `success: bool`, `exit_code: int`, `stdout: str`, `stderr: str`, `service_name: str`, `action: str`, `is_protected: bool`
  - Do NOT reuse `ServiceRuleCreate` validator — the protected service check returns `is_protected: bool` flag, it does NOT raise

  **Auth**: N/A (schemas only)

  **Must NOT do**:
  - Do NOT hard-block protected services in schema validation — only flag them
  - Do NOT add `enable`/`disable`/`mask` to `ServiceCommandAction`

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 1 (with T2)
  - **Blocks**: T3
  - **Blocked By**: None

  **References**:
  - `backend/app/services/schemas.py` — existing schema pattern (Pydantic v2, `@field_validator`)
  - `backend/app/services/constants.py` — `PROTECTED_SERVICES` frozenset

  **Acceptance Criteria**:
  - [ ] `ServiceCommandRequest(service_name="nginx", action="start")` validates
  - [ ] `ServiceCommandRequest(service_name="nginx; rm -rf /", action="start")` raises ValidationError
  - [ ] `ServiceCommandRequest(service_name="nginx", action="enable")` raises ValidationError
  - [ ] `ServiceCommandRequest(service_name="sshd", action="stop")` validates (does NOT raise)

  **QA Scenarios**:
  ```
  Scenario: Protected service not blocked in schema
    Tool: Bash
    Steps:
      1. cd backend && .venv/bin/python -c "from app.services.live_schemas import ServiceCommandRequest; r = ServiceCommandRequest(service_name='sshd', action='stop'); print(r)"
    Expected Result: No exception — schema allows sshd
    Evidence: .sisyphus/evidence/task-1-protected-not-blocked.txt

  Scenario: Injection attempt blocked
    Tool: Bash
    Steps:
      1. cd backend && .venv/bin/python -c "from app.services.live_schemas import ServiceCommandRequest; ServiceCommandRequest(service_name='nginx; rm -rf /', action='start')"
    Expected Result: ValidationError raised
    Evidence: .sisyphus/evidence/task-1-injection-blocked.txt
  ```

  **Commit**: YES — `feat(schemas): add service inventory and command schemas`

- [ ] 2. SSH Inventory + Command Execution Functions

  **What to do**:
  - Add to `backend/app/services/collector.py` (extend, don't replace):
    - `async def list_all_services(host_ip, ssh_port, private_key_pem) -> list[dict]`:
      - SSH command: `systemctl list-units --type=service --all --no-pager --plain`
      - Parse output: 5 columns — `UNIT LOAD ACTIVE SUB DESCRIPTION`
      - Strip `.service` suffix from unit names
      - Return list of dicts: `{"unit": str, "load_state": str, "active_state": str, "sub_state": str, "description": str}`
      - Wrap SSH call with `asyncio.wait_for(conn.run(...), timeout=30.0)`
      - On connection failure: return empty list (caller handles)
    - `async def execute_service_command(host_ip, ssh_port, private_key_pem, service_name, action) -> dict`:
      - Validate action is in `("start", "stop", "restart")`
      - Build command: `f"systemctl {action} {shlex.quote(service_name)}"`
      - Wrap with `asyncio.wait_for(conn.run(..., check=False), timeout=30.0)`
      - Return: `{"success": exit_code == 0, "exit_code": int, "stdout": str, "stderr": str}`
      - On timeout: return `{"success": False, "exit_code": -1, "stdout": "", "stderr": "Command timed out after 30s"}`

  **Must NOT do**:
  - Do NOT modify existing `collect_service_states()` function
  - Do NOT accept arbitrary systemctl subcommands
  - Do NOT pass `--force` to any command

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 1 (with T1)
  - **Blocks**: T3
  - **Blocked By**: None

  **References**:
  - `backend/app/services/collector.py` — existing asyncssh pattern (extend this file)

  **Acceptance Criteria**:
  - [ ] `list_all_services()` parses multi-column output correctly
  - [ ] `execute_service_command()` uses `shlex.quote()` on service name
  - [ ] Both functions have 30s timeout via `asyncio.wait_for()`
  - [ ] Timeout produces clean error dict, not exception

  **QA Scenarios**:
  ```
  Scenario: Functions import cleanly
    Tool: Bash
    Steps:
      1. cd backend && .venv/bin/python -c "from app.services.collector import list_all_services, execute_service_command; print('OK')"
    Expected Result: "OK" printed, no import error
    Evidence: .sisyphus/evidence/task-2-import.txt
  ```

  **Commit**: YES — `feat(ssh): add live service inventory and command execution`

- [ ] 3. API Endpoints + Router Registration + Audit Logging

  **What to do**:
  - Create `backend/app/api/service_live.py` with router `APIRouter(prefix="/services", tags=["service-live"])`:
    - `GET /services/hosts/{host_id}/inventory` → `list[ServiceInventoryItem]`
      - Look up host + SSH key, decrypt key, call `list_all_services()`
      - For each service, set `is_managed` by checking if service name exists in ServiceRules for this host
      - Set `is_protected` by checking against `PROTECTED_SERVICES`
      - Auth: `current_active_user`
      - Return 400 if host has no SSH key
    - `POST /services/hosts/{host_id}/command` → `ServiceCommandResponse`
      - Body: `ServiceCommandRequest` (service_name + action)
      - Look up host + SSH key, decrypt key
      - Set `is_protected` flag from `PROTECTED_SERVICES`
      - Call `execute_service_command()` with `shlex.quote()` applied to name
      - Call `log_action(db, action=f"service_{action}", entity_type="service_command", entity_id=host_id, user_id=user.id, after_state={"service_name": name, "action": action, "exit_code": result["exit_code"]})`
      - Return `ServiceCommandResponse` with `is_protected` flag
      - Auth: `current_active_user`
      - Return 400 if host has no SSH key
  - Register in `backend/app/main.py`:
    ```python
    from app.api.service_live import router as service_live_router
    app.include_router(service_live_router, prefix="/api")
    ```

  **Must NOT do**:
  - Do NOT put service name in URL path
  - Do NOT add batch/multi-service endpoints
  - Do NOT block protected services — return `is_protected: true` in response

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO — Sequential after T1, T2
  - **Blocks**: T4, T5
  - **Blocked By**: T1, T2

  **References**:
  - `backend/app/api/service_sync.py:39-95` — SSH key lookup + execution pattern
  - `backend/app/audit/logger.py` — `log_action()` signature
  - `backend/app/services/constants.py` — `PROTECTED_SERVICES`

  **Acceptance Criteria**:
  - [ ] Inventory endpoint returns `list[ServiceInventoryItem]` with `is_managed` and `is_protected` flags
  - [ ] Command endpoint returns `ServiceCommandResponse` with structured result
  - [ ] Command endpoint calls `log_action()` for every command
  - [ ] Missing SSH key returns 400
  - [ ] Router registered in main.py

  **QA Scenarios**:
  ```
  Scenario: Inventory endpoint registered
    Tool: Bash
    Steps:
      1. cd backend && .venv/bin/python -c "from app.main import app; routes = [r.path for r in app.routes]; assert '/api/services/hosts/{host_id}/inventory' in routes or any('inventory' in r for r in routes); print('OK')"
    Expected Result: Route registered
    Evidence: .sisyphus/evidence/task-3-route.txt
  ```

  **Commit**: YES — `feat(api): add service inventory and command endpoints`

- [ ] 4. Frontend — Inventory Table + Action Buttons

  **What to do**:
  - Add types to `frontend/lib/types.ts`:
    ```typescript
    export interface LiveService {
      unit: string
      load_state: string
      active_state: string
      sub_state: string
      description: string
      is_managed: boolean
      is_protected: boolean
    }

    export interface ServiceCommandResult {
      success: boolean
      exit_code: number
      stdout: string
      stderr: string
      service_name: string
      action: string
      is_protected: boolean
    }
    ```
  - Modify `frontend/app/(dashboard)/hosts/[id]/page.tsx` — extend the existing "services" tab:
    - Add an "Inventory" section BELOW the existing effective-services table
    - "Load Inventory" button that fetches `GET /api/services/hosts/${id}/inventory`
    - Results shown in a table: Unit, Active State (badge), Sub State, Description, Actions
    - Badges: `active` = green, `inactive` = gray, `failed` = red, `activating`/`deactivating` = yellow
    - Client-side search/filter input above the table
    - Each row has Start / Stop / Restart buttons
    - Button click flow:
      - If `is_protected`: show custom `<Dialog>` with warning text ("This is a protected system service. Are you sure?") + Confirm/Cancel
      - If not protected: show native `confirm("Restart nginx?")`
      - On confirm: `POST /api/services/hosts/${id}/command` with `{service_name, action}`
      - Show loading spinner on the clicked button, disable all action buttons for that row
      - On success: show green toast/inline message, re-fetch inventory
      - On failure: show red error with stderr content
    - "Refresh" button to re-fetch inventory manually
    - "Managed" badge on services that match existing ServiceRules

  **Must NOT do**:
  - Do NOT add auto-polling / websocket
  - Do NOT add server-side filtering
  - Do NOT put service name in URL for the command fetch

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 3 (with T5)
  - **Blocks**: F1
  - **Blocked By**: T3

  **References**:
  - `frontend/app/(dashboard)/hosts/[id]/page.tsx` — existing services tab to extend
  - `frontend/app/(dashboard)/groups/[id]/services/page.tsx` — table + dialog pattern

  **Acceptance Criteria**:
  - [ ] `npm run build` passes
  - [ ] Inventory table renders with all columns
  - [ ] Filter input filters services client-side
  - [ ] Action buttons present on each row
  - [ ] Protected service click shows warning Dialog

  **QA Scenarios**:
  ```
  Scenario: Frontend builds
    Tool: Bash
    Steps:
      1. cd frontend && npm run build
    Expected Result: Exit code 0, no TypeScript errors
    Evidence: .sisyphus/evidence/task-4-build.txt
  ```

  **Commit**: YES — `feat(ui): add service inventory table and action controls`

- [ ] 5. Test Suite

  **What to do**:
  - Create `backend/tests/test_service_commands.py` with:
    - `TestCommandSchemas`: regex validation (valid, injection blocked, `.service` stripped, protected not blocked, invalid action rejected)
    - `TestInventoryParsing`: parse multi-column `systemctl list-units` output (happy path, empty, single service, service with spaces in description)
    - `TestCommandExecution`: `shlex.quote()` applied, timeout handling
    - `TestCommandAPI`: create host + SSH key, POST command, verify 201 + response shape, verify audit log entry, verify 400 on missing SSH key, verify 422 on invalid service name

  **Must NOT do**:
  - Do NOT make real SSH connections in tests
  - Do NOT modify existing test files

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 3 (with T4)
  - **Blocks**: F1
  - **Blocked By**: T3

  **References**:
  - `backend/tests/test_services.py` — test structure to follow
  - `backend/tests/conftest.py` — fixtures

  **Acceptance Criteria**:
  - [ ] 12+ tests
  - [ ] Schema validation tests pass without DB
  - [ ] API tests use `superuser_client` fixture

  **QA Scenarios**:
  ```
  Scenario: Test file is valid Python
    Tool: Bash
    Steps:
      1. cd backend && .venv/bin/python -c "import ast; ast.parse(open('tests/test_service_commands.py').read()); print('OK')"
    Expected Result: "OK" — valid syntax
    Evidence: .sisyphus/evidence/task-5-syntax.txt
  ```

  **Commit**: YES — `test(services): add live control test suite`

---

## Final Verification Wave

- [ ] F1. **Code Quality + Compliance Review** — `unspecified-high`
  Read all new/modified files. Check: regex validation present, `shlex.quote()` used, timeout on SSH, audit logging on all commands, protected service flag returned, no forbidden subcommands accepted, frontend build passes, action buttons disabled during execution.
  Output: `APPROVE/REJECT with details`

---

## Commit Strategy

1. `feat(schemas): add service inventory and command schemas` — T1
2. `feat(ssh): add live service inventory and command execution` — T2
3. `feat(api): add service inventory and command endpoints` — T3
4. `feat(ui): add service inventory table and action controls` — T4
5. `test(services): add live control test suite` — T5

---

## Success Criteria

### Verification Commands
```bash
curl -b cookie http://localhost:8000/api/hosts/1/services/inventory  # 50+ services
curl -X POST -b cookie -H 'Content-Type: application/json' \
  -d '{"service_name":"nginx","action":"restart"}' \
  http://localhost:8000/api/hosts/1/services/command  # {success: true, ...}
cd frontend && npm run build  # PASS
cd backend && pytest tests/test_service_commands.py -v  # PASS
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] SSH command injection blocked
- [ ] Protected services warned, not blocked
- [ ] All commands audit-logged
