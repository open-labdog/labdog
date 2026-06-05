# LabDog Documentation

Guides and reference material for running LabDog. For a project
summary see the [top-level README](https://github.com/open-labdog/labdog/blob/main/README.md).

## Contents

- [Documentation index](#documentation-index)
- [How Configuration Is Applied](#how-configuration-is-applied)
  - [Groups, Hosts, and Priority](#groups-hosts-and-priority)
  - [Host-Level Overrides](#host-level-overrides)
  - [How Sync Applies to the Remote Host](#how-sync-applies-to-the-remote-host)
  - [Automatic Safety Rules](#automatic-safety-rules)
- [Architecture](#architecture)
- [Installation](#installation)
  - [From Package (Recommended)](#from-package-recommended)
  - [From Tarball](#from-tarball)
  - [Verifying Downloads](#verifying-downloads)
  - [Post-install Configuration](#post-install-configuration)
  - [Uninstalling](#uninstalling)
- [Quick Start (Docker)](#quick-start-docker)
- [Environment Variables](#environment-variables)
- [Local Development](#local-development)
- [Testing](#testing)
- [API Endpoints](#api-endpoints)
- [Project Structure](#project-structure)
- [Extension Modules](#extension-modules)
- [Known Limitations](#known-limitations)
- [CI/CD](#cicd)

## Documentation index

### UI Guide

| Section | What it covers |
|---|---|
| [ui/](./ui/README.md) | Overview of every page in the LabDog web interface |
| [ui/dashboard.md](./ui/dashboard.md) | Fleet overview — metric cards, host table, Collect State |
| [ui/hosts.md](./ui/hosts.md) | Host management, discovery scanning, SSH terminal |
| [ui/groups.md](./ui/groups.md) | Groups, all module tabs (firewall, services, packages, /etc/hosts, cron, users, DNS, Firewall Sync) |
| [ui/gitops-ui.md](./ui/gitops-ui.md) | Git repo connections, enabling GitOps on a group, webhook setup, import flow |
| [ui/scheduled-actions.md](./ui/scheduled-actions.md) | Schedules — cron-driven runs of any action (built-in or pack-supplied) against hosts, groups, or the fleet |
| [ui/actions.md](./ui/actions.md) | Actions and Action Packs — ad-hoc playbook runs and bring-your-own playbooks |
| [ui/admin.md](./ui/admin.md) | SSH Keys, Audit Log, User management |
| [ui/settings.md](./ui/settings.md) | All settings configurable in the UI (log level, drift interval, timeouts, discovery tuning) |

### Configuration Examples

| Section | What it covers |
|---|---|
| [examples/gitops/](./examples/gitops/README.md) | End-to-end GitOps guide — webhook setup, YAML schema reference for every module, missing-section semantics, idempotency rules, error taxonomy, mutation-lock behaviour, multi-group repo layouts, break-glass recovery. |
| [examples/gitops/minimal.yaml](./examples/gitops/minimal.yaml) | Smallest valid group YAML — a starting template. |
| [examples/gitops/web-servers.yaml](./examples/gitops/web-servers.yaml) | Realistic web-tier example covering all seven modules. |
| [examples/gitops/database.yaml](./examples/gitops/database.yaml) | Realistic PostgreSQL-tier example with a declared apt repository, per-user cron backups, and different resolver backend. |
| [examples/gitops/modules/](./examples/gitops/modules/) | One focused YAML per module (firewall, services, packages, hosts-entries, cron-jobs, resolver, users) with every field annotated and edge cases demonstrated. |
| [examples/precedence/](./examples/precedence/README.md) | How LabDog merges group-level and host-level configurations when a host belongs to multiple groups. Worked examples for every module. |
| [examples/action-packs/](./examples/action-packs/README.md) | Three working examples of the action-pack format — minimal, destructive-with-parameter, and a pack that ships its own role. Drop into a git repo or local directory and add via the UI. |

### Operations

| Section | What it covers |
|---|---|
| [backup-restore.md](./backup-restore.md) | Backing up the PostgreSQL database and the encryption key, a daily-backup systemd timer, restore procedure (fresh host + point-in-time), disaster recovery for lost-key / lost-DB scenarios, and security notes for backup handling. |
| [encryption-key-rotation.md](./encryption-key-rotation.md) | When and how to rotate `security.encryption_key`: pre-rotation checklist, step-by-step procedure, rollback, and failure modes. |
| [upgrade.md](./upgrade.md) | Upgrade and rollback procedure for new releases. |
| [production-deploy.md](./production-deploy.md) | Docker Compose production reference (TLS, volumes, secrets). |
| [security-hardening.md](./security-hardening.md) | Reverse-proxy TLS, cookie/CSP/rate-limit configuration, DB security, superuser policy. |
| [trivy-rationale.md](./trivy-rationale.md) | Per-CVE rationale for entries in `.trivyignore`. |

### Where to start

- **New to LabDog?** → [ui/README.md](./ui/README.md) for a tour of the interface
- **Configuring operational settings?** → [ui/settings.md](./ui/settings.md)
- **Setting up GitOps for the first time?** → [examples/gitops/README.md](./examples/gitops/README.md)
- **Looking for a specific YAML field?** → the matching file in [examples/gitops/modules/](./examples/gitops/modules/)
- **Trying to reason about multi-group hosts?** → [examples/precedence/README.md](./examples/precedence/README.md)
- **Want to add or override action playbooks?** → [ui/actions.md](./ui/actions.md) and [examples/action-packs/README.md](./examples/action-packs/README.md)
- **Running LabDog in production?** → [backup-restore.md](./backup-restore.md) before users start entering credentials

### Authoritative sources

The YAML examples in this tree parse cleanly against the live Pydantic
schema; the schema itself lives in
[`backend/app/gitops/schema.py`](https://github.com/open-labdog/labdog/blob/main/backend/app/gitops/schema.py)
and is the source of truth. Per-module handlers are in
[`backend/app/gitops/importers/`](https://github.com/open-labdog/labdog/tree/main/backend/app/gitops/importers/).

## How Configuration Is Applied

LabDog organizes configuration through **host groups**. You define rules at the group level, assign hosts to one or more groups, and LabDog merges everything into a single effective configuration per host.

### Groups, Hosts, and Priority

Each host group has a **priority** (higher number = higher priority). When a host belongs to multiple groups, configurations are merged with higher-priority groups winning conflicts:

1. Groups are sorted by priority (highest first)
2. For each configuration item, **first occurrence wins** — the highest-priority group's version is kept, lower-priority duplicates are discarded
3. Host-level overrides **fully replace** the group-level entry for that item (available on all modules except firewall rules)

**Conflict resolution key** — what counts as "the same item":

| Module | Identity Key | Example |
|--------|-------------|---------|
| Firewall rules | Signature: protocol + direction + ports + source/dest CIDR | Two groups both defining TCP ACCEPT on port 443 from 0.0.0.0/0 — higher-priority group wins |
| Services | `service_name` | Two groups both managing `nginx` — higher-priority group's state/enabled wins |
| /etc/hosts | `ip_address` | Two groups both mapping `10.0.0.5` — higher-priority group's hostname wins |
| Packages | `package_name` | Two groups both managing `curl` — higher-priority group's version/state wins |
| Linux users | `username` | Two groups both defining user `deploy` — higher-priority group's config wins |
| Linux groups | `groupname` | Two groups both defining group `developers` — higher-priority group wins |
| Cron jobs | `name` + `user` | Two groups both defining cron job "backup" for user "root" — higher-priority group wins |
| DNS resolver | None (singleton) | Only one resolver config per host — highest-priority group wins entirely |

### Host-Level Overrides

- **Firewall rules**: No host-level overrides. Rules are group-level only.
- **Services**: A host-level service entry completely replaces the group-level entry for that `service_name`.
- **/etc/hosts**: A host-level entry completely replaces the group-level entry for that `ip_address`.
- **Packages**: A host-level package entry completely replaces the group-level entry for that `package_name`.
- **Linux users/groups**: A host-level entry completely replaces the group-level entry for that `username` or `groupname`.
- **Cron jobs**: A host-level entry completely replaces the group-level entry for that `name` + `user` pair.
- **DNS resolver**: A host-level resolver config completely replaces any group-level config.

Host overrides are applied after the group merge, so they always win regardless of group priority.

### How Sync Applies to the Remote Host

When you sync, LabDog computes the full effective configuration and pushes it to the host via Ansible. The apply strategy differs by module:

| Module | Strategy | What happens on sync | Manual edits on host? |
|--------|----------|---------------------|-----------------------|
| **Firewall (nftables)** | Full replacement | Writes complete `/etc/nftables.conf` (includes `flush ruleset`), validates, reloads | Overwritten |
| **Firewall (iptables)** | Full replacement | Writes complete iptables rules via `iptables-restore`, validates, persists | Overwritten |
| **/etc/hosts** | Full replacement | Writes complete `/etc/hosts` via atomic copy, validates localhost entry exists | Overwritten |
| **Services** | Per-service tasks | Deploys unit files (full or override) if configured, sets `state` (started/stopped) and `enabled` (true/false), cleans up orphaned LabDog-managed files | Preserved (unmanaged services are left alone) |
| **Packages** | Per-package tasks | Installs/removes individual packages via `apt`/`dnf`/`yum` (auto-detected) | Preserved (unmanaged packages are left alone) |
| **Linux users** | Per-user tasks | Creates/removes users, sets authorized_keys (`exclusive=true`), writes `/etc/sudoers.d/{user}` | Preserved (unmanaged users are left alone) |
| **Cron jobs** | Per-job tasks | Creates/removes individual cron entries via `crontab` (identified by job name) | Preserved (unmanaged cron jobs are left alone) |
| **DNS resolver** | Full replacement | Writes complete resolver config (`/etc/resolv.conf`, `systemd-resolved`, or NetworkManager), restarts service | Overwritten |

**Key takeaway**: Modules that manage a single config file (firewall/nftables, firewall/iptables, /etc/hosts, DNS resolver) use **full replacement** — LabDog owns the entire file and manual edits will be lost on next sync. Modules that manage individual items (services, packages, users, cron jobs) are **selectively managed** — only the items you define in LabDog are touched; everything else on the host is left alone.

**Coalesced execution**: From v0.2.0 onward, every sync — bulk or
per-tab — runs through one orchestrator task per host that emits a
single Ansible playbook covering the requested modules in a fixed
order (`packages → resolver → services → hosts-file → cron →
linux-users → firewall`, with firewall last so its 60-second deadman
switch doesn't expire while later modules run). Concurrent sync
requests for the same host queue (`SyncJob.status="pending"`) and
are dispatched in `created_at` order; only one ansible-runner
invocation against a given host at a time. The new
`POST /api/sync/hosts/{id}/bulk` endpoint exposes multi-module
syncs to API callers; per-tab Sync buttons are unchanged externally
but internally delegate to the same orchestrator.

### Automatic Safety Rules

- **SSH lockout prevention**: An SSH ACCEPT rule for the LabDog server IP is always injected at the top of the firewall ruleset (priority 999999). This rule cannot be deleted and ensures you never lock yourself out.
- **System /etc/hosts entries**: `127.0.0.1 localhost` and `::1 localhost` are always injected into the rendered hosts file, regardless of what you configure.
- **Protected service deny-list**: Critical services (`sshd`, `systemd-*`) are blocked from management to prevent accidental lockout.
- **DNS resolver header**: A `# Managed by LabDog` comment is injected into rendered resolver config files.

## Architecture

| Component | Technology | Port |
|-----------|-----------|------|
| Frontend | Next.js 16 + shadcn/ui + TanStack Query | 3000 |
| Backend API | FastAPI + SQLAlchemy (async) | 8000 |
| Database | PostgreSQL 16 | 5432 |
| Task Queue | Celery + Redis (RedBeat scheduler) | -- |
| Config Management | Ansible (ansible-runner) | -- |

## Installation

### From Package (Recommended)

Pre-built packages are available on the [Releases](https://github.com/open-labdog/labdog/releases) page for each tagged version.

**Supported distros** — the bundled venv requires `python3.12` to be
installed at `/usr/bin/python3.12`. That's available out of the box on:

| Distro | Notes |
|---|---|
| Ubuntu 24.04+ | `python3.12` package in main repo |
| Debian 13+ (trixie) | `python3.12` package in main repo |
| RHEL / Rocky / Alma 9+ | `python3.12` package in AppStream — `dnf install python3.12` |
| Fedora 39+ | `python3.12` package in main repo |

Older Debian (12 / bookworm) does not ship python3.12 in any official
repo and is **not supported by the .deb / .rpm install path** — use
the [Docker image](#quick-start-docker) on those hosts.

**Debian / Ubuntu (.deb)**

```bash
VERSION=0.2.0
curl -LO https://github.com/open-labdog/labdog/releases/download/v${VERSION}/labdog_${VERSION}-1_amd64.deb
sudo apt install ./labdog_${VERSION}-1_amd64.deb
```

**RHEL / Fedora / Rocky (.rpm)**

```bash
VERSION=0.2.0
curl -LO https://github.com/open-labdog/labdog/releases/download/v${VERSION}/labdog-${VERSION}-1.x86_64.rpm
sudo dnf install ./labdog-${VERSION}-1.x86_64.rpm
```

After package install, skip to [Post-install configuration](#post-install-configuration).

### From Tarball

```bash
VERSION=0.2.0
curl -LO https://github.com/open-labdog/labdog/releases/download/v${VERSION}/labdog-${VERSION}-linux-amd64.tar.gz
tar -xzf labdog-${VERSION}-linux-amd64.tar.gz
cd labdog-${VERSION}-linux-amd64
sudo ./install.sh
```

### Verifying Downloads

```bash
curl -LO https://github.com/open-labdog/labdog/releases/download/v${VERSION}/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
```

### Post-install Configuration

All three install methods place the default config at `/etc/labdog/labdog.toml`. Edit it before starting the service:

```bash
sudo nano /etc/labdog/labdog.toml
```

**Required fields:**

| Setting | Description | How to generate |
|---------|-------------|-----------------|
| `[security] secret_key` | JWT signing key | `openssl rand -base64 32` |
| `[security] encryption_key` | AES-256-GCM key for SSH key encryption (32 bytes; standard or url-safe base64) | `openssl rand -base64 32` |
| `[security] labdog_server_ip` | This server's IP (used in SSH lockout prevention rule) | `ip route get 1 \| awk '{print $7; exit}'` |
| `[database] url` | PostgreSQL async connection string | — |

```toml
[security]
secret_key    = "<output of: openssl rand -base64 32>"
encryption_key = "<output of: openssl rand -base64 32>"
labdog_server_ip = "192.168.1.10"   # this server's IP

[database]
url = "postgresql+asyncpg://labdog:password@localhost:5432/labdog"
```

**Prerequisites** (not bundled):

- PostgreSQL 14+
- Redis 6+

**Start the service:**

```bash
sudo systemctl enable --now labdog.service
sudo systemctl status labdog.service
# Logs:
sudo journalctl -u labdog -f
```

LabDog listens on `http://127.0.0.1:8000` by default. Put it behind a reverse proxy (nginx, Caddy) for HTTPS.

### Uninstalling

**Debian / Ubuntu**

```bash
# Remove (keeps config, data, and logs)
sudo apt remove labdog

# Remove everything including config, data, logs, and system user
sudo apt purge labdog
```

**RHEL / Fedora / Rocky**

```bash
sudo dnf remove labdog
# Config, data, and logs under /etc/labdog, /var/lib/labdog,
# /var/log/labdog are preserved. Remove manually if no longer needed.
```

**Tarball install**

```bash
cd labdog-<version>-linux-amd64
sudo ./uninstall.sh          # keeps config/data/logs
sudo ./uninstall.sh --purge  # removes everything
```

## Quick Start (Docker)

### Prerequisites

- Docker and Docker Compose v2

### Steps

1. Clone and configure:
   ```bash
   git clone <repo-url> labdog && cd labdog
   cp .env.example dev/.env
   ```

2. Generate required secrets:
   ```bash
   # Generate ENCRYPTION_KEY (32 bytes, base64):
   python3 -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"

   # Generate SECRET_KEY:
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
   Paste both values into `.env`.

3. Start the stack:
   ```bash
   docker compose -f dev/docker-compose.yml up -d
   ```

4. Access:
   - Web UI and API: http://localhost:8000 — the container serves the built frontend and the API on a single port
   - Health check: http://localhost:8000/health

   (`http://localhost:3000` is only the Next.js dev server used in [local development](#local-development), not the Docker Compose stack.)

5. Register the first user (automatically becomes available for superuser promotion).

## Environment Variables

LabDog reads settings as environment variables using the pattern
`LABDOG_<SECTION>__<KEY>` (double underscore between section and key) —
e.g. `LABDOG_SERVER__PORT=9000`. They can also be set in
`dev/labdog.toml`; env vars override the file. See
[`.env.example`](../.env.example) for the full reference. The most
common ones:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LABDOG_SECURITY__SECRET_KEY` | Yes (production) | `change-me-in-production` (insecure default rejected at startup) | JWT signing key |
| `LABDOG_SECURITY__ENCRYPTION_KEY` | Yes (production) | insecure default rejected at startup | AES-256-GCM master key for encrypting stored SSH keys, Proxmox API tokens, and git HTTPS PATs (must decode to 32 bytes; standard or url-safe base64) |
| `LABDOG_DATABASE__URL` | Auto (Docker) | `postgresql+asyncpg://labdog:labdog@localhost:5432/labdog` | Async PostgreSQL connection string |
| `LABDOG_REDIS__URL` | Auto (Docker) | `redis://localhost:6379/0` | Redis URL for the Celery broker and result backend |
| `LABDOG_SECURITY__LABDOG_SERVER_IP` | No | `127.0.0.1` | IP of the LabDog server (used in the auto-injected SSH lockout rule) |
| `POSTGRES_PASSWORD` | Docker | `labdog` | PostgreSQL password — consumed by the `postgres` container and interpolated into `LABDOG_DATABASE__URL` in the compose file |
| `NEXT_PUBLIC_API_URL` | Frontend build | `http://localhost:8000` | Backend API URL baked into the frontend at build time |

> Operational settings (drift interval, log level, SSH/Ansible
> timeouts, audit retention, discovery tuning) are managed on the
> **Settings** page and stored in the database — they are not set via
> environment variables.

## Local Development

The `dev.sh` script manages all dev processes:

```bash
./dev/dev.sh start       # Start postgres + redis (Docker) + backend + frontend
./dev/dev.sh stop        # Stop everything
./dev/dev.sh status      # Show running processes
./dev/dev.sh logs        # Tail all dev logs

./dev/dev.sh infra       # Start only postgres + redis
./dev/dev.sh backend     # Start only backend (uvicorn + celery worker + celery beat)
./dev/dev.sh frontend    # Start only frontend (next dev)

./dev/dev.sh migrate     # Run alembic upgrade head
./dev/dev.sh migrate-down    # Roll back one migration
./dev/dev.sh migrate-new "description"  # Generate new migration
```

### Manual Setup (without dev.sh)

**Backend:**
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d postgres redis
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev  # http://localhost:3000
```

**Celery workers:**
```bash
cd backend && source .venv/bin/activate
celery -A app.tasks worker -Q default,long_running --loglevel=info
celery -A app.tasks beat --scheduler redbeat.RedBeatScheduler --loglevel=info
```

## Testing

### Backend

Tests use testcontainers to spin up a throwaway PostgreSQL instance automatically.

```bash
cd backend && source .venv/bin/activate
pytest tests/ --ignore=tests/integration -v           # unit/module tests
pytest tests/integration/ -v -m integration           # integration tests (requires Docker)
```

The test suite covers: audit, auth, cron, crypto, diff, discovery, drift, gitops (converter, importer, lockdown, pipeline, serializer, webhooks), groups, hosts, merge, packages, parsers, renderers, resolver, rules, service commands, services, SSH terminal, sync, and user management.

### Frontend E2E (Playwright)

```bash
cd frontend
npx playwright install --with-deps
npx playwright test          # requires running Docker stack
npx playwright test --ui     # interactive test runner
```

E2E spec files cover auth, dashboard, groups, hosts, rules, SSH terminal, sync, audit, and UX patterns (breadcrumbs, command palette, confirm dialogs, host grouping, mobile, search, toasts).

## API Endpoints

### Auth
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/auth/setup-status` | Check if initial setup is needed |
| `POST` | `/api/auth/register` | Register first user (closed after first user) |
| `POST` | `/api/auth/jwt/login` | Login (sets httpOnly cookie) |
| `POST` | `/api/auth/jwt/logout` | Logout |
| `GET` | `/users/me` | Current user info |

### Infrastructure
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST` | `/api/groups` | List/create host groups |
| `GET/POST` | `/api/hosts` | List/create hosts |
| `POST` | `/api/ssh-keys` | Upload SSH key (encrypted at rest) |
| `GET` | `/api/audit-log` | View audit trail |
| `GET` | `/health` | Health check |

### Sync (coalesced multi-module)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/sync/hosts/{id}/bulk` | Sync any subset of modules in one call. Body: `{"module_filter": ["firewall", "services"]}` (or `null` for all). Idempotent — returns the in-flight job's id with HTTP 200 if a bulk sync is already pending/running for the host. Superuser-only. |
| `GET` | `/api/sync/jobs/{id}` | Check sync job status (any sync — bulk or per-tab) |

The bulk endpoint dispatches one orchestrator task per host that
produces a single unified Ansible playbook covering every requested
module — one ansible-runner invocation regardless of how many
modules are syncing. Per-host serialisation: only one sync runs per
host at a time; concurrent requests queue (`SyncJob.status="pending"`)
and are dispatched in `created_at` order when the in-flight task
finishes.

Two `audit_log` rows per sync: `sync_triggered` at API entry,
`sync_completed` at orchestrator finish (carries
`{module: "in_sync"|"error"|"no_tasks"}` composite payload).

### Firewall Rules
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST/PUT/DELETE` | `/api/groups/{id}/rules` | Manage firewall rules per group |
| `GET` | `/api/hosts/{id}/effective-rules` | Get merged effective rules |
| `POST` | `/api/sync/hosts/{id}/plan` | Preview firewall changes |
| `POST` | `/api/sync/hosts/{id}/sync` | Apply firewall changes via Ansible. Internally a single-module bulk sync (`module_filter=["firewall"]`); shares the orchestrator's queue + audit semantics. |
| `POST` | `/api/drift/hosts/{id}/check` | Check firewall drift |
| `POST` | `/api/drift/groups/{id}/check` | Check drift for all hosts in group |

### Service Management
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST/PUT/DELETE` | `/api/groups/{id}/services` | Manage service rules per group |
| `GET/POST/PUT/DELETE` | `/api/hosts/{id}/services` | Host-level service overrides |
| `GET` | `/api/hosts/{id}/effective-services` | Merged effective services |
| `POST` | `/api/services/hosts/{id}/plan` | Preview service changes |
| `POST` | `/api/services/hosts/{id}/sync` | Sync services via Ansible (delegated to the orchestrator with `module_filter=["services"]`) |
| `POST` | `/api/services/hosts/{id}/drift-check` | Check service drift |
| `PUT` | `/api/services/hosts/{id}/drift-settings` | Toggle service drift detection |
| `GET` | `/api/services/hosts/{id}/unit-file/{service_name}` | Fetch current unit file content from host via SSH |

### /etc/hosts Management
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST/PUT/DELETE` | `/api/groups/{id}/hosts-entries` | Manage hosts file entries per group |
| `GET/POST/PUT/DELETE` | `/api/hosts/{id}/hosts-entries` | Host-level hosts file overrides |
| `GET` | `/api/hosts/{id}/effective-hosts-entries` | Merged effective entries |
| `GET` | `/api/hosts/{id}/hosts-file-preview` | Preview rendered /etc/hosts |
| `POST` | `/api/hosts-mgmt/hosts/{id}/plan` | Preview hosts file changes |
| `POST` | `/api/hosts-mgmt/hosts/{id}/sync` | Sync /etc/hosts via Ansible (delegated to the orchestrator with `module_filter=["hosts-file"]`) |
| `POST` | `/api/hosts-mgmt/hosts/{id}/drift-check` | Check hosts file drift |

### Package Management
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST/PUT/DELETE` | `/api/groups/{id}/packages` | Manage packages per group |
| `GET/POST/PUT/DELETE` | `/api/hosts/{id}/packages` | Host-level package overrides |
| `GET` | `/api/hosts/{id}/effective-packages` | Merged effective packages |
| `POST` | `/api/packages/hosts/{id}/sync` | Sync packages via Ansible (delegated to the orchestrator with `module_filter=["packages"]`) |

### Linux User Management
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST/PUT/DELETE` | `/api/groups/{id}/users` | Manage Linux users per group |
| `GET/POST/PUT/DELETE` | `/api/hosts/{id}/users` | Host-level user overrides |
| `GET` | `/api/hosts/{id}/effective-users` | Merged effective users |
| `POST` | `/api/user-mgmt/hosts/{id}/sync` | Sync users via Ansible (delegated to the orchestrator with `module_filter=["linux-users"]`) |

### Cron Jobs
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST/PUT/DELETE` | `/api/groups/{id}/cron-jobs` | Manage cron jobs per group |
| `GET/POST/PUT/DELETE` | `/api/hosts/{id}/cron-jobs` | Host-level cron overrides |
| `GET` | `/api/hosts/{id}/effective-cron-jobs` | Merged effective cron jobs |
| `POST` | `/api/cron/hosts/{id}/sync` | Sync cron jobs via Ansible (delegated to the orchestrator with `module_filter=["cron"]`) |

### DNS Resolver
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST/PUT/DELETE` | `/api/groups/{id}/resolver` | Manage resolver config per group |
| `GET/POST/PUT/DELETE` | `/api/hosts/{id}/resolver` | Host-level resolver override |
| `GET` | `/api/hosts/{id}/effective-resolver` | Merged effective resolver |
| `POST` | `/api/resolver/hosts/{id}/sync` | Sync resolver via Ansible (delegated to the orchestrator with `module_filter=["resolver"]`) |

### Host Discovery
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/discovery/scan` | Start network scan (returns job_id) |
| `GET` | `/api/discovery/scan/{job_id}` | Poll scan job status |
| `POST` | `/api/discovery/add-hosts` | Bulk-add discovered hosts |

### SSH Terminal
| Method | Path | Description |
|--------|------|-------------|
| `WS` | `/api/ssh-terminal/ws/{host_id}` | WebSocket SSH terminal session |

### GitOps
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST/PUT/DELETE` | `/api/git-repos` | Manage Git repository connections |
| `POST` | `/webhooks/github` | GitHub push webhook (HMAC-SHA256 signature) |
| `POST` | `/webhooks/gitlab` | GitLab push webhook (X-Gitlab-Token shared secret) |
| `POST` | `/webhooks/gitea` | Gitea push webhook (HMAC-SHA256 signature) |

See [examples/gitops/README.md](./examples/gitops/README.md) for setup walkthrough and YAML examples covering every module.

### Grafana metrics
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST` | `/api/grafana/instances` | List/register Grafana Mimir/Loki backends (bearer token encrypted at rest) |
| `GET/PUT/DELETE` | `/api/grafana/instances/{id}` | Manage a registered instance |
| `POST` | `/api/grafana/instances/{id}/test` · `/api/grafana/instances/test` | Connection test (saved instance / pre-save draft) |
| `GET` | `/api/grafana/hosts/{id}/metrics` | Instant CPU/memory/disk for a host (queried from the default Mimir instance by the `labdog_host_id` label) |

See [ui/metrics.md](./ui/metrics.md) for the end-to-end loop with the bundled Alloy install action.

## Project Structure

```
labdog/
├── backend/
│   ├── app/
│   │   ├── actions/         # Pack loader, registry, manifest schema, git sync
│   │   ├── ansible/         # Bundled action pack (gitignored; fetched at build time from labdog-playbooks)
│   │   ├── ansible_runtime/ # Playbook composer + ansible-runner wrapper
│   │   ├── api/             # FastAPI route handlers (incl. /api/version)
│   │   ├── audit/           # Audit logging
│   │   ├── auth/            # JWT auth (cookie-based)
│   │   ├── ca_certs/        # Trusted CA certificate deployment module
│   │   ├── cron/            # Cron job management module
│   │   ├── crypto/          # AES-256-GCM encryption
│   │   ├── discovery/       # Host network discovery
│   │   ├── drift/           # Firewall drift detection
│   │   ├── gitops/          # GitOps integration
│   │   ├── grafana/         # Grafana Mimir/Loki integration (instant host metrics)
│   │   ├── hosts_mgmt/      # /etc/hosts management module
│   │   ├── models/          # SQLAlchemy models
│   │   ├── packages/        # Package management module
│   │   ├── packs/           # DB-backed action-pack subsystem
│   │   ├── proxmox/         # Proxmox VE hypervisor integration
│   │   ├── resolver/        # DNS resolver module
│   │   ├── rules/           # Firewall rule validation, renderers, merge
│   │   ├── schemas/         # Pydantic request/response schemas
│   │   ├── services/        # Service management module
│   │   ├── ssh_terminal/    # Web shell (WebSocket SSH terminal)
│   │   ├── sync/            # Per-host orchestrator + bulk sync entry point
│   │   ├── tasks/           # Celery tasks (sync orchestrator, drift, scheduled actions)
│   │   ├── user_mgmt/       # Linux user/group management
│   │   └── workflows/       # Proxmox snapshot / verify / rollback steps
│   ├── alembic/             # Database migrations
│   ├── tests/               # pytest suite
│   │   ├── integration/     # Integration tests (require full stack)
│   │   └── test_*.py        # Unit/module tests
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── app/                 # Next.js App Router pages
│   ├── components/          # React components (shadcn/ui)
│   ├── e2e/                 # Playwright E2E tests
│   ├── hooks/               # Custom React hooks
│   ├── lib/                 # API client, utilities
│   ├── Dockerfile
│   └── package.json
├── labdog-lint/           # CLI tool for YAML rule validation
├── packaging/               # Linux package build system (deb/rpm/tarball)
├── docs/                    # This directory — docs index + examples
├── dev/                     # Local development tools
│   ├── dev.sh               # Dev environment management script
│   ├── build.sh             # Local Docker build script
│   ├── deploy.sh            # Local Docker deploy script
│   ├── docker-compose.yml   # Local dev stack (postgres + redis)
│   ├── labdog.toml          # Dev configuration
│   └── .env                 # Local secrets (gitignored — copy from .env.example)
├── dev.sh                   # Thin wrapper → dev/dev.sh
├── .env.example             # Environment variable template
└── Dockerfile
```

## Extension Modules

LabDog uses a modular extension architecture. Each module follows the same pattern: model → schemas → merge engine → API → Ansible generator → drift detector → Celery tasks → frontend UI.

| Module | Status | Description |
|--------|--------|-------------|
| Firewall Rules | Shipped | nftables/iptables rule management |
| Service Management | Shipped | systemd service state management |
| /etc/hosts | Shipped | Host file entry management |
| Package Management | Shipped | apt/dnf/yum package management |
| Linux User Management | Shipped | System users, SSH keys, sudo rules |
| Cron Jobs | Shipped | Cron job scheduling |
| DNS Resolver | Shipped | resolv.conf / systemd-resolved config |
| Web Shell | Shipped | Browser-based SSH terminal (xterm.js + WebSocket + asyncssh) |
| Host Discovery | Shipped | Network scanning + SSH-verified bulk host import |
| Proxmox Integration | Shipped | VM/container discovery from Proxmox VE hypervisors |
| Grafana metrics | Shipped | Instant CPU/memory/disk on the host page from a Mimir/Prometheus backend; ties into the Alloy install action |
| GitOps | Shipped | Git-based configuration import with webhook sync |

## Known Limitations

- **Drift detection edge cases**: Firewall parsers exist for nftables and iptables, but drift comparison may miss some complex rule configurations.
- **No HTTPS in dev**: Cookie `secure=False` by default. Set `cookie_secure=True` for production with HTTPS.
- **Single Ansible control node**: All sync operations run from the LabDog server. No distributed execution.

## CI/CD

LabDog runs CI on GitHub Actions (`.github/workflows/ci.yml`). Stages:
lint → test → build → scan.

- **Lint**: ruff (backend) + ESLint/tsc (frontend) + gitleaks
- **Test**: backend pytest (unit + integration via testcontainers) +
  frontend Playwright smoke + pip-audit
- **Build**: Docker images for backend and frontend; published to
  Docker Hub on `main` (`latest` + `<sha>`) and `dev` (`test` +
  `test-<sha>`)
- **Release** (`release-artifacts`): triggered by push to `main`
  (i.e. when a `dev` → `main` PR merges). Builds the `.tar.gz`,
  `.deb`, `.rpm`, and `SHA256SUMS` set via `packaging/build.sh`,
  auto-tags the merge commit `vX.Y.Z` from the repo-root
  [`VERSION`](https://github.com/open-labdog/labdog/blob/main/VERSION) file, pushes the tag, and publishes a
  GitHub Release with the artifacts attached. Idempotent on tag
  presence — re-running a previous merge's workflow skips with a
  notice instead of double-releasing. See
  [CONTRIBUTING.md → Release process](https://github.com/open-labdog/labdog/blob/main/CONTRIBUTING.md#release-process).
