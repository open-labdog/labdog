# Single Service Consolidation — Unified Binary Architecture

## TL;DR

> **Quick Summary**: Collapse Barricade from 5 systemd services + Node.js runtime into a single Python process. Convert Next.js to static export served by FastAPI, run Alembic migrations at startup, and embed Celery worker+beat as managed subprocesses. Result: one `barricade.service`, no Node.js dependency, simpler packaging.
>
> **Deliverables**:
> - Frontend converted to static export, served by FastAPI `StaticFiles`
> - Authentication middleware replaced with client-side auth guard
> - API proxy rewrites replaced with same-origin serving (frontend + API on same port)
> - Alembic migrations run at app startup (before uvicorn accepts connections)
> - Celery worker+beat spawned as managed subprocesses from `__main__.py`
> - Single `barricade.service` replaces 5 systemd units + target
> - Updated packaging (no bundled Node.js, smaller package)
> - Updated docker-compose (single backend container, optional separate worker)
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 3 waves
> **Critical Path**: T1 → T3 → T5 → T7

---

## Context

### Current Architecture (5 processes)

```
barricade-api.service       → uvicorn (FastAPI)
barricade-worker.service    → celery worker
barricade-beat.service      → celery beat scheduler
barricade-frontend.service  → node server.js (Next.js standalone)
barricade-migrate.service   → alembic upgrade head (oneshot)
barricade.target            → groups all above
```

### Target Architecture (1 process)

```
barricade.service → python -m app
  ├── alembic upgrade head          (runs before uvicorn starts)
  ├── uvicorn app.main:app          (FastAPI serves API + static frontend)
  ├── celery worker subprocess      (background tasks)
  └── celery beat subprocess        (periodic scheduler)
```

### Why This Works for Barricade

Barricade is a single-node management server. It does not need horizontal scaling, independent worker failure domains, or separate Node.js runtime. The consolidation:
- Eliminates Node.js runtime dependency entirely (~50MB savings)
- Reduces ops surface from 5 services to 1
- Simplifies packaging (no need to bundle `node` binary)
- Makes `systemctl start barricade` / `systemctl stop barricade` intuitive

### Architecture Decisions

- **Static export**: All pages are client components with `'use client'`. No SSR, no API routes, no server actions. Only blockers are middleware.ts (auth redirect) and next.config.ts rewrites — both replaceable.
- **Same-origin serving**: FastAPI serves static frontend files at `/` and API at `/api/*`. This eliminates CORS complexity, proxy rewrites, and the `NEXT_PUBLIC_API_URL` / `ALLOWED_ORIGINS` configuration. Cookies work automatically (same origin). Frontend's `API_BASE = ""` already assumes same-origin.
- **Celery subprocess management**: The main process spawns `celery worker --beat` as a child process (using `subprocess.Popen`). On SIGTERM, the main process sends SIGTERM to the child, waits for graceful shutdown, then exits. RedBeat prevents duplicate schedules even if restarted.
- **Startup migrations**: Alembic runs synchronously before uvicorn starts accepting connections. If migration fails, the process exits with non-zero code and systemd restarts it.
- **Fonts**: `next/font/google` fetches fonts at build time and embeds them as static files — works with static export.

### Logging Impact

**Current state**: 5 services each write to their own journald unit:
```bash
journalctl -u barricade-api
journalctl -u barricade-worker
journalctl -u barricade-beat
journalctl -u barricade-frontend
```

**After consolidation**: Everything goes to one unit (`barricade.service`):
```bash
journalctl -u barricade          # all logs in one stream
```

**Implications**:
- **Positive**: Single `journalctl -u barricade -f` shows everything — no need to tail 4 units. JSON log format (`[logging] format = "json"`) makes it easy to filter by component in log aggregators (Loki, ELK).
- **Positive**: No more frontend Node.js logs polluting the journal.
- **Mitigation for mixed output**: The logging config (from the TOML refactor) already tags each log line with the logger name (`app.api.sync`, `celery.worker`, etc.). The celery subprocess inherits stderr, so its output appears in the same journal stream with proper attribution.
- **Lost**: Cannot independently filter `journalctl -u barricade-worker` for just Celery logs. Workaround: `journalctl -u barricade | grep celery` or use JSON format with `jq`.
- **No change**: Audit logging (database-backed) is unaffected — it's in PostgreSQL, not log files.

### Security

- Static files served from a fixed directory (`/usr/lib/barricade/frontend/out/`). FastAPI's `StaticFiles` does not allow path traversal.
- Same-origin serving eliminates CORS attack surface (no cross-origin requests needed).
- Auth cookie still httpOnly/SameSite — client-side auth guard is for UX (redirect to login), not security. API endpoints still enforce `current_active_user` / `current_superuser` server-side.

---

## Work Objectives

### Definition of Done
- [x] Frontend builds with `output: "export"` and produces static files in `out/`
- [x] Next.js middleware.ts removed; client-side `AuthGuard` component handles redirects
- [x] Root page (`/`) renders client-side redirect instead of server-side `redirect()`
- [x] FastAPI serves static frontend at `/` via `StaticFiles` with SPA fallback
- [x] API routes continue to work at `/api/*` (same origin, no CORS needed for browser)
- [x] Alembic migrations run at startup before uvicorn accepts connections
- [x] Celery worker+beat spawned as managed subprocess from `__main__.py`
- [x] Graceful shutdown: SIGTERM propagated to celery subprocess
- [x] Single `barricade.service` systemd unit replaces all 5 units + target
- [x] Docker compose simplified to single backend service (+ postgres + redis)
- [x] Packaging updated: no Node.js binary, no frontend service, smaller package
- [x] `dev.sh` updated: single backend process (with `--reload` for uvicorn, separate celery for dev)
- [x] All existing tests pass
- [ ] Frontend Playwright E2E tests pass against single-service deployment (requires running stack)

### Must Have
- Client-side auth guard that checks cookie presence and redirects to `/login`
- SPA fallback in FastAPI (unknown routes serve `index.html` for client-side routing)
- Celery subprocess health monitoring (restart if crashed)
- SIGTERM/SIGINT propagation to child processes
- Startup migration failure = process exit (systemd restarts)
- `next.config.ts` with `output: "export"` and `trailingSlash: true`
- Static build step in Makefile/packaging that runs `npm run build`
- Frontend `API_BASE` remains `""` (same-origin, no change needed)

### Must NOT Have (Guardrails)
- No custom HTTP server for static files (use FastAPI's `StaticFiles`)
- No embedded Python HTTP server for frontend (no `http.server`)
- No changes to any backend API routes, models, or business logic
- No changes to frontend component logic, styling, or data fetching
- No removal of CORS middleware entirely (keep it for non-browser API clients)
- No WebSocket changes (SSH terminal must continue working)
- No modification to Celery task code, routing, or scheduling logic
- No changes to Alembic migration files

---

## Verification Strategy

### Automated
- Existing pytest suite (unit + API tests) must pass unchanged
- Existing Playwright E2E tests must pass against single-service deployment
- New test: verify static files served at `/`, `/login`, `/dashboard`
- New test: verify SPA fallback (unknown route returns `index.html`)
- New test: verify celery subprocess starts and processes a task

### Manual
- Build package, install on clean VM, verify single `systemctl start barricade` works
- Verify `journalctl -u barricade` shows API + Celery logs
- Verify SSH terminal (WebSocket) works through single service
- Verify auth flow: login → cookie set → navigate protected routes → logout → redirected

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Frontend Static Export — 3 parallel):
├── T1: Convert Next.js to static export (next.config.ts + remove middleware) [unspecified-high]
├── T2: Client-side AuthGuard component + root page redirect [unspecified-high]
└── T3: FastAPI static file serving + SPA fallback [unspecified-high]

Wave 2 (Process Consolidation — 2 parallel):
├── T4: Startup migrations in __main__.py (depends: none, parallel-safe) [quick]
└── T5: Celery subprocess manager with signal propagation (depends: none) [unspecified-high]

Wave 3 (Deployment — 2 parallel):
├── T6: Single systemd service + docker-compose + dev.sh (depends: T3, T5) [unspecified-high]
└── T7: Packaging update — remove Node.js, update Makefile/nfpm (depends: T1, T6) [unspecified-high]

Wave 4 (Verification):
└── T8: Integration testing + E2E verification (depends: all) [unspecified-high]

Critical Path: T1 → T3 → T6 → T7
Max Concurrent: 3
```

### Dependency Matrix

```
T1 (static export)      → T7 (packaging)
T2 (auth guard)         → T8 (testing)
T3 (FastAPI static)     → T6 (systemd/docker), T8 (testing)
T4 (startup migrations) → T6 (systemd/docker)
T5 (celery subprocess)  → T6 (systemd/docker)
T6 (deployment)         → T7 (packaging), T8 (testing)
T7 (packaging)          → T8 (testing)
```

---

## TODOs

- [x] 1. Convert Next.js to Static Export

  **What to do**:
  - Update `frontend/next.config.ts`:
    - Change `output` from `"standalone"` to `"export"`
    - Add `trailingSlash: true` (required for static export routing)
    - Remove the `rewrites()` function (no server to execute rewrites)
    - Keep `reactStrictMode: true`
  - Delete `frontend/middleware.ts` (middleware is incompatible with static export)
  - Update `frontend/app/page.tsx`: Replace server-side `redirect("/dashboard")` with a client component that does `window.location.replace("/dashboard")` (or render nothing and let AuthGuard handle it)
  - Update `frontend/app/layout.tsx`: The `next/font/google` import works with static export (fonts are fetched at build time). No changes needed.
  - Run `npm run build` and verify output in `frontend/out/`
  - Verify all routes produce static HTML files

  **Must NOT do**:
  - Change any component logic or styling
  - Remove `'use client'` directives
  - Add any `getStaticPaths` or `generateStaticParams` (not needed — all pages are client-rendered)

  **Agent profile**: `unspecified-high`
  **Depends on**: Nothing
  **Acceptance**: `npm run build` succeeds with `output: "export"`, `frontend/out/` contains `index.html`, `login/index.html`, `dashboard/index.html`, etc.
  **Commit**: `feat: convert frontend to static export`

---

- [x] 2. Client-Side Auth Guard

  **What to do**:
  - Create `frontend/components/auth-guard.tsx`:
    - Client component that checks for `barricade_auth` cookie presence
    - If no cookie and not on `/login` or `/register`, redirect to `/login`
    - If cookie present and on `/login` or `/register`, redirect to `/dashboard`
    - Show nothing (or a loading spinner) during the check to prevent flash
    - This replaces the deleted `middleware.ts` functionality
  - Integrate `AuthGuard` into `frontend/app/layout.tsx` (wrap children) or into `Providers`
  - Note: This is a UX convenience only — the real auth enforcement is server-side on every API call. If someone manually navigates to `/dashboard` without a cookie, they'll see the shell but all API calls will fail and the existing `AuthProvider` will set `user = null`.

  **Must NOT do**:
  - Modify the existing `AuthProvider` in `providers.tsx` (it already handles user state)
  - Add any server-side auth logic
  - Change the cookie name or format

  **Agent profile**: `unspecified-high`
  **Depends on**: Nothing (can parallel with T1)
  **Acceptance**: Auth flow works: unauthenticated user sees login page, authenticated user sees dashboard, logout redirects to login
  **Commit**: `feat: add client-side auth guard to replace Next.js middleware`

---

- [x] 3. FastAPI Static File Serving + SPA Fallback

  **What to do**:
  - Update `backend/app/main.py`:
    - After all API routers, mount static files with SPA fallback
    - Add a configurable `static_dir` setting (default: auto-detect from project structure)
    - Use `StaticFiles(directory=static_dir, html=True)` mounted at `/`
    - Add a catch-all route for SPA client-side routing that serves `index.html` for any non-API, non-file path
  - Add `static_dir` to `[server]` section in `backend/app/config.py`:
    - `static_dir: str = ""` — path to frontend `out/` directory
    - Empty = auto-detect: look for `../frontend/out` (dev) or `/usr/lib/barricade/frontend/out` (production)
  - The static files must be mounted AFTER all API routers so `/api/*` routes take priority
  - CORS middleware should remain (for non-browser API clients like curl/scripts)

  **Must NOT do**:
  - Change any API route paths or behavior
  - Remove CORS middleware
  - Serve static files from a different process

  **Agent profile**: `unspecified-high`
  **Depends on**: T1 (needs `out/` directory to exist for testing)
  **Acceptance**: `curl http://localhost:8000/` returns frontend HTML, `curl http://localhost:8000/api/health` returns JSON, `curl http://localhost:8000/dashboard` returns `index.html` (SPA fallback)
  **Commit**: `feat: serve static frontend from FastAPI with SPA fallback`

---

- [x] 4. Startup Migrations

  **What to do**:
  - Update `backend/app/__main__.py`:
    - Before calling `uvicorn.run()`, run Alembic migrations synchronously
    - Use `alembic.command.upgrade(alembic_config, "head")` programmatically
    - Construct `alembic.config.Config` pointing to the `alembic.ini` in the backend directory
    - If migration fails, log the error and `sys.exit(1)` (systemd will restart)
    - Add a `--skip-migrate` flag for development (when running migrations separately)
  - The migration must complete BEFORE uvicorn starts accepting connections

  **Must NOT do**:
  - Modify any Alembic migration files
  - Run migrations in a background thread (must block startup)
  - Catch and swallow migration errors (must fail loudly)

  **Agent profile**: `quick`
  **Depends on**: Nothing
  **Acceptance**: Fresh database + `python -m app` → tables created → API serves requests. Bad migration → process exits with non-zero code.
  **Commit**: `feat: run database migrations at startup`

---

- [x] 5. Celery Subprocess Manager

  **What to do**:
  - Create `backend/app/celery_manager.py`:
    - Class `CeleryManager` that spawns `celery -A app.tasks worker --beat --scheduler redbeat.RedBeatScheduler --max-tasks-per-child=100 -Q default,long_running --loglevel={config}` as a subprocess
    - Subprocess inherits stderr (logs go to same journal stream)
    - On `SIGTERM`/`SIGINT` to parent: send `SIGTERM` to celery subprocess, wait up to 60s for graceful shutdown, then `SIGKILL`
    - Health check: monitor subprocess, restart if it exits unexpectedly (with backoff)
    - Expose `start()`, `stop()`, `is_alive()` methods
  - Update `backend/app/__main__.py`:
    - Start `CeleryManager` before `uvicorn.run()`
    - Register signal handlers to propagate shutdown
    - Add `--no-celery` flag for development (run celery separately for auto-reload)
  - The Celery subprocess must use `--beat` flag to embed the beat scheduler (RedBeat ensures no duplicate schedules across restarts)
  - Log level for the celery subprocess should come from `settings.logging.level`

  **Must NOT do**:
  - Run Celery in-process (threads) — it must be a subprocess for isolation
  - Modify any Celery task code or routing configuration
  - Remove the ability to run Celery separately (keep `--no-celery` flag)

  **Agent profile**: `unspecified-high`
  **Depends on**: Nothing
  **Acceptance**: `python -m app` starts both uvicorn and celery. `kill <pid>` cleanly stops both. Celery processes tasks (trigger a sync via API and verify it completes).
  **Commit**: `feat: embed celery worker+beat as managed subprocess`

---

- [x] 6. Deployment Consolidation (systemd + docker-compose + dev.sh)

  **What to do**:
  - Create `packaging/systemd/barricade.service` (single unit):
    ```ini
    [Unit]
    Description=Barricade
    After=network-online.target postgresql.service redis.service
    Requires=postgresql.service redis.service

    [Service]
    Type=simple
    User=barricade
    Group=barricade
    WorkingDirectory=/usr/lib/barricade/backend
    Environment=PATH=/usr/lib/barricade/venv/bin:/usr/bin:/bin
    Environment=BARRICADE_CONFIG=/etc/barricade/barricade.toml
    ExecStart=/usr/lib/barricade/venv/bin/python -m app
    Restart=on-failure
    RestartSec=5s
    TimeoutStopSec=65
    PrivateTmp=true
    NoNewPrivileges=true
    ProtectSystem=strict
    ReadWritePaths=/var/lib/barricade /var/log/barricade /tmp /dev/shm

    [Install]
    WantedBy=multi-user.target
    ```
  - Delete old systemd units: `barricade-api.service`, `barricade-worker.service`, `barricade-beat.service`, `barricade-frontend.service`, `barricade-migrate.service`, `barricade.target`
  - Update `docker-compose.yml`:
    - Remove `frontend`, `migrate`, `celery-worker`, `celery-beat` services
    - Backend service runs `python -m app` (handles everything)
    - Keep `postgres` and `redis` services
    - Mount frontend `out/` directory (or build it into the backend image)
  - Update `dev.sh`:
    - `backend` command: run `python -m app --reload --no-celery` (uvicorn with auto-reload)
    - Run celery separately in dev for independent reload: `celery -A app.tasks worker --beat ...`
    - `frontend` command: run `npm run dev` (for hot-reload during development)
    - In dev mode, frontend dev server (port 3000) proxies to backend (port 8000) as before — static serving is for production only
  - Update `barricade.toml` docs: remove `frontend_port` from `[server]` section (no longer needed in production)

  **Must NOT do**:
  - Break the development workflow (frontend hot-reload must still work)
  - Remove the ability to run components separately for debugging

  **Agent profile**: `unspecified-high`
  **Depends on**: T3 (static serving), T4 (startup migrations), T5 (celery subprocess)
  **Acceptance**: `systemctl start barricade` brings up full stack. `systemctl stop barricade` cleanly stops everything. `journalctl -u barricade` shows API + Celery logs.
  **Commit**: `feat: consolidate into single barricade.service`

---

- [x] 7. Packaging Update

  **What to do**:
  - Update `packaging/Makefile`:
    - Remove Node.js download/bundle step
    - Add `npm run build` step that produces `frontend/out/`
    - Copy `frontend/out/` into staging directory (instead of standalone server)
    - Remove `node/` directory from staging
  - Update `packaging/nfpm.yaml`:
    - Remove Node.js binary from contents
    - Add `frontend/out/` static files to `/usr/lib/barricade/frontend/out/`
    - Remove old systemd units, add single `barricade.service`
    - Remove `barricade.target`
    - Update dependencies: remove Node.js requirement
  - Update `packaging/install.sh`:
    - Remove Node.js references
    - Update "Next steps" instructions for single service
    - Remove `barricade.target` references, use `barricade.service`
  - Update `packaging/uninstall.sh`:
    - Remove old unit file references
    - Use `barricade.service` instead of `barricade.target`
  - Update `packaging/scripts/postinst.sh`:
    - Reference `barricade.service` instead of `barricade.target`
    - Restart single service on upgrade
  - Update `packaging/scripts/prerm.sh`:
    - Stop `barricade.service` instead of `barricade.target`
  - Update `packaging/etc/barricade.toml`:
    - Remove `frontend_port` from `[server]` section
    - Add `static_dir` to `[server]` section (default auto-detect is fine for packaged installs)

  **Must NOT do**:
  - Change the package name or versioning scheme
  - Remove the tarball build target (install.sh flow)
  - Modify the config file preservation logic (`config|noreplace`)

  **Agent profile**: `unspecified-high`
  **Depends on**: T1 (static export), T6 (single service)
  **Acceptance**: `make deb` produces a .deb package. Install on clean system. `systemctl start barricade` works. Frontend loads. No Node.js on system required.
  **Commit**: `feat: update packaging for single-service architecture`

---

- [x] 8. Integration Testing + E2E Verification

  **What to do**:
  - Run full pytest suite — all existing tests must pass without changes
  - Run Playwright E2E tests against the single-service deployment
  - Verify:
    - Static frontend loads at `http://localhost:8000/`
    - Login flow works (cookie set, redirected to dashboard)
    - All dashboard pages render correctly
    - SSH terminal (WebSocket) works
    - Sync/drift operations work (Celery tasks execute)
    - Audit log records actions
    - `journalctl -u barricade` shows unified logs
  - Fix any issues found during testing

  **Must NOT do**:
  - Skip any existing test suites
  - Modify tests to make them pass (fix the code instead)

  **Agent profile**: `unspecified-high`
  **Depends on**: All previous tasks
  **Acceptance**: All pytest tests pass. All Playwright E2E tests pass. Manual smoke test on packaged install succeeds.
  **Commit**: `test: verify single-service consolidation`
