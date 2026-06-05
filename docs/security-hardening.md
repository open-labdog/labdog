# Security Hardening

Production checklist for a LabDog install. The defaults are tuned
for development — they assume `localhost`, plaintext HTTP, and a
single trusted user. Before exposing the service beyond the loopback
interface, work through every section here.

- [HTTPS termination](#https-termination)
  - [nginx](#nginx)
  - [Caddy](#caddy)
- [Cookie and HTTPS settings](#cookie-and-https-settings)
- [Security headers and CSP](#security-headers-and-csp)
- [Rate limiting](#rate-limiting)
- [Superuser scope](#superuser-scope)
- [Database connection security](#database-connection-security)
- [Encryption-key handling](#encryption-key-handling)

---

## HTTPS termination

LabDog does not terminate TLS itself in the default deployment.
Bind the backend to `127.0.0.1:8000` and put a reverse proxy in
front of it. **Do not expose `0.0.0.0:8000` directly on a public
interface** — it has no TLS, no rate limiting at the network
layer, and no request-size cap.

Set `[server] host = "127.0.0.1"` in `/etc/labdog/labdog.toml` (or
`LABDOG_SERVER__HOST=127.0.0.1` in the environment) on production
hosts.

### nginx

```nginx
# /etc/nginx/sites-available/labdog
server {
    listen 80;
    server_name labdog.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name labdog.example.com;

    ssl_certificate     /etc/letsencrypt/live/labdog.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/labdog.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;

    client_max_body_size 10m;

    # WebSocket upgrade for the in-browser SSH terminal
    location /api/ssh-terminal/ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 1h;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Tell LabDog to trust the proxy's `X-Forwarded-*` headers:

```toml
[server]
host = "127.0.0.1"
trusted_proxies = ["127.0.0.1"]
forwarded_allow_ips = "127.0.0.1"
```

### Caddy

Caddy provisions and renews ACME certificates automatically:

```caddy
# /etc/caddy/Caddyfile
labdog.example.com {
    encode gzip

    # WebSocket for the SSH terminal
    @ws {
        path /api/ssh-terminal/ws/*
    }
    reverse_proxy @ws 127.0.0.1:8000

    reverse_proxy 127.0.0.1:8000 {
        header_up X-Real-IP {remote}
        header_up X-Forwarded-For {remote}
        header_up X-Forwarded-Proto {scheme}
    }

    request_body {
        max_size 10MB
    }
}
```

---

## Cookie and HTTPS settings

Once HTTPS is in place, flip the two settings that opt LabDog into
secure-cookie mode and HTTP→HTTPS redirects. Both are disabled by
default (development uses plain HTTP).

```toml
[security]
cookie_secure = true        # JWT cookie only sent over HTTPS

[tls]
force_https = true          # 301 redirect any plain-HTTP request
```

Or via environment:

```bash
LABDOG_SECURITY__COOKIE_SECURE=true
LABDOG_TLS__FORCE_HTTPS=true
```

When either flag is true, LabDog also emits an HSTS header
(`Strict-Transport-Security: max-age=63072000; includeSubDomains`).
Don't enable it before the certificate works — HSTS pinning a
broken setup locks browsers out for the max-age window.

`session_lifetime_seconds` defaults to 86 400 (24 h). Lower it for
high-sensitivity environments; users will re-login more often.

---

## Security headers and CSP

LabDog's `SecurityHeadersMiddleware` (in `backend/app/main.py`)
already emits a baseline:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `X-XSS-Protection: 1; mode=block`
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()`
- `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'`
- `Strict-Transport-Security: max-age=63072000; includeSubDomains` (only when HTTPS is on)

The frontend is built with Tailwind, which inlines styles via
runtime-generated `<style>` tags — `'unsafe-inline'` for `style-src`
is intentional and cannot be tightened without breaking the UI.
The same caveat applies to `script-src` for now (Next.js inlines a
small bootstrap script).

If you want a stricter CSP enforced at the proxy layer (overriding
the app's header), nginx makes that straightforward:

```nginx
# Replace the application-supplied CSP with a stricter one
proxy_hide_header Content-Security-Policy;
add_header Content-Security-Policy "default-src 'self'; \
    script-src 'self' 'unsafe-inline'; \
    style-src 'self' 'unsafe-inline'; \
    img-src 'self' data:; \
    font-src 'self' data:; \
    connect-src 'self' wss://labdog.example.com; \
    frame-ancestors 'none'; \
    base-uri 'self'; \
    form-action 'self'" always;
```

`connect-src` must include the `wss://` origin you serve from, or
the in-browser SSH terminal will fail to connect.

---

## Rate limiting

LabDog applies application-level rate limits when `[rate_limit]
enabled = true` (the default in production configs). The limits are
configurable:

```toml
[rate_limit]
enabled = true
login = "5/minute"
api   = "100/minute"
```

Add a network-layer cap at the reverse proxy as a second line of
defence. nginx `limit_req_zone` for the auth endpoints:

```nginx
# /etc/nginx/conf.d/labdog-ratelimit.conf
limit_req_zone $binary_remote_addr zone=labdog_auth:10m rate=10r/m;
limit_req_zone $binary_remote_addr zone=labdog_api:10m  rate=120r/m;

# Inside server { ... } block in the labdog vhost:
location /api/auth/jwt/login {
    limit_req zone=labdog_auth burst=5 nodelay;
    proxy_pass http://127.0.0.1:8000;
    # ...same proxy_set_header lines as the catch-all location
}

location /api/ {
    limit_req zone=labdog_api burst=20 nodelay;
    proxy_pass http://127.0.0.1:8000;
    # ...same proxy_set_header lines
}
```

The proxy-layer limits should be more permissive than the
application-layer ones — they exist to protect the service against
flood traffic that doesn't make it past the proxy at all.

---

## Superuser scope

LabDog has two user roles. The **only** difference between them is
the ability to manage other users:

- **Superuser** (`is_superuser=True`): can create, edit, delete,
  and reset passwords for other users via the admin UI at
  `/admin/users` (`backend/app/api/admin_users.py`).
- **Regular user** (`is_active=True`, `is_superuser=False`): can do
  everything else — browse hosts, edit groups, manage SSH keys,
  configure git repos and action packs, trigger syncs, run
  actions, open SSH terminals, change application settings, view
  audit logs.

This is the right model for a single-operator homelab install and
small-team setups. There is **no per-host scope mechanism** — a
regular user has the same operational access to every managed host
as a superuser. If you need finer-grained authz (e.g. user X can
only touch host group Y), it doesn't exist yet; track the request
via a GitHub issue.

The first registered user is **atomically promoted** to superuser
during the same transaction as the user-row insert (see
`backend/app/auth/users.py:UserManager.create`). There is no
setup-time superuser; the registration endpoint closes after the
first user is created.

---

## Database connection security

If Postgres runs on a separate host from the LabDog backend,
require TLS on the connection:

```toml
[database]
url = "postgresql+asyncpg://labdog:<password>@db.example.com:5432/labdog?ssl=require"
```

(asyncpg accepts `ssl=require` / `ssl=verify-full` as URL params.
For `verify-full`, also configure a CA bundle on the LabDog host.)

Restrict the `labdog` DB role to the `labdog` schema and database;
do not give it `SUPERUSER` or `CREATEDB`. A representative grant
script run as the Postgres superuser:

```sql
-- Run as postgres
CREATE DATABASE labdog;
CREATE USER labdog WITH PASSWORD '<strong random password>';
GRANT CONNECT ON DATABASE labdog TO labdog;
\c labdog
GRANT USAGE, CREATE ON SCHEMA public TO labdog;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO labdog;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO labdog;
```

Alembic migrations run as this role, so it needs DDL on its own
schema — but nothing more.

For same-host deployments where Postgres is reachable only over a
Unix socket or `127.0.0.1`, TLS is optional; the schema-restriction
guidance still applies.

---

## Encryption-key handling

`security.encryption_key` decrypts every credential LabDog stores
(SSH private keys, Proxmox API tokens, git HTTPS PATs, Grafana bearer
tokens). It is the single most sensitive piece of state in the system.

- **Never commit** `/etc/labdog/labdog.toml` or any backup of it to
  a git repo. If it lands in git history, treat every stored
  credential as compromised and rotate.
- **Never paste** the key into chat, email, ticket attachments,
  screenshots, or wiki pages.
- **Store** the key in a secret manager (HashiCorp Vault, AWS
  Secrets Manager, GCP Secret Manager, Bitwarden, 1Password, etc.)
  alongside your other production secrets. Inject it into the
  process via `LABDOG_SECURITY__ENCRYPTION_KEY` rather than leaving
  it in the on-disk TOML — file-system perms are easier to get
  wrong than a secrets-manager IAM policy.
- **Back it up** together with the database (see
  [backup-restore.md](./backup-restore.md)). Database without key =
  unreadable ciphertext. Key without database = key with nothing
  to decrypt.

For periodic rotation of the key (re-encrypting every column under
a fresh key), see `docs/encryption-key-rotation.md`. Until that
runbook lands, the documented response to a suspected key leak is
the truncate-and-re-enter procedure in
[backup-restore.md → Lost the encryption key](./backup-restore.md#lost-the-encryption-key):
generate a new key, restart, then clear encrypted rows and re-enter
credentials from the UI.
