# Host Auto-Discovery — Network Scan + Bulk Add

## TL;DR

> **Quick Summary**: Add network auto-discovery to Barricade. User provides a CIDR range, Barricade scans for hosts with SSH (port 22) open, filters out already-known hosts, and lets the user select multiple discovered hosts to bulk-add with a chosen SSH key and optional group assignment.
>
> **Deliverables**:
> - Network scanner module (asyncio TCP port check, CIDR validation, blocked ranges)
> - Discovery Celery task with progress reporting
> - Discovery API: POST scan, GET status/results, POST bulk-add
> - Discovery frontend page with scan form, live results table, SSH key selector, bulk add
> - Pydantic schemas and Settings additions
> - pytest suite for scanner + API
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: T1 → T3 → T5 → T6

---

## Context

### Original Request
User wants to specify a network CIDR, have Barricade scan for hosts with port 22 open, skip already-added hosts, then select multiple discovered hosts and add them all using a chosen SSH key.

### Research Findings
- **Scanner**: `asyncio.open_connection()` + `asyncio.wait_for(timeout=1.0)` + `asyncio.Semaphore(100)` — pure stdlib, no nmap/scapy. /24 (254 hosts) scans in ~3 seconds.
- **CIDR handling**: `ipaddress.ip_network(strict=False)` from stdlib. Must validate prefix length and block reserved ranges.
- **Execution model**: Celery task with `track_started=True` + `self.update_state()` for progress. Frontend polls via GET endpoint.
- **Hostname uniqueness**: Host model has unique constraint on `hostname`. Use reverse DNS with IP fallback.

### Metis Review — Key Directives
- `writer.wait_closed()` after `writer.close()` — prevents fd leaks under /20 scans
- `ConnectionResetError` = port open (SSH exists but closed connection)
- Block `169.254.0.0/16` (cloud metadata SSRF), `127.0.0.0/8`, `224.0.0.0/4`, `240.0.0.0/4`
- Celery `track_started=True` for STARTED state visibility
- Progress updates every ~50 hosts (not every single host)
- PENDING state ambiguity: Celery returns PENDING for both "queued" and "unknown task ID"
- Max bulk-add: 50 hosts per request
- One scan at a time per user (prevent concurrent scan abuse)

---

## Work Objectives

### Core Objective
Enable network-based host discovery so admins can find SSH-accessible hosts on their network and bulk-add them to Barricade without manual IP entry.

### Definition of Done
- [ ] User can enter CIDR, start scan, see discovered hosts with progress
- [ ] Already-known hosts (by IP) are excluded from results
- [ ] User can select multiple hosts, pick an SSH key, optionally assign groups, and bulk-add
- [ ] Invalid/oversized CIDRs are rejected with clear error messages
- [ ] Reserved/dangerous ranges (loopback, link-local, multicast) are blocked
- [ ] Scan completes /24 in under 10 seconds
- [ ] All tests pass

### Must Have
- CIDR validation: `ipaddress.ip_network(strict=False)`, reject prefix < configurable min (default /20)
- Blocked ranges: 127.0.0.0/8, 169.254.0.0/16, 224.0.0.0/4, 240.0.0.0/4
- Scanner: asyncio TCP port 22 check with 1s timeout, Semaphore(100) concurrency
- `writer.wait_closed()` after `writer.close()` in scanner
- `ConnectionResetError` treated as "port open"
- Celery task with `track_started=True` and progress via `self.update_state()`
- Filter results: exclude IPs already in `hosts` table
- Bulk-add endpoint: accept list of IPs + ssh_key_id + optional group_ids
- Hostname generation: attempt reverse DNS (`socket.getfqdn()`), fall back to IP string
- Max 50 hosts per bulk-add request
- Superuser-only access on all discovery endpoints
- Frontend: CIDR input, scan button, progress indicator, results table with checkboxes, SSH key dropdown, group multi-select, "Add Selected" button

### Must NOT Have (Guardrails)
- No nmap, scapy, or external scanning tools — stdlib only
- No recurring/scheduled scans — manual trigger only
- No auto-detect firewall backend during discovery (done on first sync)
- No auto-assign to groups (user chooses explicitly)
- No agent-based discovery (scan from Barricade server only)
- No scanning ports other than 22

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES (pytest + testcontainers from gap-fixes)
- **Automated tests**: YES (tests after implementation)
- **Framework**: pytest + pytest-asyncio + httpx

### QA Policy
- Scanner unit tests: mock asyncio connections
- API integration tests: real DB via testcontainers
- Frontend: Playwright E2E if time permits, otherwise API-only verification

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 2 parallel):
├── Task 1: Network scanner module + CIDR validation [unspecified-high]
└── Task 2: Discovery schemas + Settings config [quick]

Wave 2 (Backend API — 2 parallel):
├── Task 3: Discovery scan API + Celery task [unspecified-high]
└── Task 4: Bulk add hosts endpoint [quick]

Wave 3 (Frontend):
└── Task 5: Discovery UI page [visual-engineering]

Wave 4 (Tests):
└── Task 6: Discovery pytest suite [unspecified-high]

Wave FINAL (Review — 4 parallel):
├── F1: Plan compliance audit [oracle]
├── F2: Code quality review [unspecified-high]
├── F3: Real QA [unspecified-high]
└── F4: Scope fidelity check [unspecified-high]

Critical Path: T1 → T3 → T5 → T6 → F1-F4
Max Concurrent: 2 (Waves 1-2)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | — | T3 | 1 |
| T2 | — | T3, T4 | 1 |
| T3 | T1, T2 | T5 | 2 |
| T4 | T2 | T5 | 2 |
| T5 | T3, T4 | T6 | 3 |
| T6 | T3, T4 | F1-F4 | 4 |

### Agent Dispatch Summary

| Wave | Tasks | Categories |
|------|-------|------------|
| 1 | 2 | T1→`unspecified-high`, T2→`quick` |
| 2 | 2 | T3→`unspecified-high`, T4→`quick` |
| 3 | 1 | T5→`visual-engineering` |
| 4 | 1 | T6→`unspecified-high` |
| FINAL | 4 | F1→`oracle`, F2-F4→`unspecified-high` |

---

## TODOs

- [ ] 1. Network Scanner Module + CIDR Validation

  **What to do**:
  - Create `backend/app/discovery/__init__.py` (empty)
  - Create `backend/app/discovery/scanner.py`:
    - `async def check_port(host: str, port: int, semaphore: asyncio.Semaphore, timeout: float = 1.0) -> str | None`:
      - Use `asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)`
      - On success: `writer.close()` then `await writer.wait_closed()` → return host
      - Catch `asyncio.TimeoutError`, `ConnectionRefusedError`, `OSError` → return None
      - Catch `ConnectionResetError` → return host (port was open, service reset)
    - `async def scan_network(cidr: str, port: int = 22, timeout: float = 1.0, max_concurrent: int = 100) -> list[str]`:
      - Parse CIDR with `ipaddress.ip_network(cidr, strict=False)`
      - Enumerate hosts with `.hosts()` (excludes network + broadcast)
      - Create `asyncio.Semaphore(max_concurrent)`
      - `asyncio.gather(*tasks)` — all probe tasks
      - Return list of IPs with open port (filter out None)
    - `def validate_cidr(cidr: str, min_prefix: int = 20) -> ipaddress.IPv4Network`:
      - Parse with `ipaddress.ip_network(cidr, strict=False)`
      - Reject if `network.prefixlen < min_prefix` → raise ValueError with clear message
      - Reject if overlaps any blocked range → raise ValueError
      - Return validated network
    - `BLOCKED_NETWORKS` constant:
      ```python
      BLOCKED_NETWORKS = [
          ipaddress.ip_network("127.0.0.0/8"),       # loopback
          ipaddress.ip_network("169.254.0.0/16"),     # link-local / cloud metadata
          ipaddress.ip_network("224.0.0.0/4"),        # multicast
          ipaddress.ip_network("240.0.0.0/4"),        # reserved
      ]
      ```

  **Must NOT do**:
  - Do NOT import nmap, scapy, or any external scanning library
  - Do NOT omit `writer.wait_closed()` — causes fd leaks under load
  - Do NOT block private RFC1918 ranges (10.x, 172.16.x, 192.168.x) — those are the primary use case

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 2)
  - **Blocks**: Task 3
  - **Blocked By**: None

  **References**:
  - `backend/app/sync/diff.py` — Example of async helper functions in the codebase
  - Python docs: `asyncio.open_connection()`, `ipaddress.ip_network()`

  **Acceptance Criteria**:
  - [ ] `from app.discovery.scanner import scan_network, validate_cidr` imports successfully
  - [ ] `validate_cidr("10.0.0.0/24")` returns valid network
  - [ ] `validate_cidr("10.0.0.0/8")` raises ValueError (too large, default min /20)
  - [ ] `validate_cidr("127.0.0.1/32")` raises ValueError (blocked range)
  - [ ] `validate_cidr("169.254.169.254/32")` raises ValueError (cloud metadata blocked)
  - [ ] Scanner function signature accepts cidr, port, timeout, max_concurrent

  **QA Scenarios**:
  ```
  Scenario: CIDR validation rejects oversized range
    Tool: Bash
    Steps:
      1. Run: cd backend && .venv/bin/python -c "
         from app.discovery.scanner import validate_cidr
         try:
           validate_cidr('10.0.0.0/8')
           print('FAIL: should have raised')
         except ValueError as e:
           assert 'prefix' in str(e).lower() or 'large' in str(e).lower()
           print(f'PASS: rejected /8: {e}')
         "
      2. Assert: output contains "PASS"
    Expected Result: /8 rejected with clear error
    Evidence: .sisyphus/evidence/task-1-cidr-validation.txt

  Scenario: Blocked ranges rejected
    Tool: Bash
    Steps:
      1. Run: cd backend && .venv/bin/python -c "
         from app.discovery.scanner import validate_cidr
         for cidr in ['127.0.0.1/32', '169.254.1.0/24', '224.0.0.0/24']:
           try:
             validate_cidr(cidr)
             print(f'FAIL: {cidr} should be blocked')
           except ValueError:
             print(f'PASS: {cidr} blocked')
         "
      2. Assert: all 3 show "PASS"
    Expected Result: All reserved ranges blocked
    Evidence: .sisyphus/evidence/task-1-blocked-ranges.txt
  ```

  **Commit**: YES
  - Message: `feat(discovery): add async network scanner with CIDR validation`
  - Files: `backend/app/discovery/__init__.py`, `backend/app/discovery/scanner.py`

- [ ] 2. Discovery Schemas + Settings Config

  **What to do**:
  - Create `backend/app/schemas/discovery.py`:
    ```python
    class ScanRequest(BaseModel):
        cidr: str                    # e.g. "10.0.0.0/24"
        port: int = 22
        timeout: float = 1.0        # per-host timeout in seconds

    class DiscoveredHost(BaseModel):
        ip: str
        hostname: str | None = None  # reverse DNS result, None if lookup failed

    class ScanStatus(BaseModel):
        job_id: str
        status: str                  # "pending" | "running" | "done" | "error"
        progress: int = 0            # hosts scanned so far
        total: int = 0               # total hosts to scan
        hosts_found: list[DiscoveredHost] = []
        error: str | None = None

    class BulkAddRequest(BaseModel):
        ips: list[str]               # IPs to add as hosts
        ssh_key_id: int              # SSH key to assign to all
        group_ids: list[int] = []    # optional groups to assign
        ssh_port: int = 22

    class BulkAddResponse(BaseModel):
        added: int
        skipped: int                 # already existed (race condition safety)
        hosts: list[HostResponse]    # created host details
    ```
  - Add to `backend/app/config.py` Settings class:
    ```python
    DISCOVERY_MIN_PREFIX: int = 20          # smallest allowed CIDR prefix (/20 = 4094 hosts)
    DISCOVERY_SCAN_TIMEOUT: float = 1.0     # per-host TCP timeout
    DISCOVERY_MAX_CONCURRENT: int = 100     # max simultaneous connections
    DISCOVERY_MAX_BULK_ADD: int = 50        # max hosts per bulk-add request
    ```

  **Must NOT do**:
  - Do NOT create a DB model for scan jobs — use Celery result backend

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Tasks 3, 4
  - **Blocked By**: None

  **References**:
  - `backend/app/schemas/hosts.py` — Existing schema pattern (HostCreate, HostResponse)
  - `backend/app/config.py` — Settings class to extend

  **Acceptance Criteria**:
  - [ ] `from app.schemas.discovery import ScanRequest, ScanStatus, BulkAddRequest` imports
  - [ ] `from app.config import settings; settings.DISCOVERY_MIN_PREFIX` returns 20
  - [ ] BulkAddRequest validates ip list + ssh_key_id

  **QA Scenarios**:
  ```
  Scenario: Schemas import and validate
    Tool: Bash
    Steps:
      1. Run: cd backend && .venv/bin/python -c "
         from app.schemas.discovery import ScanRequest, BulkAddRequest
         req = ScanRequest(cidr='10.0.0.0/24')
         assert req.port == 22
         bulk = BulkAddRequest(ips=['10.0.0.1'], ssh_key_id=1)
         assert len(bulk.ips) == 1
         print('PASS: schemas validate')
         "
    Expected Result: Schemas work correctly
    Evidence: .sisyphus/evidence/task-2-schemas.txt
  ```

  **Commit**: YES
  - Message: `feat(discovery): add discovery schemas and settings`
  - Files: `backend/app/schemas/discovery.py`, `backend/app/config.py`

- [ ] 3. Discovery Scan API + Celery Task

  **What to do**:
  - Create `backend/app/tasks/discovery.py` — Celery task:
    - `@celery_app.task(bind=True, track_started=True, name="discovery.scan_network")`
    - `def scan_network_task(self, cidr: str, port: int, timeout: float, exclude_ips: list[str]) -> dict`:
      1. Parse CIDR, enumerate hosts, subtract `exclude_ips` (already in DB)
      2. Run scan via `asyncio.run(scan_network(...))`
      3. Update progress every ~50 hosts: `self.update_state(state='PROGRESS', meta={'progress': n, 'total': total})`
      4. For each discovered IP, attempt reverse DNS: `socket.getfqdn(ip)` — if returns the IP itself, hostname = None
      5. Return `{"hosts_found": [{"ip": "...", "hostname": "..."|None}, ...], "total_scanned": N}`
    - Use settings for timeout/concurrency values

  - Create `backend/app/api/discovery.py` — FastAPI router:
    - `POST /discovery/scan` (superuser only):
      1. Validate CIDR using `validate_cidr(req.cidr, settings.DISCOVERY_MIN_PREFIX)`
      2. Query DB for all existing host IPs: `SELECT ip_address FROM hosts`
      3. Dispatch Celery task with `exclude_ips`
      4. Return `ScanStatus(job_id=task.id, status="pending", total=host_count)`
    - `GET /discovery/scan/{job_id}` (superuser only):
      1. Check `AsyncResult(job_id)`
      2. Map Celery states: PENDING → "pending", STARTED → "running", PROGRESS → "running" (with progress from meta), SUCCESS → "done", FAILURE → "error"
      3. On SUCCESS: return full results with `hosts_found`
      4. On FAILURE: return error message
    - Register router in `app/main.py` with `prefix="/api"`

  **Must NOT do**:
  - Do NOT call `result.get()` — it blocks the web worker
  - Do NOT allow concurrent scans from same user (check if active scan exists)
  - Do NOT return raw Celery exception objects — sanitize error messages

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 4)
  - **Blocks**: Task 5
  - **Blocked By**: Tasks 1, 2

  **References**:
  - `backend/app/tasks/sync.py` — Existing Celery task pattern (imports celery_app, asyncio.run bridge)
  - `backend/app/tasks/__init__.py` — celery_app instance
  - `backend/app/api/sync.py` — Existing API pattern for dispatching Celery tasks
  - `backend/app/main.py:48-55` — Router registration pattern

  **Acceptance Criteria**:
  - [ ] POST /api/discovery/scan with valid CIDR → 200 with job_id
  - [ ] POST /api/discovery/scan with /8 → 422
  - [ ] POST /api/discovery/scan with 127.0.0.0/24 → 422 (blocked)
  - [ ] GET /api/discovery/scan/{job_id} → returns status + progress
  - [ ] On completion: hosts_found list excludes IPs already in DB
  - [ ] Non-superuser → 403
  - [ ] Router registered in main.py

  **QA Scenarios**:
  ```
  Scenario: Scan endpoint rejects blocked CIDR
    Tool: Bash (curl)
    Preconditions: Superuser logged in
    Steps:
      1. POST /api/discovery/scan {"cidr": "127.0.0.0/24"}
      2. Assert: HTTP 422, response contains "blocked" or "not permitted"
    Expected Result: Blocked range rejected
    Evidence: .sisyphus/evidence/task-3-blocked-scan.txt

  Scenario: Scan endpoint accepts valid CIDR
    Tool: Bash (curl)
    Steps:
      1. POST /api/discovery/scan {"cidr": "10.0.0.0/28"}
      2. Assert: HTTP 200, response has job_id and status="pending"
    Expected Result: Scan initiated
    Evidence: .sisyphus/evidence/task-3-valid-scan.txt
  ```

  **Commit**: YES (grouped with T4)
  - Message: `feat(api): add discovery scan and bulk-add endpoints`
  - Files: `backend/app/tasks/discovery.py`, `backend/app/api/discovery.py`, `backend/app/main.py`

- [ ] 4. Bulk Add Hosts Endpoint

  **What to do**:
  - Add to `backend/app/api/discovery.py`:
    - `POST /discovery/add-hosts` (superuser only):
      1. Validate `len(req.ips) <= settings.DISCOVERY_MAX_BULK_ADD` → 422 if exceeded
      2. Validate `ssh_key_id` exists in DB → 404 if not
      3. Validate all `group_ids` exist in DB → 404 if any missing
      4. For each IP in `req.ips`:
         a. Check if host with this IP already exists → skip (race condition safety)
         b. Attempt hostname resolution: `socket.getfqdn(ip)` — if returns raw IP, use IP as hostname
         c. Ensure hostname uniqueness: if `hostname` already exists in DB, append `-{n}` suffix
         d. Create Host: `hostname=resolved, ip_address=ip, ssh_port=req.ssh_port, ssh_key_id=req.ssh_key_id`
         e. Assign to groups via HostGroupMembership inserts
      5. Return `BulkAddResponse(added=N, skipped=M, hosts=[...])`
  - Use a single DB transaction for all hosts (atomic: all succeed or all fail)

  **Must NOT do**:
  - Do NOT create hosts one-by-one with separate transactions
  - Do NOT auto-detect firewall backend (left as "unknown")
  - Do NOT skip SSH key validation

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 3)
  - **Blocks**: Task 5
  - **Blocked By**: Task 2

  **References**:
  - `backend/app/api/hosts.py:30-75` — Existing host creation logic (model construction, group membership insert pattern)
  - `backend/app/models/host.py` — Host model, HostGroupMembership table

  **Acceptance Criteria**:
  - [ ] POST /api/discovery/add-hosts with valid IPs + ssh_key_id → 201, returns added count
  - [ ] Duplicate IPs silently skipped (not error)
  - [ ] Max 50 hosts per request enforced → 422 if exceeded
  - [ ] Invalid ssh_key_id → 404
  - [ ] All hosts assigned to specified groups
  - [ ] Hostnames are unique (no duplicate constraint violations)

  **QA Scenarios**:
  ```
  Scenario: Bulk add creates hosts
    Tool: Bash (curl)
    Preconditions: SSH key exists (id=1), superuser logged in
    Steps:
      1. POST /api/discovery/add-hosts {"ips":["10.99.0.1","10.99.0.2"],"ssh_key_id":1}
      2. Assert: HTTP 201, response has added=2
      3. GET /api/hosts → Assert: both hosts appear in list
    Expected Result: Hosts bulk-created successfully
    Evidence: .sisyphus/evidence/task-4-bulk-add.txt

  Scenario: Duplicate IPs skipped
    Tool: Bash (curl)
    Steps:
      1. POST /api/discovery/add-hosts {"ips":["10.99.0.1"],"ssh_key_id":1} (already exists from above)
      2. Assert: HTTP 201, added=0, skipped=1
    Expected Result: No error, silently skipped
    Evidence: .sisyphus/evidence/task-4-duplicate-skip.txt
  ```

  **Commit**: YES (grouped with T3)

- [ ] 5. Discovery UI Page

  **What to do**:
  - Create `frontend/app/(dashboard)/hosts/discover/page.tsx`:
    - **Scan Form Section**:
      - CIDR input field with placeholder "e.g. 10.0.0.0/24"
      - "Scan Network" button (disabled while scan in progress)
      - Validation: basic CIDR format check client-side before submit
    - **Progress Section** (visible during scan):
      - Progress bar or text: "Scanning... 128/254 hosts checked"
      - Poll GET /api/discovery/scan/{job_id} every 2 seconds via TanStack Query `refetchInterval`
      - Stop polling when status = "done" or "error"
    - **Results Section** (visible after scan completes):
      - Table with columns: checkbox | IP | Hostname (or "—") | Status ("New")
      - "Select All" / "Deselect All" toggle
      - Empty state: "No new SSH hosts found on this network"
    - **Add Section** (visible when hosts selected):
      - SSH Key dropdown: fetch from GET /api/ssh-keys, show name + "(default)" indicator
      - Groups multi-select: fetch from GET /api/groups, optional
      - "Add {N} Hosts" button — calls POST /api/discovery/add-hosts
      - Success toast: "{N} hosts added" with link to hosts list
    - **Error handling**:
      - Invalid CIDR → show inline error under input
      - Scan error → show error banner
      - Bulk-add error → show error toast
  - Add navigation: "Discover Hosts" button on the hosts list page (`/hosts`)
  - Use existing `apiFetch` pattern from `frontend/lib/api.ts`
  - Follow existing page layout patterns (Card, Table components from shadcn/ui)

  **Must NOT do**:
  - Do NOT auto-refresh the hosts list page after add (user navigates manually)
  - Do NOT show already-known hosts in results (backend already filters them)
  - Do NOT add discovery to the main nav — access via button on hosts page

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: Full page with form, polling, table with checkboxes, dropdowns, state management
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (alone)
  - **Blocks**: Task 6
  - **Blocked By**: Tasks 3, 4

  **References**:
  - `frontend/app/(dashboard)/hosts/page.tsx` — Existing hosts page (add "Discover" button here)
  - `frontend/app/(dashboard)/groups/[id]/sync/page.tsx` — Example of polling pattern with TanStack Query
  - `frontend/lib/api.ts` — `apiFetch<T>()` helper
  - `frontend/lib/types.ts` — TypeScript interfaces (Host, SSHKey)
  - `frontend/components/ui/` — shadcn/ui components (Card, Table, Button, Input, Select, Checkbox)

  **Acceptance Criteria**:
  - [ ] /hosts page has "Discover Hosts" button
  - [ ] /hosts/discover page renders with CIDR input + Scan button
  - [ ] Scan shows progress while running
  - [ ] Results table shows discovered hosts with checkboxes
  - [ ] SSH key dropdown populated from API
  - [ ] "Add Selected" creates hosts and shows success feedback
  - [ ] Error states handled (invalid CIDR, scan failure, add failure)

  **QA Scenarios**:
  ```
  Scenario: Discovery page renders and accepts input
    Tool: Playwright
    Steps:
      1. Navigate to /hosts
      2. Assert: "Discover" or "Discover Hosts" button visible
      3. Click "Discover Hosts" button
      4. Assert: CIDR input field visible
      5. Type "10.0.0.0/28" into CIDR input
      6. Assert: "Scan" button is enabled
    Expected Result: Discovery page loads with functional form
    Evidence: .sisyphus/evidence/task-5-discovery-page.png

  Scenario: SSH key dropdown populated
    Tool: Playwright
    Preconditions: At least one SSH key exists
    Steps:
      1. Navigate to /hosts/discover
      2. Assert: SSH key dropdown shows available keys
    Expected Result: Keys loaded from API
    Evidence: .sisyphus/evidence/task-5-ssh-dropdown.png
  ```

  **Commit**: YES
  - Message: `feat(ui): add host discovery page`
  - Files: `frontend/app/(dashboard)/hosts/discover/page.tsx`, `frontend/app/(dashboard)/hosts/page.tsx` (button)

- [ ] 6. Discovery Pytest Suite

  **What to do**:
  - Create `backend/tests/test_discovery.py` with:
    - **Class TestCIDRValidation**:
      - `test_valid_cidr` — "10.0.0.0/24" passes
      - `test_cidr_too_large` — "10.0.0.0/8" raises ValueError
      - `test_blocked_loopback` — "127.0.0.1/32" raises ValueError
      - `test_blocked_link_local` — "169.254.0.0/24" raises ValueError
      - `test_private_ranges_allowed` — "192.168.1.0/24" passes (RFC1918 is the use case)
    - **Class TestScanner** (mock asyncio connections):
      - `test_scan_returns_open_ports` — mock `asyncio.open_connection` to succeed for 2 IPs, fail for rest → returns 2 IPs
      - `test_scan_handles_timeout` — mock timeout → returns empty list
      - `test_connection_reset_counts_as_open` — mock ConnectionResetError → IP included in results
    - **Class TestDiscoveryAPI** (integration with DB):
      - `test_scan_endpoint_accepts_valid_cidr` — POST /api/discovery/scan → 200 with job_id
      - `test_scan_endpoint_rejects_blocked_cidr` — POST /api/discovery/scan with 127.0.0.0/24 → 422
      - `test_bulk_add_creates_hosts` — POST /api/discovery/add-hosts → 201, hosts in DB
      - `test_bulk_add_skips_existing` — add host first, then bulk-add same IP → skipped=1
      - `test_bulk_add_enforces_limit` — 51 IPs → 422
      - `test_non_superuser_rejected` — viewer_client → 403
  - Use conftest fixtures from gap-fixes: `superuser_client`, `viewer_client`, `db`, `mock_celery_tasks`
  - Mock Celery task dispatch in API tests (don't actually scan)
  - Add `pytestmark = pytest.mark.integration`

  **Must NOT do**:
  - Do NOT perform real network scans in tests
  - Do NOT test against external networks

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 3, 4

  **References**:
  - `backend/tests/conftest.py` — Shared fixtures
  - `backend/tests/test_sync.py` — Pattern for testing Celery-dispatching endpoints with mock

  **Acceptance Criteria**:
  - [ ] `cd backend && .venv/bin/pytest tests/test_discovery.py -v` → all pass, ≥10 tests
  - [ ] Scanner tests mock asyncio (no real network calls)
  - [ ] API tests use real DB via testcontainers
  - [ ] RBAC tested (non-superuser rejected)

  **QA Scenarios**:
  ```
  Scenario: Discovery test suite passes
    Tool: Bash
    Steps:
      1. Run: cd backend && .venv/bin/pytest tests/test_discovery.py -v --tb=short
      2. Assert: exit code 0, ≥10 tests, 0 failures
    Expected Result: All discovery tests pass
    Evidence: .sisyphus/evidence/task-6-discovery-tests.txt
  ```

  **Commit**: YES
  - Message: `test(discovery): add discovery pytest suite`
  - Files: `backend/tests/test_discovery.py`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Verify all Must Have items implemented. Check blocked ranges enforced. Verify max bulk-add limit. Check superuser-only access.

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run pytest + ruff. Check for fd leaks (writer.wait_closed). Check no secrets in code. Verify CIDR validation covers edge cases.

- [ ] F3. **Real QA** — `unspecified-high`
  Start Docker stack. Scan localhost/24 or test network. Verify results exclude existing hosts. Bulk-add 3 hosts. Verify they appear in hosts list.

- [ ] F4. **Scope Fidelity Check** — `unspecified-high`
  Verify no nmap/scapy dependencies. No scheduled scans. No firewall auto-detect. No non-22 port scanning. Only discovery-related files changed.

---

## Commit Strategy

| Task | Commit Message | Key Files |
|------|---------------|-----------|
| T1 | `feat(discovery): add async network scanner with CIDR validation` | `app/discovery/scanner.py` |
| T2 | `feat(discovery): add discovery schemas and settings` | `app/schemas/discovery.py`, `app/config.py` |
| T3+T4 | `feat(api): add discovery scan and bulk-add endpoints` | `app/api/discovery.py`, `app/tasks/discovery.py` |
| T5 | `feat(ui): add host discovery page` | `frontend/app/(dashboard)/hosts/discover/page.tsx` |
| T6 | `test(discovery): add discovery pytest suite` | `backend/tests/test_discovery.py` |

---

## Success Criteria

### Verification Commands
```bash
# Scanner unit test
cd backend && .venv/bin/pytest tests/test_discovery.py -v

# CIDR validation rejects bad input
curl -s -X POST http://localhost:8000/api/discovery/scan \
  -H "Cookie: ..." -d '{"cidr":"10.0.0.0/8"}' | jq .detail
# Expected: "CIDR range too large..."

# Scan a /24
curl -s -X POST http://localhost:8000/api/discovery/scan \
  -H "Cookie: ..." -d '{"cidr":"10.0.0.0/24"}' | jq .
# Expected: {"job_id": "...", "status": "pending"}

# Bulk add
curl -s -X POST http://localhost:8000/api/discovery/add-hosts \
  -H "Cookie: ..." -d '{"ips":["10.0.0.1","10.0.0.2"],"ssh_key_id":1}' | jq .
# Expected: {"added": 2, "hosts": [...]}
```
