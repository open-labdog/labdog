# Non-Docker Packaging: Tarball + .deb + .rpm

## TL;DR

> **Quick Summary**: Package Barricade as a self-contained tarball and native Linux packages (.deb/.rpm) using nfpm, with systemd service management, embedded Node.js, FHS-compliant layout, and automatic database migrations on upgrade.
> 
> **Deliverables**:
> - `packaging/` directory with Makefile, nfpm.yaml, systemd units, maintainer scripts, env template
> - `make tarball` → `barricade-{version}-linux-amd64.tar.gz`
> - `make deb` → `barricade_{version}_amd64.deb`
> - `make rpm` → `barricade-{version}.x86_64.rpm`
> - Prerequisite 1-line code fix for runtime-configurable API URL
> 
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: T1 (API fix) → T5 (Makefile venv) → T9 (nfpm + package targets) → T10 (verification)

---

## Context

### Original Request
Package Barricade for deployment without Docker — both as a pre-built tarball for manual installation and as native .deb/.rpm packages.

### Interview Summary
**Key Discussions**:
- User wants both .deb (Debian/Ubuntu) and .rpm (RHEL/Fedora/Rocky) packages
- Node.js binary embedded in package (zero external Node dependency)
- FHS-compliant layout: `/usr/lib/barricade` (code), `/etc/barricade` (config), `/var/lib/barricade` (data)
- PostgreSQL and Redis remain external dependencies (user-provided)

**Research Findings**:
- nfpm generates deb+rpm from single YAML config — no dpkg-deb/rpmbuild needed
- Next.js `output: 'standalone'` produces minimal server.js + pruned node_modules
- `NEXT_PUBLIC_*` env vars are baked at build time (critical: must use relative URLs)
- Python venv shebangs must be fixed when building at staging path vs install path
- systemd `PartOf=` + target pattern provides clean group start/stop

### Metis Review
**Identified Gaps** (addressed):
- `NEXT_PUBLIC_API_URL` baked at build time → fixed by switching to relative URLs via Next.js proxy
- `ansible-runner` needs PATH to include venv/bin → set in all systemd units
- Alembic needs WorkingDirectory=/usr/lib/barricade/backend → set in migrate + backend units
- Config path resolution in pydantic Settings → EnvironmentFile takes precedence
- Don't auto-start on fresh install → enable only, user must configure env first
- Migration retry on DB not ready → Restart=on-failure with backoff

---

## Work Objectives

### Core Objective
Create a complete `packaging/` directory that builds Barricade into distributable Linux packages.

### Concrete Deliverables
- `packaging/Makefile` — Build orchestration (venv, frontend, node, tarball, deb, rpm)
- `packaging/nfpm.yaml` — Package metadata for deb/rpm generation
- `packaging/systemd/` — 6 unit files (target + 5 services)
- `packaging/scripts/` — Maintainer scripts (preinst, postinst, prerm, postrm)
- `packaging/etc/barricade.env` — Config template with documentation
- `packaging/tmpfiles.d/barricade.conf` — Runtime directory creation
- `packaging/build.sh` — One-command build script (prerequisite checks + full pipeline)
- `packaging/install.sh` — Tarball install script (bundled in tarball for manual installs)
- 1-line fix in `frontend/lib/api.ts` — Switch to relative API URLs

### Definition of Done
- [x] `make -C packaging tarball` produces installable tarball
- [x] `make -C packaging deb` produces valid .deb (dpkg-deb --info succeeds)
- [x] `make -C packaging rpm` produces valid .rpm (rpm -qip succeeds)
- [x] `packaging/build.sh` runs end-to-end and produces all 3 artifacts
- [x] Tarball contains `install.sh` and it installs successfully in a clean container
- [x] Package installs on Ubuntu 24.04 container without errors
- [x] Systemd units pass `systemd-analyze verify`
- [x] Config file preserved on upgrade (config|noreplace)

### Must Have
- All 4 application processes managed via systemd
- Database migration runs automatically before services (on upgrade)
- Config file at `/etc/barricade/barricade.env` with clear documentation
- System user `barricade` created on install
- Clean uninstall (purge removes everything)
- Embedded Node.js binary (no system nodejs dependency)
- Works on x86_64 Linux

### Must NOT Have (Guardrails)
- No reverse proxy (nginx/caddy) configuration — deployment-specific
- No TLS/HTTPS setup — reverse proxy handles this
- No PostgreSQL or Redis installation — user-provided
- No logrotate config — systemd journal handles log rotation
- No arm64 support in v1 — x86_64 only
- No APT/YUM repository setup — just produce .deb/.rpm files
- No CI/CD pipeline — Makefile is the build interface
- No auto-start on fresh install — enable only, user configures first
- No code changes outside `packaging/` and the 1-line `lib/api.ts` fix
- No dev dependencies in package venv
- No `__pycache__`, `.pyc`, `tests/`, `e2e/`, `.git` in package

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (pytest for backend)
- **Automated tests**: Tests-after (container-based install verification)
- **Framework**: Bash scripts in Docker containers

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Package builds**: Use Bash — run make targets, verify output files exist
- **Package content**: Use Bash — inspect tarball/deb/rpm contents
- **Installation**: Use Bash (Docker) — install package in container, verify paths/permissions
- **Systemd**: Use Bash — run `systemd-analyze verify` on unit files

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — 3 parallel tasks):
├── Task 1: Prerequisite code fix for API_BASE relative URLs [quick]
├── Task 2: Config template + tmpfiles.d [quick]
└── Task 3: All 6 systemd unit files [unspecified-high]

Wave 2 (Build pipeline — 3 parallel, depends on Wave 1):
├── Task 4: Makefile build-venv target + shebang fixup (depends: T1) [deep]
├── Task 5: Makefile build-frontend target (depends: T1) [unspecified-high]
└── Task 6: Makefile download-node target [quick]

Wave 3 (Packaging + scripts — depends on Wave 2):
├── Task 7: Maintainer scripts (depends: T3) [unspecified-high]
├── Task 8: nfpm.yaml + Makefile tarball/deb/rpm targets (depends: T4,T5,T6,T7) [deep]
├── Task 9: build.sh — one-command build script (depends: T8) [quick]
├── Task 10: install.sh — tarball install script (depends: T3,T7) [unspecified-high]
└── Task 11: Update MASTER-PLAN.md with packaging entry [quick]

Wave FINAL (Verification — after ALL tasks):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA — build + install test (unspecified-high)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay

Critical Path: T1 → T4 → T8 → F1-F4 → user okay
Parallel Speedup: ~50% faster than sequential
Max Concurrent: 3 (Waves 1 & 2)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | — | T4, T5 | 1 |
| T2 | — | T7 | 1 |
| T3 | — | T7, T10 | 1 |
| T4 | T1 | T8 | 2 |
| T5 | T1 | T8 | 2 |
| T6 | — | T8 | 2 |
| T7 | T2, T3 | T8, T10 | 3 |
| T8 | T4, T5, T6, T7 | T9 | 3 |
| T9 | T8 | F1-F4 | 3 |
| T10 | T3, T7 | F1-F4 | 3 |
| T11 | — | — | 3 |

### Agent Dispatch Summary

- **Wave 1**: **3** — T1 → `quick`, T2 → `quick`, T3 → `unspecified-high`
- **Wave 2**: **3** — T4 → `deep`, T5 → `unspecified-high`, T6 → `quick`
- **Wave 3**: **5** — T7 → `unspecified-high`, T8 → `deep`, T9 → `quick`, T10 → `unspecified-high`, T11 → `quick`
- **FINAL**: **4** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [x] 1. Prerequisite: Switch frontend to relative API URLs

  **What to do**:
  - Change `frontend/lib/api.ts` line 1: `export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"` → `export const API_BASE = ""`
  - This makes all browser API calls use relative URLs (`/api/...`), which the Next.js server-side proxy (`next.config.ts` rewrites) routes to the backend
  - The backend URL becomes a server-side runtime config, not a client-side build-time constant
  - Verify the dev.sh workflow still works (Next.js dev server proxies to localhost:8000)

  **Must NOT do**:
  - Change `next.config.ts` — the rewrite rules already handle proxying
  - Remove `NEXT_PUBLIC_API_URL` from next.config.ts — it's still used server-side for the proxy destination
  - Change `frontend/app/providers.tsx` — its `API_BASE` usage for `/users/me` and `/auth/jwt/logout` will also benefit from relative URLs

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Tasks 4, 5
  - **Blocked By**: None

  **References**:
  - `frontend/lib/api.ts:1` — The line to change. Currently exports absolute URL fallback.
  - `frontend/next.config.ts:7-14` — Server-side rewrite rules that proxy `/api/:path*` to backend. These use `NEXT_PUBLIC_API_URL` at runtime (server-side), so the proxy still works.
  - `frontend/app/providers.tsx:16,36` — Uses `API_BASE` for auth calls. Will use relative URLs after fix.
  - `frontend/middleware.ts` — Auth middleware. Unaffected by this change (uses cookies, not API calls).

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: Frontend API calls use relative URLs
    Tool: Bash (grep)
    Steps:
      1. grep -n "API_BASE" frontend/lib/api.ts
      2. Verify line 1 contains: export const API_BASE = ""
      3. grep -rn "NEXT_PUBLIC_API_URL" frontend/next.config.ts
      4. Verify next.config.ts still reads the env var for server-side proxy
    Expected Result: api.ts exports empty string, next.config.ts unchanged
    Evidence: .sisyphus/evidence/task-1-relative-urls.txt

  Scenario: Frontend build succeeds
    Tool: Bash
    Steps:
      1. cd frontend && npx next build
      2. Verify exit code 0
      3. Verify .next/standalone/server.js exists
    Expected Result: Build completes without errors
    Evidence: .sisyphus/evidence/task-1-frontend-build.txt
  ```

  **Commit**: YES
  - Message: `fix(frontend): use relative API URLs for non-Docker deployment`
  - Files: `frontend/lib/api.ts`
  - Pre-commit: `cd frontend && npx next lint`

- [x] 2. Create config template and tmpfiles.d

  **What to do**:
  - Create `packaging/etc/barricade.env` — env template with ALL variables documented:
    - `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `ENCRYPTION_KEY`, `BARRICADE_SERVER_IP`
    - `ALLOWED_ORIGINS`, `DRIFT_CHECK_INTERVAL_MINUTES`
    - `NEXT_PUBLIC_API_URL` (server-side proxy destination)
    - Include generation commands: `openssl rand -base64 32` for SECRET_KEY and ENCRYPTION_KEY
    - Mark required vs optional, document defaults
  - Create `packaging/tmpfiles.d/barricade.conf`:
    ```
    d /run/barricade 0755 barricade barricade -
    ```

  **Must NOT do**:
  - Include real secrets or passwords
  - Include PostgreSQL/Redis setup instructions (out of scope)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: Task 7
  - **Blocked By**: None

  **References**:
  - `backend/app/config.py` — All Pydantic Settings fields with defaults. Source of truth for env vars.
  - `backend/app/main.py:37-39` — `ALLOWED_ORIGINS` env var (not in Pydantic Settings, uses os.environ directly).
  - `backend/app/auth/users.py:26` — Cookie security setting (hardcoded False, document as production override).
  - `docker-compose.yml:51-56` — Docker env var patterns to match.
  - `frontend/next.config.ts:3` — `NEXT_PUBLIC_API_URL` used for server-side proxy.

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: Env template contains all required variables
    Tool: Bash (grep)
    Steps:
      1. grep "DATABASE_URL" packaging/etc/barricade.env
      2. grep "REDIS_URL" packaging/etc/barricade.env
      3. grep "SECRET_KEY" packaging/etc/barricade.env
      4. grep "ENCRYPTION_KEY" packaging/etc/barricade.env
      5. grep "BARRICADE_SERVER_IP" packaging/etc/barricade.env
      6. grep "ALLOWED_ORIGINS" packaging/etc/barricade.env
      7. grep "NEXT_PUBLIC_API_URL" packaging/etc/barricade.env
      8. grep "openssl rand" packaging/etc/barricade.env
    Expected Result: All variables present with documentation and generation commands
    Evidence: .sisyphus/evidence/task-2-env-template.txt

  Scenario: tmpfiles.d config is valid
    Tool: Bash
    Steps:
      1. cat packaging/tmpfiles.d/barricade.conf
      2. Verify contains "d /run/barricade 0755 barricade barricade -"
    Expected Result: Valid tmpfiles.d entry
    Evidence: .sisyphus/evidence/task-2-tmpfiles.txt
  ```

  **Commit**: YES (groups with T3)
  - Message: `packaging: add systemd units, env template, tmpfiles config`
  - Files: `packaging/etc/barricade.env`, `packaging/tmpfiles.d/barricade.conf`

- [x] 3. Create all systemd unit files

  **What to do**:
  - Create `packaging/systemd/barricade.target`:
    - `After=network-online.target`, `Wants=network-online.target`
    - `WantedBy=multi-user.target` in [Install]
  - Create `packaging/systemd/barricade-migrate.service`:
    - `Type=oneshot`, `RemainAfterExit=yes`
    - `ExecStart=/usr/lib/barricade/venv/bin/alembic upgrade head`
    - `WorkingDirectory=/usr/lib/barricade/backend`
    - `Restart=on-failure`, `RestartSec=10s`, `StartLimitBurst=5` (retry if DB not ready)
    - `PartOf=barricade.target`
  - Create `packaging/systemd/barricade-api.service`:
    - `ExecStart=/usr/lib/barricade/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000`
    - `WorkingDirectory=/usr/lib/barricade/backend`
    - `After=barricade-migrate.service postgresql.service redis.service`
    - `PartOf=barricade.target`
  - Create `packaging/systemd/barricade-worker.service`:
    - `ExecStart=/usr/lib/barricade/venv/bin/celery -A app.tasks worker --max-tasks-per-child=100 -Q default,long_running --loglevel=info`
    - `WorkingDirectory=/usr/lib/barricade/backend`
    - `After=barricade-migrate.service`, `PartOf=barricade.target`
  - Create `packaging/systemd/barricade-beat.service`:
    - `ExecStart=/usr/lib/barricade/venv/bin/celery -A app.tasks beat --scheduler redbeat.RedBeatScheduler --loglevel=info`
    - `WorkingDirectory=/usr/lib/barricade/backend`
    - `After=barricade-worker.service redis.service`, `PartOf=barricade.target`
  - Create `packaging/systemd/barricade-frontend.service`:
    - `ExecStart=/usr/lib/barricade/node/bin/node /usr/lib/barricade/frontend/server.js`
    - `Environment=NODE_ENV=production PORT=3000 HOSTNAME=127.0.0.1`
    - `After=barricade-api.service`, `PartOf=barricade.target`
  - ALL backend services MUST include:
    - `EnvironmentFile=/etc/barricade/barricade.env`
    - `Environment=PATH=/usr/lib/barricade/venv/bin:/usr/bin:/bin`
    - `User=barricade`, `Group=barricade`
    - Security hardening: `PrivateTmp=true`, `NoNewPrivileges=true`, `ProtectSystem=strict`
    - `ReadWritePaths=/var/lib/barricade /var/log/barricade /tmp /dev/shm`
    - `Restart=on-failure`, `RestartSec=5s`

  **Must NOT do**:
  - Use `Type=forking` with `--detach` — keep services in foreground for journald
  - Bind API to 0.0.0.0 — use 127.0.0.1, let reverse proxy handle external access
  - Include nginx/caddy unit files

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Task 7
  - **Blocked By**: None

  **References**:
  - `docker-compose.yml:50,70,88,109` — Exact process commands to match (same flags, queues, arguments).
  - `dev.sh:81,87-88,94-95` — Dev script process commands (same pattern, but with --reload for dev).
  - `backend/app/tasks/__init__.py` — Celery app definition, queue config, routing.
  - `backend/app/config.py` — Pydantic Settings reads from env vars + .env file.
  - `frontend/next.config.ts:6` — `output: 'standalone'` generates server.js.

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: All 6 systemd units exist and parse correctly
    Tool: Bash
    Steps:
      1. ls packaging/systemd/ — expect 6 files
      2. For each .service file: grep "PartOf=barricade.target"
      3. For each backend service: grep "EnvironmentFile=/etc/barricade/barricade.env"
      4. For each backend service: grep "PATH=/usr/lib/barricade/venv/bin"
      5. grep "RemainAfterExit=yes" packaging/systemd/barricade-migrate.service
      6. grep "Type=oneshot" packaging/systemd/barricade-migrate.service
    Expected Result: All units present with correct directives
    Evidence: .sisyphus/evidence/task-3-systemd-units.txt

  Scenario: Units pass systemd-analyze verify (if available)
    Tool: Bash
    Steps:
      1. systemd-analyze verify packaging/systemd/*.service 2>&1 || echo "systemd-analyze not available"
    Expected Result: No errors (warnings about missing targets are ok since not installed)
    Evidence: .sisyphus/evidence/task-3-systemd-verify.txt
  ```

  **Commit**: YES (groups with T2)
  - Message: `packaging: add systemd units, env template, tmpfiles config`
  - Files: `packaging/systemd/*`, `packaging/etc/*`, `packaging/tmpfiles.d/*`

- [x] 4. Makefile: build-venv target with shebang fixup

  **What to do**:
  - Create `packaging/Makefile` with variables: `VERSION`, `ARCH`, `PYTHON`, `NODE_VERSION`, `DESTDIR`, `PREFIX=/usr/lib/barricade`
  - `build-venv` target:
    1. Create venv at `$(DESTDIR)$(PREFIX)/venv` using system `python3.12 -m venv`
    2. Install backend package: `$(DESTDIR)$(PREFIX)/venv/bin/pip install --no-cache-dir ../backend/`
    3. Exclude dev dependencies (no `[dev]` extras)
    4. Fix shebangs: `sed -i "s|$(DESTDIR)||g" $(DESTDIR)$(PREFIX)/venv/bin/*`
    5. Fix `pyvenv.cfg`: update `home =` to point to system Python location (not staging dir)
    6. Copy `backend/` source (app/, alembic/, alembic.ini) to `$(DESTDIR)$(PREFIX)/backend/`
    7. Strip `__pycache__`, `.pyc`, `tests/` from venv site-packages
    8. Verify `ansible-playbook` is available in venv bin/
  - Add `clean` target to remove `$(DESTDIR)` and `dist/`
  - Add `help` target listing all available targets

  **Must NOT do**:
  - Install dev dependencies (`pytest`, `ruff`, `testcontainers`, `httpx`)
  - Use `uv` (not guaranteed on build host) — use standard pip
  - Hardcode Python path — use `$(PYTHON)` variable

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6)
  - **Blocks**: Task 8
  - **Blocked By**: Task 1

  **References**:
  - `backend/pyproject.toml` — Package definition, dependencies, Python version requirement.
  - `backend/Dockerfile:5-6` — Docker build approach (uv pip install fallback to pip install).
  - `backend/app/config.py:6` — `Path(__file__).resolve().parents[2] / ".env"` — explains why WorkingDirectory matters.
  - `backend/alembic.ini:6` — `script_location = alembic` (relative path, needs correct cwd).

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: Venv builds and shebangs are correct
    Tool: Bash
    Steps:
      1. make -C packaging build-venv DESTDIR=/tmp/barricade-test
      2. head -1 /tmp/barricade-test/usr/lib/barricade/venv/bin/celery
      3. Verify shebang is "#!/usr/lib/barricade/venv/bin/python3" (no DESTDIR prefix)
      4. grep "^home" /tmp/barricade-test/usr/lib/barricade/venv/bin/pyvenv.cfg
      5. Verify home points to system Python, not staging dir
      6. ls /tmp/barricade-test/usr/lib/barricade/venv/bin/ansible-playbook
    Expected Result: Venv built, shebangs clean, ansible-playbook present
    Evidence: .sisyphus/evidence/task-4-venv-build.txt

  Scenario: No dev dependencies in venv
    Tool: Bash
    Steps:
      1. /tmp/barricade-test/usr/lib/barricade/venv/bin/pip list --format=columns
      2. Verify "pytest" NOT in output
      3. Verify "ruff" NOT in output
      4. Verify "fastapi" IS in output
    Expected Result: Only production dependencies installed
    Evidence: .sisyphus/evidence/task-4-no-dev-deps.txt
  ```

  **Commit**: YES
  - Message: `packaging: add Makefile with venv, frontend, and node build targets`
  - Files: `packaging/Makefile`

- [x] 5. Makefile: build-frontend target

  **What to do**:
  - Add `build-frontend` target to `packaging/Makefile`:
    1. `cd ../frontend && npm ci && npm run build`
    2. `mkdir -p $(DESTDIR)$(PREFIX)/frontend`
    3. `cp -r ../frontend/.next/standalone/. $(DESTDIR)$(PREFIX)/frontend/`
    4. `cp -r ../frontend/public $(DESTDIR)$(PREFIX)/frontend/`
    5. `cp -r ../frontend/.next/static $(DESTDIR)$(PREFIX)/frontend/.next/`
  - The standalone output includes server.js and pruned node_modules
  - `public/` and `.next/static/` must be copied separately (Next.js standalone doesn't include them)

  **Must NOT do**:
  - Include `node_modules/` from the source tree (standalone output has its own pruned copy)
  - Include dev files: `e2e/`, `.env.local`, `tsconfig.json`, source `.tsx` files

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 6)
  - **Blocks**: Task 8
  - **Blocked By**: Task 1

  **References**:
  - `frontend/Dockerfile:14-16` — Docker build copies: `public/`, `.next/standalone/`, `.next/static/`. Follow this exact pattern.
  - `frontend/next.config.ts:6` — `output: 'standalone'` config that enables standalone build.
  - `frontend/package.json:6-7` — `build` and `start` scripts.

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: Frontend standalone output is complete
    Tool: Bash
    Steps:
      1. make -C packaging build-frontend DESTDIR=/tmp/barricade-test
      2. test -f /tmp/barricade-test/usr/lib/barricade/frontend/server.js
      3. test -d /tmp/barricade-test/usr/lib/barricade/frontend/public
      4. test -d /tmp/barricade-test/usr/lib/barricade/frontend/.next/static
      5. test -d /tmp/barricade-test/usr/lib/barricade/frontend/node_modules
    Expected Result: All standalone artifacts present
    Evidence: .sisyphus/evidence/task-5-frontend-build.txt
  ```

  **Commit**: YES (groups with T4)
  - Message: `packaging: add Makefile with venv, frontend, and node build targets`
  - Files: `packaging/Makefile`

- [x] 6. Makefile: download-node target

  **What to do**:
  - Add `download-node` target to `packaging/Makefile`:
    1. Set `NODE_VERSION ?= 20.18.1` (latest 20 LTS at time of writing)
    2. Download `https://nodejs.org/dist/v$(NODE_VERSION)/node-v$(NODE_VERSION)-linux-x64.tar.xz`
    3. Extract `bin/node` binary only to `$(DESTDIR)$(PREFIX)/node/bin/node`
    4. Strip debug symbols: `strip $(DESTDIR)$(PREFIX)/node/bin/node` (reduces ~90MB → ~40MB)
    5. Verify the binary runs: `$(DESTDIR)$(PREFIX)/node/bin/node --version`
  - Only extract `bin/node` — we don't need npm, npx, or the full Node.js distribution

  **Must NOT do**:
  - Include npm/npx (not needed at runtime — frontend is pre-built)
  - Download arm64 binary (x86_64 only for v1)
  - Use the system package manager to install Node

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5)
  - **Blocks**: Task 8
  - **Blocked By**: None

  **References**:
  - `frontend/Dockerfile:1` — `FROM node:20-alpine` — confirms Node 20 is the target version.
  - Node.js releases: `https://nodejs.org/dist/` — official binary distribution.

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: Node binary downloaded and functional
    Tool: Bash
    Steps:
      1. make -C packaging download-node DESTDIR=/tmp/barricade-test
      2. /tmp/barricade-test/usr/lib/barricade/node/bin/node --version
      3. Verify output starts with "v20."
      4. file /tmp/barricade-test/usr/lib/barricade/node/bin/node | grep "ELF 64-bit"
    Expected Result: Node 20.x binary present and executable
    Evidence: .sisyphus/evidence/task-6-node-download.txt
  ```

  **Commit**: YES (groups with T4, T5)
  - Message: `packaging: add Makefile with venv, frontend, and node build targets`
  - Files: `packaging/Makefile`

- [x] 7. Create maintainer scripts (preinst, postinst, prerm, postrm)

  **What to do**:
  - Create `packaging/scripts/preinst.sh`:
    - Create `barricade` system user + group if not exists (`adduser --system --group --no-create-home --home /var/lib/barricade --shell /usr/sbin/nologin`)
  - Create `packaging/scripts/postinst.sh`:
    - Fix ownership: `chown -R barricade:barricade /var/lib/barricade /var/log/barricade`
    - `systemctl daemon-reload`
    - On fresh install: Print instructions to edit env file and enable services
    - On upgrade: Run migration (`systemctl start barricade-migrate.service`), restart active services
    - `systemd-tmpfiles --create barricade.conf` for /run/barricade
  - Create `packaging/scripts/prerm.sh`:
    - Stop and disable `barricade.target` on remove
  - Create `packaging/scripts/postrm.sh`:
    - On purge: Remove user, remove /var/lib/barricade, /var/log/barricade, /etc/barricade
    - On remove: `systemctl daemon-reload`
  - All scripts must: `set -e`, use `case "$1" in` for dpkg action types, be POSIX sh compatible

  **Must NOT do**:
  - Auto-start services on fresh install (enable only is also not done — user must explicitly enable)
  - Remove config on regular remove (only on purge)
  - Assume PostgreSQL/Redis are running during install

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 8, 9)
  - **Blocks**: Task 8
  - **Blocked By**: Tasks 2, 3

  **References**:
  - Metis review maintainer script patterns — See preinst/postinst/prerm/postrm patterns from research.
  - `docker-compose.yml:34` — Migration command: `alembic upgrade head`.
  - Debian Policy Manual §6 — Package maintainer scripts conventions.

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: Scripts are valid POSIX shell
    Tool: Bash
    Steps:
      1. shellcheck -s sh packaging/scripts/preinst.sh
      2. shellcheck -s sh packaging/scripts/postinst.sh
      3. shellcheck -s sh packaging/scripts/prerm.sh
      4. shellcheck -s sh packaging/scripts/postrm.sh
    Expected Result: No errors (warnings acceptable)
    Evidence: .sisyphus/evidence/task-7-shellcheck.txt

  Scenario: Scripts handle all dpkg action types
    Tool: Bash (grep)
    Steps:
      1. grep 'case.*"$1"' packaging/scripts/preinst.sh
      2. grep 'install\|upgrade' packaging/scripts/preinst.sh
      3. grep 'configure' packaging/scripts/postinst.sh
      4. grep 'remove\|upgrade' packaging/scripts/prerm.sh
      5. grep 'purge' packaging/scripts/postrm.sh
    Expected Result: All scripts handle correct dpkg action types
    Evidence: .sisyphus/evidence/task-7-actions.txt
  ```

  **Commit**: YES (groups with T8)
  - Message: `packaging: add nfpm config, maintainer scripts, and package targets`
  - Files: `packaging/scripts/*`

- [x] 8. Create nfpm.yaml and Makefile package targets

  **What to do**:
  - Create `packaging/nfpm.yaml`:
    - Package name: `barricade`, arch: `amd64`, version from `$(VERSION)`
    - Dependencies: `python3 (>= 3.12)` (deb), `python3.12` (rpm)
    - Recommends: `postgresql-client`, `redis-tools`
    - Contents: map staging tree to FHS paths (usr/lib/barricade/*, etc/barricade/*, systemd units, tmpfiles.d)
    - Config files: `type: config|noreplace` for `/etc/barricade/barricade.env`
    - Dir entries for `/usr/lib/barricade`, `/etc/barricade`, `/var/lib/barricade`, `/var/log/barricade`
    - Scripts: point to `packaging/scripts/` maintainer scripts
    - deb compression: zstd
  - Add Makefile targets:
    - `build`: depends on build-venv, build-frontend, download-node
    - `tarball`: create `dist/barricade-$(VERSION)-linux-amd64.tar.gz` from staging tree + packaging files
    - `deb`: `VERSION=$(VERSION) nfpm pkg --packager deb --target dist/`
    - `rpm`: `VERSION=$(VERSION) nfpm pkg --packager rpm --target dist/`
    - `all`: tarball + deb + rpm

  **Must NOT do**:
  - Declare PostgreSQL or Redis as hard dependencies (they're external)
  - Include CI/CD configuration
  - Add arm64 architecture support

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (end of Wave 3)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 4, 5, 6, 7

  **References**:
  - nfpm documentation — `https://nfpm.goreleaser.com/configuration/` for YAML schema.
  - `backend/pyproject.toml:5-6` — Package name and version.
  - Metis review nfpm.yaml patterns — Full nfpm.yaml template from research.
  - `docker-compose.yml` — Reference for service names, ports, env vars.

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: deb package is valid
    Tool: Bash
    Steps:
      1. make -C packaging deb VERSION=0.1.0
      2. dpkg-deb --info dist/barricade_0.1.0_amd64.deb
      3. dpkg-deb --contents dist/barricade_0.1.0_amd64.deb | grep "usr/lib/barricade/venv/bin/python3"
      4. dpkg-deb --contents dist/barricade_0.1.0_amd64.deb | grep "usr/lib/barricade/frontend/server.js"
      5. dpkg-deb --contents dist/barricade_0.1.0_amd64.deb | grep "usr/lib/barricade/node/bin/node"
      6. dpkg-deb --contents dist/barricade_0.1.0_amd64.deb | grep "etc/barricade/barricade.env"
      7. dpkg-deb --contents dist/barricade_0.1.0_amd64.deb | grep "barricade-api.service"
    Expected Result: Valid .deb with all expected contents
    Evidence: .sisyphus/evidence/task-8-deb-package.txt

  Scenario: rpm package is valid
    Tool: Bash
    Steps:
      1. make -C packaging rpm VERSION=0.1.0
      2. rpm -qip dist/barricade-0.1.0-1.x86_64.rpm
      3. rpm -qlp dist/barricade-0.1.0-1.x86_64.rpm | grep "usr/lib/barricade/venv/bin/python3"
    Expected Result: Valid .rpm with correct metadata
    Evidence: .sisyphus/evidence/task-8-rpm-package.txt

  Scenario: tarball contains install instructions
    Tool: Bash
    Steps:
      1. make -C packaging tarball VERSION=0.1.0
      2. tar -tf dist/barricade-0.1.0-linux-amd64.tar.gz | head -20
      3. Verify contains usr/lib/barricade/, etc/barricade/, systemd units
    Expected Result: Tarball has FHS-compliant structure
    Evidence: .sisyphus/evidence/task-8-tarball.txt
  ```

  **Commit**: YES (groups with T7)
  - Message: `packaging: add nfpm config, maintainer scripts, and package targets`
  - Files: `packaging/nfpm.yaml`, `packaging/Makefile` (additions)

- [x] 9. Create build.sh — one-command build script

  **What to do**:
  - Create `packaging/build.sh` — a single entry point that:
    1. Checks prerequisites: `python3.12`, `pip`, `npm`, `curl`, `tar`, `nfpm` — prints clear error for each missing tool with install instructions
    2. Accepts `--version=X.Y.Z` flag (defaults to reading from `backend/pyproject.toml`)
    3. Accepts `--target=all|tarball|deb|rpm` flag (defaults to `all`)
    4. Runs `make build` (venv + frontend + node download)
    5. Runs the selected package targets
    6. Prints summary: file paths, sizes, SHA256 checksums of produced artifacts
  - Script should be idempotent (re-running produces same output)
  - Script should `set -euo pipefail` and provide clear progress output with `[step N/M]` prefixes
  - Include `--help` flag documenting all options
  - Include `--clean` flag to wipe staging dir and dist/ before building

  **Must NOT do**:
  - Install prerequisites automatically (just detect and print instructions)
  - Require root/sudo to run
  - Push packages anywhere (no upload/publish)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (after T8)
  - **Blocks**: F1-F4
  - **Blocked By**: Task 8

  **References**:
  - `packaging/Makefile` — All build targets this script wraps.
  - `dev.sh` — Follow the same script conventions: `set -euo pipefail`, `log()` helper, usage function.
  - `backend/pyproject.toml:6` — Version string: `version = "0.1.0"`.

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: build.sh detects missing prerequisites
    Tool: Bash
    Steps:
      1. PATH=/usr/bin packaging/build.sh --target=tarball 2>&1 || true
      2. Verify output contains "missing" or "not found" for at least nfpm
    Expected Result: Clear error message with install instructions for missing tools
    Evidence: .sisyphus/evidence/task-9-prereq-check.txt

  Scenario: build.sh --help prints usage
    Tool: Bash
    Steps:
      1. packaging/build.sh --help
      2. Verify output contains --version, --target, --clean flags
    Expected Result: Usage documentation printed
    Evidence: .sisyphus/evidence/task-9-help.txt

  Scenario: build.sh produces all artifacts (if prerequisites met)
    Tool: Bash
    Steps:
      1. packaging/build.sh --version=0.1.0 --target=all
      2. ls -lh dist/barricade-*
      3. Verify .tar.gz, .deb, and .rpm files exist
      4. Verify SHA256 checksums printed in output
    Expected Result: All 3 artifacts produced with checksums
    Evidence: .sisyphus/evidence/task-9-full-build.txt
  ```

  **Commit**: YES (groups with T8)
  - Message: `packaging: add nfpm config, maintainer scripts, and package targets`
  - Files: `packaging/build.sh`

- [x] 10. Create install.sh — tarball install script

  **What to do**:
  - Create `packaging/install.sh` that is **included inside the tarball** and handles manual installation:
    1. Must run as root (check and exit with message if not)
    2. Create `barricade` system user + group (same logic as preinst.sh)
    3. Copy files to FHS locations:
       - `usr/lib/barricade/` → `/usr/lib/barricade/`
       - `etc/barricade/` → `/etc/barricade/` (skip if exists — don't overwrite config)
       - `systemd/` → `/usr/lib/systemd/system/`
       - `tmpfiles.d/` → `/usr/lib/tmpfiles.d/`
    4. Fix ownership: `chown -R barricade:barricade /var/lib/barricade /var/log/barricade`
    5. Create directories: `/var/lib/barricade`, `/var/log/barricade`, `/run/barricade`
    6. Run `systemctl daemon-reload`
    7. Print post-install instructions:
       - Edit `/etc/barricade/barricade.env`
       - Run `systemctl enable --now barricade.target`
       - Verify with `systemctl status barricade.target`
  - Also create an `uninstall.sh`:
    1. Stop and disable `barricade.target`
    2. Remove files from all FHS locations
    3. Optionally remove user (with `--purge` flag)
    4. Optionally remove config and data (with `--purge` flag)
  - Both scripts: `set -euo pipefail`, clear progress output, `--help` flag
  - The Makefile `tarball` target must include `install.sh` and `uninstall.sh` in the tarball root

  **Must NOT do**:
  - Auto-start services (just enable and print instructions)
  - Run database migrations (user must start PostgreSQL first)
  - Overwrite existing `/etc/barricade/barricade.env` (preserve admin config)
  - Remove user data on regular uninstall (only with --purge)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 7, 8, 9, 11)
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 3, 7

  **References**:
  - `packaging/scripts/preinst.sh` — User creation logic to reuse (same `adduser` command).
  - `packaging/scripts/postinst.sh` — Post-install steps to mirror (ownership, daemon-reload, instructions).
  - `packaging/scripts/prerm.sh` — Stop/disable logic to reuse in uninstall.sh.
  - `packaging/scripts/postrm.sh` — Purge logic to reuse in uninstall.sh --purge.
  - `packaging/systemd/` — Service unit file paths to install.
  - `packaging/etc/barricade.env` — Config file path to check before overwriting.

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: install.sh rejects non-root execution
    Tool: Bash
    Steps:
      1. Run as non-root user: packaging/install.sh 2>&1 || true
      2. Verify output contains "root" or "sudo"
    Expected Result: Clear error about requiring root
    Evidence: .sisyphus/evidence/task-10-nonroot.txt

  Scenario: install.sh deploys all files correctly in container
    Tool: Bash (Docker)
    Steps:
      1. Extract tarball in Ubuntu 24.04 container
      2. Run ./install.sh
      3. test -d /usr/lib/barricade/venv/bin
      4. test -f /usr/lib/barricade/frontend/server.js
      5. test -f /usr/lib/barricade/node/bin/node
      6. test -f /etc/barricade/barricade.env
      7. test -f /usr/lib/systemd/system/barricade.target
      8. getent passwd barricade
      9. stat -c "%U:%G" /var/lib/barricade — expect barricade:barricade
    Expected Result: All files in place, user created, correct ownership
    Evidence: .sisyphus/evidence/task-10-install.txt

  Scenario: install.sh preserves existing config
    Tool: Bash (Docker)
    Steps:
      1. In container: mkdir -p /etc/barricade && echo "SECRET_KEY=custom" > /etc/barricade/barricade.env
      2. Run ./install.sh
      3. grep "custom" /etc/barricade/barricade.env
    Expected Result: Existing config NOT overwritten
    Evidence: .sisyphus/evidence/task-10-preserve-config.txt

  Scenario: uninstall.sh removes files cleanly
    Tool: Bash (Docker)
    Steps:
      1. Run ./install.sh then ./uninstall.sh
      2. test ! -d /usr/lib/barricade
      3. test -f /etc/barricade/barricade.env (config preserved without --purge)
      4. Run ./uninstall.sh --purge
      5. test ! -d /etc/barricade
    Expected Result: Clean removal, purge removes everything
    Evidence: .sisyphus/evidence/task-10-uninstall.txt
  ```

  **Commit**: YES
  - Message: `packaging: add install.sh and uninstall.sh for tarball deployment`
  - Files: `packaging/install.sh`, `packaging/uninstall.sh`

- [x] 11. Update MASTER-PLAN.md with packaging entry

  **What to do**:
  - Add row to Implementation Plans table in `.sisyphus/plans/MASTER-PLAN.md`:
    - `| 12 | **Packaging** | packaging.md | M | 📋 Planned | Pre-built tarball, .deb, .rpm via nfpm. Systemd services. FHS layout. |`
  - Add to Ideas table if not already present: "APT/YUM repository hosting"

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 7, 8, 9, 10)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `.sisyphus/plans/MASTER-PLAN.md:25-38` — Implementation Plans table to update.

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: MASTER-PLAN.md updated
    Tool: Bash (grep)
    Steps:
      1. grep "Packaging" .sisyphus/plans/MASTER-PLAN.md
      2. Verify row exists with packaging.md link
    Expected Result: Packaging entry present in master plan table
    Evidence: .sisyphus/evidence/task-11-master-plan.txt
  ```

  **Commit**: YES
  - Message: `docs: add packaging entry to master plan`
  - Files: `.sisyphus/plans/MASTER-PLAN.md`

---

## Final Verification Wave

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists. For each "Must NOT Have": search codebase for forbidden patterns. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run shellcheck on all .sh scripts. Validate nfpm.yaml syntax. Verify Makefile targets are idempotent. Check for hardcoded paths that should be variables. Review systemd units for security hardening.
  Output: `Shellcheck [PASS/FAIL] | nfpm [PASS/FAIL] | Makefile [N clean/N issues] | VERDICT`

- [x] F3. **Real Manual QA** — `unspecified-high`
  Build the packages locally (`make -C packaging deb rpm tarball`). Install .deb in Ubuntu 24.04 Docker container. Verify all paths, permissions, systemd units, config file. Test upgrade path with config preservation.
  Output: `Build [PASS/FAIL] | Install [PASS/FAIL] | Paths [N/N] | Units [N/N] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 compliance. Check "Must NOT do" items. Detect scope creep. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Scope [CLEAN/N issues] | VERDICT`

---

## Commit Strategy

| # | Message | Files | Pre-commit |
|---|---------|-------|------------|
| 1 | `fix(frontend): use relative API URLs for non-Docker deployment` | `frontend/lib/api.ts` | `cd frontend && npx next lint` |
| 2 | `packaging: add systemd units, env template, tmpfiles config` | `packaging/systemd/*`, `packaging/etc/*`, `packaging/tmpfiles.d/*` | `systemd-analyze verify packaging/systemd/*.service` |
| 3 | `packaging: add Makefile with venv, frontend, and node build targets` | `packaging/Makefile` | `make -C packaging help` |
| 4 | `packaging: add nfpm config, maintainer scripts, build.sh, and package targets` | `packaging/nfpm.yaml`, `packaging/scripts/*`, `packaging/build.sh` | `packaging/build.sh --help` |
| 5 | `packaging: add install.sh and uninstall.sh for tarball deployment` | `packaging/install.sh`, `packaging/uninstall.sh` | `shellcheck packaging/install.sh packaging/uninstall.sh` |

---

## Success Criteria

### Verification Commands
```bash
packaging/build.sh --version=0.1.0  # Expected: produces all 3 artifacts with checksums
make -C packaging tarball  # Expected: dist/barricade-*.tar.gz exists
make -C packaging deb      # Expected: dist/barricade_*_amd64.deb exists
make -C packaging rpm      # Expected: dist/barricade-*.x86_64.rpm exists
dpkg-deb --info dist/barricade_*_amd64.deb  # Expected: valid package info
rpm -qip dist/barricade-*.x86_64.rpm        # Expected: valid package info
tar -tf dist/barricade-*.tar.gz | grep install.sh  # Expected: install.sh in tarball root
```

### Final Checklist
- [x] All "Must Have" present
- [x] All "Must NOT Have" absent
- [x] Package installs cleanly on Ubuntu 24.04
- [x] Systemd units pass verification
- [x] Config preserved on upgrade

---

## CI/CD Integration

GitLab CI has been added to automate the package build process. The `.gitlab-ci.yml` pipeline includes a `package` stage that runs on tagged releases, invoking `packaging/build.sh` to produce tarball, .deb, and .rpm artifacts. A `release` stage then creates a GitLab release with download links for all three artifacts. Manual builds via `packaging/build.sh` still work as documented above.
