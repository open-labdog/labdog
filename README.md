# Barricade

Centralized Linux configuration management via Ansible. Manage firewall rules, systemd services, `/etc/hosts`, and more from a web UI — preview changes before applying, detect drift, and sync to hosts over SSH.

## Features

- **Firewall management**: nftables, firewalld, ufw — same rules, any backend
- **Service management**: Declare systemd service states (running/stopped), sync via Ansible, detect drift
- **/etc/hosts management**: Manage host file entries with full-file rendering, system entry protection, and file preview
- **Plan-before-apply**: Preview exact changes before syncing to remote hosts
- **SSH lockout prevention**: Auto-injected system rule ensures SSH access is never accidentally blocked
- **Drift detection**: Periodic and manual checks for out-of-sync hosts across all modules
- **Audit trail**: Append-only log of all actions with before/after state
- **Priority-based merge**: Groups with higher priority override lower ones on shared hosts; host-level overrides replace group rules
- **Protected service deny-list**: Critical services (sshd, systemd-*) blocked from accidental management

## How Configuration Is Applied

Barricade organizes configuration through **host groups**. You define rules at the group level, assign hosts to one or more groups, and Barricade merges everything into a single effective configuration per host.

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

When you sync, Barricade computes the full effective configuration and pushes it to the host via Ansible. The apply strategy differs by module:

| Module | Strategy | What happens on sync | Manual edits on host? |
|--------|----------|---------------------|-----------------------|
| **Firewall (nftables)** | Full replacement | Writes complete `/etc/nftables.conf` (includes `flush ruleset`), validates, reloads | Overwritten |
| **Firewall (ufw)** | Full replacement | Writes complete `/etc/ufw/user.rules` + `user6.rules`, reloads | Overwritten |
| **Firewall (firewalld)** | Per-rule tasks | Adds/removes individual `firewalld` rules | Preserved (unmanaged rules are left alone) |
| **/etc/hosts** | Full replacement | Writes complete `/etc/hosts` via atomic copy, validates localhost entry exists | Overwritten |
| **Services** | Per-service tasks | Sets `state` (started/stopped) and `enabled` (true/false) per service individually | Preserved (unmanaged services are left alone) |
| **Packages** | Per-package tasks | Installs/removes individual packages via `apt`/`dnf`/`yum` (auto-detected) | Preserved (unmanaged packages are left alone) |
| **Linux users** | Per-user tasks | Creates/removes users, sets authorized_keys (`exclusive=true`), writes `/etc/sudoers.d/{user}` | Preserved (unmanaged users are left alone) |
| **Cron jobs** | Per-job tasks | Creates/removes individual cron entries via `crontab` (identified by job name) | Preserved (unmanaged cron jobs are left alone) |
| **DNS resolver** | Full replacement | Writes complete resolver config (`/etc/resolv.conf`, `systemd-resolved`, or NetworkManager), restarts service | Overwritten |

**Key takeaway**: Modules that manage a single config file (firewall/nftables, firewall/ufw, /etc/hosts, DNS resolver) use **full replacement** — Barricade owns the entire file and manual edits will be lost on next sync. Modules that manage individual items (services, packages, users, cron jobs, firewall/firewalld) are **selectively managed** — only the items you define in Barricade are touched; everything else on the host is left alone.

### Automatic Safety Rules

- **SSH lockout prevention**: An SSH ACCEPT rule for the Barricade server IP is always injected at the top of the firewall ruleset (priority 999999). This rule cannot be deleted and ensures you never lock yourself out.
- **System /etc/hosts entries**: `127.0.0.1 localhost` and `::1 localhost` are always injected into the rendered hosts file, regardless of what you configure.
- **Protected service deny-list**: Critical services (`sshd`, `systemd-*`) are blocked from management to prevent accidental lockout.
- **DNS resolver header**: A `# Managed by Barricade` comment is injected into rendered resolver config files.

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

Pre-built packages are available on the [Releases](../../releases) page for each tagged version.

**Debian / Ubuntu (.deb)**

```bash
VERSION=0.0.1
curl -LO https://gitlab.example.com/dennis/barricade/-/packages/generic/barricade/${VERSION}/barricade_${VERSION}-1_amd64.deb
sudo apt install ./barricade_${VERSION}-1_amd64.deb
```

**RHEL / Fedora / Rocky (.rpm)**

```bash
VERSION=0.0.1
curl -LO https://gitlab.example.com/dennis/barricade/-/packages/generic/barricade/${VERSION}/barricade-${VERSION}-1.x86_64.rpm
sudo dnf install ./barricade-${VERSION}-1.x86_64.rpm
```

After package install, skip to [Post-install configuration](#post-install-configuration).

### From Tarball

```bash
VERSION=0.0.1
curl -LO https://gitlab.example.com/dennis/barricade/-/packages/generic/barricade/${VERSION}/barricade-${VERSION}-linux-amd64.tar.gz
tar -xzf barricade-${VERSION}-linux-amd64.tar.gz
cd barricade-${VERSION}-linux-amd64
sudo ./install.sh
```

### Verifying Downloads

```bash
curl -LO https://gitlab.example.com/dennis/barricade/-/packages/generic/barricade/${VERSION}/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
```

### Post-install Configuration

All three install methods place the default config at `/etc/barricade/barricade.toml`. Edit it before starting the service:

```bash
sudo nano /etc/barricade/barricade.toml
```

**Required fields:**

| Setting | Description | How to generate |
|---------|-------------|-----------------|
| `[security] secret_key` | JWT signing key | `openssl rand -base64 32` |
| `[security] encryption_key` | AES-256-GCM key for SSH key encryption (32 bytes, base64) | `openssl rand -base64 32` |
| `[security] barricade_server_ip` | This server's IP (used in SSH lockout prevention rule) | `ip route get 1 \| awk '{print $7; exit}'` |
| `[database] url` | PostgreSQL async connection string | — |

```toml
[security]
secret_key    = "<output of: openssl rand -base64 32>"
encryption_key = "<output of: openssl rand -base64 32>"
barricade_server_ip = "192.168.1.10"   # this server's IP

[database]
url = "postgresql+asyncpg://barricade:password@localhost:5432/barricade"
```

**Prerequisites** (not bundled):

- PostgreSQL 14+
- Redis 6+

**Start the service:**

```bash
sudo systemctl enable --now barricade.service
sudo systemctl status barricade.service
# Logs:
sudo journalctl -u barricade -f
```

Barricade listens on `http://127.0.0.1:8000` by default. Put it behind a reverse proxy (nginx, Caddy) for HTTPS.

### Uninstalling

**Debian / Ubuntu**

```bash
# Remove (keeps config, data, and logs)
sudo apt remove barricade

# Remove everything including config, data, logs, and system user
sudo apt purge barricade
```

**RHEL / Fedora / Rocky**

```bash
sudo dnf remove barricade
# Config, data, and logs under /etc/barricade, /var/lib/barricade,
# /var/log/barricade are preserved. Remove manually if no longer needed.
```

**Tarball install**

```bash
cd barricade-<version>-linux-amd64
sudo ./uninstall.sh          # keeps config/data/logs
sudo ./uninstall.sh --purge  # removes everything
```

---

## Quick Start (Docker)

### Prerequisites

- Docker and Docker Compose v2

### Steps

1. Clone and configure:
   ```bash
   git clone <repo-url> barricade && cd barricade
   cp .env.example .env
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
   docker compose up -d
   ```

4. Access:
   - Frontend: http://localhost:3000
   - API: http://localhost:8000
   - Health check: http://localhost:8000/health

5. Register the first user (automatically becomes available for superuser promotion).

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTGRES_PASSWORD` | Docker | `barricade` | PostgreSQL password |
| `SECRET_KEY` | Yes (production) | `change-me-in-production` | JWT signing key |
| `ENCRYPTION_KEY` | Yes (production) | -- | AES-256-GCM master key for SSH key encryption (32 bytes, base64) |
| `BARRICADE_SERVER_IP` | Yes | `127.0.0.1` | IP of the Barricade server (used in SSH lockout rule) |
| `NEXT_PUBLIC_API_URL` | Frontend | `http://localhost:8000` | Backend API URL |
| `DATABASE_URL` | Auto (Docker) | `postgresql+asyncpg://barricade:barricade@localhost:5432/barricade` | Async PostgreSQL connection string |
| `REDIS_URL` | Auto (Docker) | `redis://localhost:6379/0` | Redis URL for Celery broker and result backend |
| `DRIFT_CHECK_INTERVAL_MINUTES` | No | `30` | Interval for automatic drift detection checks |

## Local Development

The `dev.sh` script manages all dev processes:

```bash
./dev.sh start       # Start postgres + redis (Docker) + backend + frontend
./dev.sh stop        # Stop everything
./dev.sh status      # Show running processes
./dev.sh logs        # Tail all dev logs

./dev.sh infra       # Start only postgres + redis
./dev.sh backend     # Start only backend (uvicorn + celery worker + celery beat)
./dev.sh frontend    # Start only frontend (next dev)

./dev.sh migrate     # Run alembic upgrade head
./dev.sh migrate-down    # Roll back one migration
./dev.sh migrate-new "description"  # Generate new migration
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

```bash
cd backend && source .venv/bin/activate
pytest tests/ --ignore=tests/integration -v
pytest tests/integration/ -v -m integration     # requires Docker
```

### Frontend E2E (Playwright)

```bash
cd frontend
npx playwright install --with-deps
npx playwright test          # requires running Docker stack
```

## API Endpoints

### Auth
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/register` | Register new user |
| `POST` | `/auth/jwt/login` | Login (sets httpOnly cookie) |
| `POST` | `/auth/jwt/logout` | Logout |
| `GET` | `/users/me` | Current user info |

### Infrastructure
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST` | `/api/groups` | List/create host groups |
| `GET/POST` | `/api/hosts` | List/create hosts |
| `POST` | `/api/ssh-keys` | Upload SSH key (encrypted at rest) |
| `GET` | `/api/audit-log` | View audit trail |
| `GET` | `/health` | Health check |

### Firewall Rules
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST/PUT/DELETE` | `/api/groups/{id}/rules` | Manage firewall rules per group |
| `GET` | `/api/hosts/{id}/effective-rules` | Get merged effective rules |
| `POST` | `/api/sync/hosts/{id}/plan` | Preview firewall changes |
| `POST` | `/api/sync/hosts/{id}/sync` | Apply firewall changes via Ansible |
| `GET` | `/api/sync/jobs/{id}` | Check sync job status |
| `POST` | `/api/drift/hosts/{id}/check` | Check firewall drift |

### Service Management
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST/PUT/DELETE` | `/api/groups/{id}/services` | Manage service rules per group |
| `GET/POST/PUT/DELETE` | `/api/hosts/{id}/services` | Host-level service overrides |
| `GET` | `/api/hosts/{id}/effective-services` | Merged effective services |
| `POST` | `/api/services/hosts/{id}/plan` | Preview service changes |
| `POST` | `/api/services/hosts/{id}/sync` | Sync services via Ansible |
| `POST` | `/api/services/hosts/{id}/drift-check` | Check service drift |
| `PUT` | `/api/services/hosts/{id}/drift-settings` | Toggle service drift detection |

### /etc/hosts Management
| Method | Path | Description |
|--------|------|-------------|
| `GET/POST/PUT/DELETE` | `/api/groups/{id}/hosts-entries` | Manage hosts file entries per group |
| `GET/POST/PUT/DELETE` | `/api/hosts/{id}/hosts-entries` | Host-level hosts file overrides |
| `GET` | `/api/hosts/{id}/effective-hosts-entries` | Merged effective entries |
| `GET` | `/api/hosts/{id}/hosts-file-preview` | Preview rendered /etc/hosts |
| `POST` | `/api/hosts-mgmt/hosts/{id}/plan` | Preview hosts file changes |
| `POST` | `/api/hosts-mgmt/hosts/{id}/sync` | Sync /etc/hosts via Ansible |
| `POST` | `/api/hosts-mgmt/hosts/{id}/drift-check` | Check hosts file drift |

## Project Structure

```
barricade/
├── backend/
│   ├── app/
│   │   ├── api/             # FastAPI route handlers
│   │   ├── ansible/         # Playbook + inventory generators
│   │   ├── audit/           # Audit logging
│   │   ├── auth/            # JWT auth (cookie-based)
│   │   ├── crypto/          # AES-256-GCM encryption
│   │   ├── drift/           # Firewall drift detection
│   │   ├── hosts_mgmt/      # /etc/hosts management module
│   │   ├── models/          # SQLAlchemy models
│   │   ├── rules/           # Firewall rule validation, renderers, merge
│   │   ├── schemas/         # Pydantic request/response schemas
│   │   ├── services/        # Service management module
│   │   ├── sync/            # Firewall plan/diff engine
│   │   ├── tasks/           # Celery tasks (sync + drift)
│   │   ├── cron/            # Cron job management module
│   │   ├── linux_users/     # Linux user management
│   │   ├── packages/        # Package management module
│   │   ├── resolver/        # DNS resolver module
│   │   ├── ssh_terminal/    # Web shell (WebSocket SSH terminal)
│   │   ├── discovery/       # Host network discovery
│   │   └── gitops/          # GitOps integration
│   ├── alembic/             # Database migrations
│   ├── tests/               # pytest suite
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
├── packaging/               # Linux package build system (deb/rpm/tarball)
├── examples/                # Configuration examples
├── dev.sh                   # Dev environment management script
├── build.sh                 # Local Docker build script
├── docker-compose.yml       # Full stack
├── .gitlab-ci.yml           # GitLab CI/CD pipelines
└── .env.example
```

## Extension Modules

Barricade uses a modular extension architecture. Each module follows the same pattern: model → schemas → merge engine → API → Ansible generator → drift detector → Celery tasks → frontend UI.

| Module | Status | Description |
|--------|--------|-------------|
| Firewall Rules | Shipped | nftables/firewalld/ufw rule management |
| Service Management | Shipped | systemd service state management |
| /etc/hosts | Shipped | Host file entry management |
| Package Management | Shipped | apt/dnf/yum package management |
| Linux User Management | Shipped | System users, SSH keys, sudo rules |
| Cron Jobs | Shipped | Cron job scheduling |
| DNS Resolver | Shipped | resolv.conf / systemd-resolved config |
| Web Shell | Shipped | Browser-based SSH terminal (xterm.js + WebSocket + asyncssh) |

## Known Limitations

- **Drift detection edge cases**: Firewall parsers exist for nftables, firewalld, and ufw, but drift comparison may miss some complex rule configurations.
- **No HTTPS in dev**: Cookie `secure=False` by default. Set `cookie_secure=True` for production with HTTPS.
- **Single Ansible control node**: All sync operations run from the Barricade server. No distributed execution.

## CI/CD

Barricade uses GitLab CI for automated builds and releases. See `.gitlab-ci.yml` for pipeline configuration.

- **Test**: Backend pytest + frontend build check on every push
- **Build**: Docker images for backend and frontend pushed to GitLab Container Registry
- **Package**: Tarball, .deb, and .rpm artifacts built on tagged releases
- **Release**: GitLab release created with package download links

## License

This project is proprietary software. All rights reserved.
