# Encryption-Key Rotation

This document explains when and how to rotate `security.encryption_key` —
the AES-256-GCM master key that protects every encrypted credential stored in
LabDog's database (`ssh_keys.encrypted_private_key`,
`proxmox_nodes.encrypted_token_secret`,
`git_repositories.encrypted_https_token`, and
`grafana_instances.encrypted_token`).

---

## When to rotate

- **Suspected key leak** — the key was logged, transmitted in plain text, or
  otherwise may have been observed by an unauthorised party.
- **Key compromise** — a backup containing the key was stolen or a host that
  held the key is considered untrusted.
- **Organisational policy** — your security policy mandates periodic key
  rotation (cadence is out of scope for this document; follow your policy).

---

## Pre-rotation checklist

1. **Back up the database** — a snapshot before rotation is the only
   safe rollback path.  See [backup-restore.md](./backup-restore.md) for the
   full backup procedure.
2. **Generate a new key**:

   ```
   python -m app.crypto.key_management
   ```

   This prints a fresh base64-encoded 32-byte key (standard alphabet).
   Url-safe base64 is also accepted at load time if you prefer
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
   Store it somewhere secure (password manager, secrets vault) before
   proceeding.

3. **Have both keys to hand** — the old key (current value of
   `LABDOG_SECURITY__ENCRYPTION_KEY` in production) and the new key generated
   above.  Do not discard the old key until step 6 passes.

---

## Procedure

### 1. Stop the API and Celery workers

No traffic should read or write encrypted columns while rotation is in
progress.

**.deb / systemd install:**

```
systemctl stop labdog labdog-celery
```

**Docker Compose:**

```
docker compose stop api celery
```

### 2. Run the rotation script

The script connects to the database, checks that all migrations are applied,
then re-encrypts every encrypted column in a single transaction.

```
cd /path/to/labdog/backend
LABDOG_OLD_KEY=<old-base64-key> \
LABDOG_NEW_KEY=<new-base64-key> \
    .venv/bin/python -m scripts.rotate_encryption_key
```

You can also pass the keys as flags if you prefer not to put them in the
environment:

```
.venv/bin/python -m scripts.rotate_encryption_key \
    --old-key <old-base64-key> \
    --new-key <new-base64-key>
```

### 3. Verify the script exits 0 and reports expected row counts

Successful output looks like:

```
  ssh_keys: 4 row(s) rotated
  proxmox_nodes: 2 row(s) rotated
  git_repositories: 1 row(s) rotated
  grafana_instances: 1 row(s) rotated
Key rotation complete.
```

The counts should match the number of credentials stored in each table.
A zero count for a table is normal if that table has no rows.  Any non-zero
exit code means the rotation did not complete — see [Failure modes](#failure-modes).

### 4. Update the production configuration

Replace the value of `LABDOG_SECURITY__ENCRYPTION_KEY` (or `[security]
encryption_key` in `labdog.toml`) with the new key.

**.deb / systemd install** — edit `/etc/labdog/labdog.toml` and set:

```toml
[security]
encryption_key = "<new-base64-key>"
```

**Docker Compose** — update the environment variable in your compose file or
`.env` and apply it:

```
docker compose up -d api celery
```

Do **not** restart LabDog before updating the key — it will fail to decrypt
the credentials it just had re-encrypted.

### 5. Restart the API and Celery workers

**.deb / systemd install:**

```
systemctl start labdog labdog-celery
```

**Docker Compose:** restart is handled by `docker compose up -d` in step 4.

### 6. Verify correct operation

- Log in to the LabDog web interface.
- Navigate to **Admin → SSH Keys** and confirm the list loads without error.
- Trigger a sync against a known-reachable host and confirm it completes
  successfully.
- Check the audit log for that sync — look for a successful sync entry with
  no decryption-related errors.

---

## Rollback

If step 6 fails, the old key is still valid — rotation does not destroy the
old key, the operator does.  To roll back:

1. Revert `LABDOG_SECURITY__ENCRYPTION_KEY` to the old key in the production
   configuration.
2. Restart the API and Celery workers.
3. Retry step 6 to confirm the service is healthy under the old key.
4. Investigate why the rotation produced unreadable data before trying again.

---

## Failure modes

**Wrong old key**
The script attempts to decrypt the first encrypted row using the supplied old
key.  If the key is wrong, `cryptography.exceptions.InvalidTag` is raised,
the transaction is rolled back, and the script exits with a non-zero code and
the message:

```
ERROR: decryption failed — wrong old key or corrupt data.
  ...
Transaction rolled back; the database is unchanged.
```

The database is unmodified.  Double-check the value of the old key and retry.

**Script killed mid-run**
If the process is killed while the transaction is open (SIGKILL, OOM, host
crash), PostgreSQL rolls back the open transaction automatically.  The
database remains entirely under the old key.  Retry from step 1.

**New key not 32 bytes**
The CLI validates both keys before touching the database.  A key that does
not decode to exactly 32 bytes is rejected immediately:

```
ERROR: new key (--new-key / LABDOG_NEW_KEY) must decode to exactly 32 bytes, got N
```

Generate a correct key with `python -m app.crypto.key_management` and retry.

**Pending migrations**
The script refuses to run if the database has unapplied Alembic migrations:

```
ERROR: database has unapplied migrations.
  Applied : [...]
  Expected: [...]
Run 'alembic upgrade head' before rotating the key.
```

Run `alembic upgrade head` from the `backend/` directory and retry.
