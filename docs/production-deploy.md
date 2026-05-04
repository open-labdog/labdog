# Production Deploy (Docker Compose)

A reference Docker Compose layout for running LabDog in production
behind a TLS-terminating reverse proxy, with persistent volumes
and env-sourced secrets.

> **Canonical alternative:** the `.deb` / `.rpm` install with the
> systemd unit at [`packaging/systemd/labdog.service`](../packaging/systemd/labdog.service)
> is the canonical production deployment for non-container hosts.
> It is documented in the main [README](./README.md#installation).
> This compose example is for shops that already run Docker and
> want one consistent deployment shape.

- [Layout](#layout)
- [compose.yaml](#composeyaml)
- [Caddyfile](#caddyfile)
- [Secrets via .env](#secrets-via-env)
- [labdog.toml](#labdogtoml)
- [Bring it up](#bring-it-up)
- [Differences from `dev/docker-compose.yml`](#differences-from-devdocker-composeyml)

---

## Layout

```
/srv/labdog/
├── compose.yaml
├── Caddyfile
├── .env                    # secrets — chmod 0600, NOT in git
├── labdog.toml             # non-secret config
└── caddy_data/             # ACME certs (auto-managed by Caddy)
```

Volumes managed by the compose file:

- `postgres_data` — PostgreSQL 16 data directory.
- `labdog_packs` — DB-backed action-pack checkouts. Mounts onto
  `ansible.packs_root_dir` (default `/var/lib/labdog/packs` —
  see `backend/app/config.py`). The bundled pack lives in the image
  itself, so this volume is only needed for git-backed packs added
  via the UI.
- `caddy_data` — Caddy's ACME state.

---

## compose.yaml

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: labdog
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: labdog
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U labdog"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  labdog:
    image: ghcr.io/open-labdog/labdog:0.2.0    # pin a real version
    environment:
      LABDOG_DATABASE__URL: postgresql+asyncpg://labdog:${POSTGRES_PASSWORD}@postgres:5432/labdog
      LABDOG_REDIS__URL: redis://redis:6379/0
      LABDOG_SECURITY__SECRET_KEY: ${LABDOG_SECURITY__SECRET_KEY}
      LABDOG_SECURITY__ENCRYPTION_KEY: ${LABDOG_SECURITY__ENCRYPTION_KEY}
      LABDOG_SECURITY__LABDOG_SERVER_IP: ${LABDOG_SECURITY__LABDOG_SERVER_IP}
      LABDOG_SECURITY__COOKIE_SECURE: "true"
      LABDOG_SECURITY__ALLOWED_ORIGINS: '["https://labdog.example.com"]'
      LABDOG_TLS__FORCE_HTTPS: "true"
      LABDOG_SERVER__TRUSTED_PROXIES: '["caddy"]'
      LABDOG_SERVER__FORWARDED_ALLOW_IPS: "*"
    volumes:
      - labdog_packs:/var/lib/labdog/packs
      # Optional: mount a host-side TOML for non-secret tunables.
      - ./labdog.toml:/etc/labdog/labdog.toml:ro
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    expose:
      - "8000"        # not published to the host — Caddy reaches it via the docker network
    restart: unless-stopped

  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"   # HTTP/3
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - labdog
    restart: unless-stopped

volumes:
  postgres_data:
  labdog_packs:
  caddy_data:
  caddy_config:
```

---

## Caddyfile

```caddy
labdog.example.com {
    encode gzip

    @ws path /api/ssh-terminal/ws/*
    reverse_proxy @ws labdog:8000

    reverse_proxy labdog:8000 {
        header_up X-Real-IP {remote}
        header_up X-Forwarded-For {remote}
        header_up X-Forwarded-Proto {scheme}
    }

    request_body {
        max_size 10MB
    }
}
```

Caddy provisions and renews the certificate via ACME on first
start. For internal deployments where ACME isn't reachable, swap
in a local CA cert via `tls /etc/ssl/certs/labdog.crt
/etc/ssl/private/labdog.key` and bind-mount the cert files.

---

## Secrets via .env

**Do NOT bake secrets into `labdog.toml`.** Source them from the
environment, where they're easier to rotate and won't end up in a
container image, a git repo, or a backup snapshot tarball by
accident.

```bash
# /srv/labdog/.env  — chmod 0600, owned by root or the deploy user
POSTGRES_PASSWORD=<run: openssl rand -base64 24>
LABDOG_SECURITY__SECRET_KEY=<run: openssl rand -base64 32>
LABDOG_SECURITY__ENCRYPTION_KEY=<run: openssl rand -base64 32>
LABDOG_SECURITY__LABDOG_SERVER_IP=10.0.0.42
```

Generate the keys once, on the deploy host:

```bash
openssl rand -base64 32      # SECRET_KEY
openssl rand -base64 32      # ENCRYPTION_KEY (32 bytes, base64)
openssl rand -base64 24      # POSTGRES_PASSWORD
```

`docker compose` picks `.env` up automatically (it has to be next
to `compose.yaml`). Lock the file down:

```bash
sudo chown root:root /srv/labdog/.env
sudo chmod 0600 /srv/labdog/.env
```

For an extra layer, hand the keys to the container as Docker
secrets and read them via the `_FILE` convention or a small
entrypoint shim — but `.env` with `chmod 0600` is the minimum bar.

---

## labdog.toml

The non-secret subset. Mount this into the container at
`/etc/labdog/labdog.toml`. Anything sensitive belongs in the
environment (see above) — env values override TOML values.

```toml
[server]
host = "0.0.0.0"   # listening inside the container; Caddy is upstream
port = 8000

[logging]
level  = "info"
format = "json"     # easier to scrape from a centralised log store
audit_retention_days = 365

[ssh]
max_sessions_per_user = 5
max_total_sessions    = 50

[discovery]
min_prefix    = 22
max_bulk_add  = 100

[ansible]
packs_root_dir = "/var/lib/labdog/packs"

[rate_limit]
enabled = true
login   = "5/minute"
api     = "100/minute"
```

---

## Bring it up

```bash
cd /srv/labdog
docker compose pull
docker compose up -d

# First-boot logs — alembic creates the schema, lifespan syncs packs
docker compose logs -f labdog
```

Open `https://labdog.example.com`, register the first user, and
proceed with [post-install configuration](./README.md#post-install-configuration)
in the main README.

For backups, see [backup-restore.md](./backup-restore.md). The
backup script needs the Postgres credentials and read access to
`labdog.toml` (or whichever env source holds the encryption key) —
it does not need to run inside the container.

---

## Differences from `dev/docker-compose.yml`

The dev compose file is intentionally minimal — it is not a
production blueprint. Concrete differences:

| Concern | `dev/docker-compose.yml` | This file |
|---|---|---|
| TLS | None — plain HTTP on `:8000` | Caddy sidecar with ACME |
| `cookie_secure` | `false` | `true` (via env) |
| `force_https` | unset (`false`) | `true` (via env) |
| Postgres port | Published on host `:5432` | Internal docker network only |
| Backend port | Published on host `:8000` | Internal docker network only |
| Pack volume | None — DB-backed packs lost on restart | `labdog_packs` volume |
| Postgres data | `postgres_data` named volume | Same — kept |
| Secret source | Inline `${VAR:-default}` with insecure fallbacks | `.env`, no defaults |
| Allowed origins | `http://localhost:3000` (config default) | `https://labdog.example.com` |
| Logging format | `text` | `json` |

If you're starting from `dev/docker-compose.yml`, working through
the table left-to-right is a reasonable hardening checklist.
