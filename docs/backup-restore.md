# Backup & Restore

Two pieces of state must survive a disaster: the PostgreSQL
database and the **encryption key** in
`[security] encryption_key`. Everything else — the frontend,
the backend code, the packaged binary — can be reinstalled from
a release artifact.

> **Read this first:** losing the encryption key while keeping
> the database is almost as bad as losing the database. Every
> encrypted column (SSH private keys, Proxmox API tokens, pack
> credentials) is decryptable only with that key. Back them up
> **together** and restore them **together**.

- [What to back up (and what NOT to)](#what-to-back-up-and-what-not-to)
- [Backing up](#backing-up)
  - [PostgreSQL](#postgresql)
  - [The encryption key](#the-encryption-key)
  - [What a complete backup set looks like](#what-a-complete-backup-set-looks-like)
  - [Automating daily backups](#automating-daily-backups)
- [Restoring](#restoring)
  - [Fresh host, existing backup](#fresh-host-existing-backup)
  - [Same host, point-in-time restore](#same-host-point-in-time-restore)
  - [After restore: what to verify](#after-restore-what-to-verify)
- [Disaster scenarios](#disaster-scenarios)
  - [Lost the encryption key](#lost-the-encryption-key)
  - [Lost the database, still have the encryption key](#lost-the-database-still-have-the-encryption-key)
  - [Both lost](#both-lost)
- [Security notes for backup handling](#security-notes-for-backup-handling)

---

## What to back up (and what NOT to)

| State | Back up? | Where | Notes |
|---|---|---|---|
| PostgreSQL database | **Yes — critical** | `postgresql+asyncpg://…` from `[database] url` | Holds every host, group, rule, audit entry, and all encrypted credentials. |
| `security.encryption_key` | **Yes — critical** | `labdog.toml` (`/etc/labdog/labdog.toml` on package installs) | 32-byte AES-256-GCM key in base64. Without it, the encrypted columns in the DB are unrecoverable. |
| `security.secret_key` | Recommended | same file | JWT signing key. Losing it invalidates all existing sessions but doesn't lose data — users re-login. |
| Ansible pack checkouts | **No** | `/var/lib/labdog/packs/<id>/` | Re-cloned from the pack's linked `GitRepository` on next sync. |
| Logs | Optional | `/var/log/labdog/` or `journalctl -u labdog` | Only for incident forensics; not required for service restoration. |
| Frontend build | **No** | `/usr/lib/labdog/frontend/out/` | Comes from the release artifact. |
| Backend venv | **No** | `/usr/lib/labdog/venv/` | Comes from the release artifact. |

---

## Backing up

### PostgreSQL

Use `pg_dump` in custom format (faster restore, built-in compression,
lets you restore individual objects):

```bash
# As the labdog DB user, or any role with read access to the labdog db
pg_dump \
  --host=localhost \
  --username=labdog \
  --dbname=labdog \
  --format=custom \
  --file=/var/backups/labdog/labdog-$(date +%Y%m%d-%H%M%S).dump
```

For a plain SQL dump (human-readable, easier to inspect and
diff, but larger on disk):

```bash
pg_dump --format=plain --clean --if-exists --dbname=labdog > labdog.sql
```

**What's inside:** every LabDog table, including `ssh_keys`,
`proxmox_nodes`, `action_packs`, and `git_repositories` with
their `encrypted_*` columns. **The dump itself does not contain
the encryption key.** The ciphertext alone is useless without
the key — that's the whole point — which is why you must back up
both.

### The encryption key

The key lives in `labdog.toml` under `[security]`:

```toml
[security]
encryption_key = "Y2ktdGVzdC1lbmNyeXB0aW9uLWtleS0zMmJ5dGVz"  # NEVER this value in prod
```

The whole file is worth backing up (it also contains
`secret_key`, DB URL, TLS paths, etc.), but the line that
absolutely must survive is `encryption_key`.

Simplest snapshot:

```bash
sudo cp /etc/labdog/labdog.toml /var/backups/labdog/labdog.toml-$(date +%Y%m%d)
sudo chmod 600 /var/backups/labdog/labdog.toml-*
```

For an encrypted offsite copy, encrypt the file with `age` or
GPG before it leaves the host. The key is roughly 32 bytes of
base64 — don't let it end up in a chat log, a wiki page, or a
screenshot by accident.

### What a complete backup set looks like

A restorable snapshot at time `T`:

```
/var/backups/labdog/
├── labdog-YYYYMMDD-HHMMSS.dump           (pg_dump custom format)
└── labdog.toml-YYYYMMDD                   (config with encryption_key)
```

If you only have the first, you have unreadable ciphertext.
If you only have the second, you have a key and no data.

### Automating daily backups

systemd timer is the usual choice on the packaged install. Drop
this at `/etc/systemd/system/labdog-backup.service` and
`labdog-backup.timer`:

```ini
# labdog-backup.service
[Unit]
Description=Nightly LabDog backup
Requires=postgresql.service
After=postgresql.service

[Service]
Type=oneshot
User=labdog
Group=labdog
UMask=0077
ExecStart=/usr/local/bin/labdog-backup.sh
```

```ini
# labdog-backup.timer
[Unit]
Description=Run LabDog backup nightly

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
RandomizedDelaySec=15m

[Install]
WantedBy=timers.target
```

And the script at `/usr/local/bin/labdog-backup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR=/var/backups/labdog
KEEP_DAYS=14
STAMP=$(date +%Y%m%d-%H%M%S)

install -d -m 0700 -o labdog -g labdog "$BACKUP_DIR"

# Database
pg_dump --dbname=labdog --format=custom \
  --file="$BACKUP_DIR/labdog-$STAMP.dump"

# Config (includes encryption_key)
install -m 0600 -o labdog -g labdog \
  /etc/labdog/labdog.toml "$BACKUP_DIR/labdog.toml-$STAMP"

# Prune anything older than KEEP_DAYS
find "$BACKUP_DIR" -mtime +$KEEP_DAYS -type f -delete
```

```bash
sudo systemctl enable --now labdog-backup.timer
```

Then push `$BACKUP_DIR` offsite (rclone to S3 / restic to B2 /
borg to a remote — whatever you already operate).

---

## Restoring

### Fresh host, existing backup

This is the common case: new machine, install the .deb / .rpm,
restore data from the backup set.

```bash
# 1. Install LabDog from the same version you backed up from
#    (newer minor versions work, but pick the same version first
#    then upgrade afterwards to keep the trail clear).
sudo apt install ./labdog_0.1.0-1_amd64.deb

# 2. Restore the config BEFORE starting the service so the
#    encryption_key is in place when migrations run.
sudo install -m 0640 -o root -g labdog \
  labdog.toml-20260425 /etc/labdog/labdog.toml

# 3. Create the target database (empty) and restore.
sudo -u postgres createdb labdog
pg_restore \
  --dbname=labdog \
  --clean --if-exists --no-owner --no-acl \
  labdog-20260425-030000.dump

# 4. Apply any pending migrations (safe on a restored-from-older-
#    version dump; noop if the dump already had the latest schema).
sudo -u labdog /usr/lib/labdog/venv/bin/alembic \
  -c /usr/lib/labdog/backend/alembic.ini upgrade head

# 5. Start the service.
sudo systemctl enable --now labdog.service
sudo journalctl -u labdog -f
```

Then hit the UI, log in with an existing account (passwords
came back with the dump), and confirm hosts, groups, and rules
are present.

### Same host, point-in-time restore

Useful when something went wrong and you want to roll back
without wiping the rest of the machine:

```bash
# Stop the service so nothing writes during restore
sudo systemctl stop labdog.service

# Drop the corrupt DB, recreate, restore
sudo -u postgres dropdb labdog
sudo -u postgres createdb labdog
pg_restore --dbname=labdog --clean --if-exists --no-owner --no-acl \
  /var/backups/labdog/labdog-20260425-030000.dump

# Migrations (idempotent)
sudo -u labdog /usr/lib/labdog/venv/bin/alembic \
  -c /usr/lib/labdog/backend/alembic.ini upgrade head

# Back up
sudo systemctl start labdog.service
```

The `encryption_key` in `/etc/labdog/labdog.toml` did not change,
so encrypted columns decrypt correctly against the restored DB.

### After restore: what to verify

1. **Log in and list hosts** — `/hosts` should show every host
   from the backup. If you see "Failed to load hosts" or an
   empty list, check `journalctl -u labdog` for the real error;
   a corrupt restore will usually throw during row
   materialisation.
2. **Hit an encrypted field** — open one of your SSH keys at
   `/ssh-keys`, or test a Proxmox node connection at
   `/hypervisors`. If it works, decryption is wired correctly:
   DB + key are a matching pair.
3. **Check audit log** — `/audit` should show pre-disaster
   events. If it's empty, the dump was incomplete.
4. **Trigger a sync against a test host** — end-to-end
   confirmation that SSH keys + host records survived together.

---

## Disaster scenarios

### Lost the encryption key

You have the DB but not the key. Every encrypted column is
cryptographically opaque — by design. **You cannot decrypt
existing credentials without the key.** Recovery path:

1. Generate a new key and put it in `labdog.toml`:
   ```bash
   python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
   ```
2. Start the service. LabDog will come up, but every existing
   SSH key, Proxmox token, and pack credential is unreadable —
   sync operations will fail with decryption errors.
3. **Clear the rows and re-enter.** For each table, `TRUNCATE`
   (or `DELETE` rows), then re-upload SSH keys via the UI, add
   Proxmox nodes again, reconnect git repos. Host records
   themselves survive (they don't hold encrypted data), but
   you'll need to reattach SSH keys to them:

   ```sql
   -- Run as the labdog DB user; adjust to your situation
   DELETE FROM ssh_keys;
   DELETE FROM proxmox_nodes;
   UPDATE git_repositories
     SET encrypted_https_token = NULL, ssh_key_id = NULL;
   UPDATE hosts SET ssh_key_id = NULL;
   ```

   Then log in and reconfigure from the UI.

Treat this as a lesson: **the key is part of the backup, not
separate from it**. There is no "forgot my key" recovery path
that preserves the data.

### Lost the database, still have the encryption key

You have the key and the config but the DB is gone or corrupt.
Restore from the most recent `pg_dump` (see
[Same host, point-in-time restore](#same-host-point-in-time-restore)).
The key decrypts the restored ciphertext without any additional
steps.

If you don't have any DB backup: LabDog is going to look like
a fresh install. Re-register the first user, reconfigure from
scratch. Host records, groups, rules, audit log — all gone.
Host OS's themselves are unaffected (LabDog only manages them,
it doesn't own their state), so rediscovery + reimport gets
them back in the inventory even if the rule history doesn't
return.

### Both lost

Fresh install. See `docs/README.md` installation section.

---

## Security notes for backup handling

- Backups contain encrypted credentials **and** (separately) the
  key that decrypts them. Together they are as sensitive as
  giving someone direct access to the LabDog server. Treat them
  as such: restricted filesystem perms, encrypted offsite,
  access logged.
- The `pg_dump` files alone are *less* sensitive than the live
  system because ciphertext without the key is opaque — but
  they still contain hashed user passwords (fastapi-users uses
  bcrypt; still don't leave the file world-readable), hostnames
  and IP addresses of managed infrastructure, and audit trails.
- When rotating the encryption key (see
  [`encryption-key-rotation.md`](encryption-key-rotation.md)), make
  sure your backup set for the rotation window contains *both* the
  old and new keys until every live row has been re-encrypted
  under the new key. Otherwise a mid-rotation restore can't
  read rows that were still ciphertext-under-old-key.
- Never commit `/etc/labdog/labdog.toml` or a backup file to a
  git repo. If it happens by accident, treat every currently-
  stored credential as compromised. The structured rotation path
  is in [`encryption-key-rotation.md`](encryption-key-rotation.md);
  for installs with few credentials, the truncate-and-re-enter
  procedure under [Lost the encryption key](#lost-the-encryption-key)
  is also valid — generate a new key, restart, then clear the
  encrypted rows and re-enter credentials from the UI.
