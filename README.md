# Barricade

Centralized Linux firewall management via Ansible. Define firewall rules in a web UI, preview changes before applying, and sync to hosts running nftables, firewalld, or ufw.

## Features

- **Multi-backend support**: nftables, firewalld, ufw -- same rules, any backend
- **Plan-before-apply**: Preview exact changes (add/remove/unchanged) before syncing
- **SSH lockout prevention**: Auto-injected system rule ensures SSH access is never accidentally blocked
- **Role-based access control**: Superuser, admin, editor, viewer roles per host group
- **Drift detection**: Periodic and manual checks for out-of-sync hosts
- **Audit trail**: Append-only log of all actions with before/after state
- **Priority-based rule merge**: Groups with higher priority override lower ones on shared hosts

## Architecture

| Component | Technology | Port |
|-----------|-----------|------|
| Frontend | Next.js 16 + shadcn/ui + TanStack Query | 3000 |
| Backend API | FastAPI + SQLAlchemy (async) | 8000 |
| Database | PostgreSQL 16 | 5432 |
| Task Queue | Celery + Redis (RedBeat scheduler) | -- |
| Config Management | Ansible (ansible-runner) | -- |

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
| `ENCRYPTION_KEY` | Yes (production) | -- | AES-256-GCM master key for SSH key encryption. Must be exactly 32 bytes base64-encoded. Generate with: `python3 -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"` |
| `BARRICADE_SERVER_IP` | Yes | `127.0.0.1` | IP of the Barricade server (used in auto-injected SSH lockout rule) |
| `NEXT_PUBLIC_API_URL` | Frontend | `http://localhost:8000` | Backend API URL |
| `DATABASE_URL` | Auto (Docker) | `postgresql+asyncpg://barricade:barricade@localhost:5432/barricade` | Async PostgreSQL connection string |
| `REDIS_URL` | Auto (Docker) | `redis://localhost:6379/0` | Redis URL for Celery broker and result backend |
| `DRIFT_CHECK_INTERVAL_MINUTES` | No | `30` | Interval for automatic drift detection checks |

## Local Development

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Start PostgreSQL + Redis (or use Docker for just those)
docker compose up -d postgres redis

# Run migrations
alembic upgrade head

# Start dev server
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev  # http://localhost:3000
```

### Celery Workers (for sync/drift)

```bash
cd backend && source .venv/bin/activate
celery -A app.tasks worker -Q default,long_running --loglevel=info
celery -A app.tasks beat --scheduler redbeat.RedBeatScheduler --loglevel=info
```

## Testing

### Backend Unit + Integration Tests

```bash
cd backend
source .venv/bin/activate
pytest tests/ --ignore=tests/integration -v    # unit + integration via testcontainers
pytest tests/integration/ -v -m integration     # full workflow test (requires Docker)
```

### Frontend E2E Tests (Playwright)

```bash
cd frontend
npx playwright install --with-deps
npx playwright test          # requires running Docker stack
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/register` | Register new user |
| `POST` | `/auth/jwt/login` | Login (sets httpOnly cookie) |
| `POST` | `/auth/jwt/logout` | Logout |
| `GET` | `/users/me` | Current user info |
| `GET/POST` | `/api/groups` | List/create host groups |
| `GET/POST` | `/api/hosts` | List/create hosts |
| `POST` | `/api/ssh-keys` | Upload SSH key (encrypted at rest) |
| `GET/POST/PUT/DELETE` | `/api/groups/{id}/rules` | Manage firewall rules per group |
| `GET` | `/api/hosts/{id}/effective-rules` | Get merged effective rules for a host |
| `POST` | `/api/sync/hosts/{id}/plan` | Preview changes without applying |
| `POST` | `/api/sync/hosts/{id}/sync` | Apply changes to host via Ansible |
| `GET` | `/api/sync/jobs/{id}` | Check sync job status |
| `POST` | `/api/drift/hosts/{id}/check` | Check if host firewall matches desired state |
| `GET` | `/api/audit-log` | View audit trail (filterable, paginated) |
| `GET` | `/health` | Health check |

## Project Structure

```
barricade/
├── backend/
│   ├── app/
│   │   ├── api/           # FastAPI route handlers
│   │   ├── ansible/       # Playbook + inventory generators
│   │   ├── audit/         # Audit logging
│   │   ├── auth/          # JWT auth + RBAC
│   │   ├── crypto/        # AES-256-GCM encryption
│   │   ├── drift/         # Drift detection engine
│   │   ├── models/        # SQLAlchemy models
│   │   ├── rules/         # Rule model, validation, renderers, merge
│   │   ├── schemas/       # Pydantic request/response schemas
│   │   ├── sync/          # Plan/diff engine
│   │   └── tasks/         # Celery tasks (sync + drift)
│   ├── alembic/           # Database migrations
│   ├── tests/             # pytest suite
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── app/               # Next.js App Router pages
│   ├── components/        # React components (shadcn/ui)
│   ├── e2e/               # Playwright E2E tests
│   ├── lib/               # API client, utilities
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml     # Full stack: 7 services
├── .env.example
└── .sisyphus/             # Work plans (internal tooling)
```

## Known Limitations

- **Firewall state parsing is stubbed**: `fetch_current_state()` returns `[]`. Plan/diff always shows "add all" and drift detection always reports "out of sync". Real parser implementation is planned.
- **No HTTPS in dev**: Cookie `secure=False` by default. Set `cookie_secure=True` for production with HTTPS.
- **Single Ansible control node**: All sync operations run from the Barricade server. No distributed execution.

## License

[Add license here]
