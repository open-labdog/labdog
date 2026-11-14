# C-060: Bind stored secrets to their row (SEC-50)

| | |
|---|---|
| Edition | community |
| Source | proposed, PR-04 "Bind stored secrets to their row" (accepted 2026-11-14); `BUGS.md` SEC-50 |
| Tier / score | P1 / 4.5 |
| Stage | 7 |
| Effort | M |
| Autonomy | B |
| Depends on | S-004 |

## Goal
A ciphertext copied from one database row or column to another no longer decrypts. Someone with database write access can no longer swap which SSH key or token a host, repository or integration uses without knowing the master key.

## Scope
In scope:
- Passing `"<table>:<id>:<column>"` as AES-GCM associated data on every encrypt and decrypt, inside the S-004 module.
- A migration that re-encrypts existing rows, and a read path that upgrades a row still in the old form.
- The key-rotation script, its test, and deleting SEC-50 from `BUGS.md`.

Out of scope:
- Changing the cipher, nonce size or key handling.
- Secrets kept outside the seven registered columns (the alert webhook token and the JWT secret are in the config file).
- Extension tables. An extension binds its own ciphertexts.

## Context
- `backend/app/crypto/encryption.py:21` (`aad = context_id.encode() if context_id else None` in `encrypt_ssh_key`) and line 35 (the same in `decrypt_ssh_key`) already support associated data. No Community caller passes `context_id`, which is SEC-50 (`BUGS.md` line 220).
- `BUGS.md` records why it was left open: the binding needs the row's id at every call site, and some pass ciphertext without its row (`clone_repo`, `ssh_connect_host`). S-004 removes that obstacle: after it, every read and write goes through `app/crypto/secrets.py` with a `SecretRef(table, row_id, column)`.
- The encrypted columns are the seven in `_build_column_registry()` (`backend/scripts/rotate_encryption_key.py:46`). `rotate()` in the same file (line 143) decrypts and encrypts with no context.
- Rows are sometimes encrypted before they have an id: `backend/app/api/ssh_keys.py:40` and `backend/app/api/proxmox_nodes.py:45` encrypt and then insert, and both columns are NOT NULL (`app/models/ssh_key.py:14`, `app/proxmox/models.py:30`). `app/api/grafana.py:134` does the same on a nullable column.
- `backend/tests/test_rotate_encryption_key.py::test_registry_covers_every_encrypted_column` (line 177) already fails when a model gains an `encrypted_*` column the registry misses.
- Latest migration is `backend/alembic/versions/0060_schedule_owner.py`; C-030 may take 0061.

## Dependencies
- S-004 must be merged first. Do both in the same session if possible: the call sites are the same.
- No external inputs. The migration needs the master key at migration time, which `python -m app` already has (`security.encryption_key`).

## Implementation steps
1. In `backend/app/crypto/secrets.py` add `def context_for(ref: SecretRef) -> str` returning `f"{ref.table}:{ref.row_id}:{ref.column}"`.
2. `store(row, column, plaintext)`: require `row.id` (raise `ValueError` otherwise) and encrypt with `context_for(...)`.
3. Default read path: decrypt with the context. On `cryptography.exceptions.InvalidTag`, try once with no context; if that succeeds, log one warning naming table, id and column (never the value) and return the plaintext. Guard the fallback with a module constant `ALLOW_UNBOUND_FALLBACK = True` and a comment that it is removed in the release after next. If both fail, raise the original `InvalidTag`.
4. Fix the create paths so the id exists before encryption:
   - `app/api/ssh_keys.py` (create): add the row with a placeholder `b""`, `await db.flush()`, then `store(row, "encrypted_private_key", body.private_key)` in the same transaction.
   - `app/api/proxmox_nodes.py` (create, line 45): the same pattern.
   - `app/api/grafana.py:134`, `app/api/git_repos.py:116`, `app/api/ai.py:181`, `app/api/notifications.py:111`, `app/gitops/webhook_secret.py:34`: flush before `store` where the row is new.
   - Grep for every `store(` call and check each has an id; the `ValueError` from step 2 makes a missed one fail in tests.
5. Migration `backend/alembic/versions/00NN_bind_secrets_to_rows.py` (next free number). For each registry column: select `id` and the blob where not NULL; try decrypt with the context (already bound, skip); else decrypt with no context and re-encrypt with it; update by id. Run in the migration's transaction so an interruption leaves everything in the old form. Fail with a clear message, without partial writes, if a blob decrypts neither way (wrong key). Read the key through `app.key_format.decode_master_key` and `settings.security.encryption_key`. Write the column list into the migration as literals; do not import the model registry, because models change after the migration is written.
6. `downgrade()`: re-encrypt each blob without context, so the previous release can read the data again.
7. `backend/scripts/rotate_encryption_key.py::rotate`: decrypt with the context under the old key (falling back to none), encrypt with the context under the new key. Extend the registry tuples or derive the context from `Model.__tablename__`, the row id and the column name.
8. Tests in `backend/tests/test_secret_binding.py`, plus updates to `tests/test_rotate_encryption_key.py` and `tests/test_crypto.py`:
   - copy `ssh_keys.encrypted_private_key` from row A to row B with a raw `UPDATE`; `reveal` on B raises `InvalidTag`;
   - copy `git_repositories.encrypted_https_token` into `encrypted_webhook_secret` on the same row; it does not decrypt;
   - a blob written with no context still reads while the fallback is on, and stops reading when `ALLOW_UNBOUND_FALLBACK` is patched to False;
   - the migration function, run against rows seeded in the old form, leaves every row readable with the fallback off, and is a no-op when run twice;
   - `downgrade()` restores the old form;
   - rotation of bound rows produces bound rows under the new key.
9. Delete the SEC-50 entry from `BUGS.md` (the file is open-only).

## Acceptance criteria
- After `alembic upgrade head` on a database with secrets in all seven columns, every secret reads with `ALLOW_UNBOUND_FALLBACK = False`.
- A raw SQL copy of a ciphertext to another row, or to another encrypted column of the same row, makes the read fail with a decryption error and a log line that names the row; the application does not use the copied secret.
- Creating an SSH key, a Proxmox node, a Grafana instance, a Git repository token, an AI provider key and the SMTP password through the API each produce a ciphertext that reads back with the fallback off.
- `python -m scripts.rotate_encryption_key` on a migrated database leaves every secret readable under the new key with the fallback off.
- `grep -n "SEC-50" BUGS.md` returns nothing.

## Test plan
- `cd backend && source .venv/bin/activate && pytest tests/test_secret_binding.py tests/test_secret_seam.py tests/test_crypto.py tests/test_rotate_encryption_key.py tests/test_webhook_secret_at_rest.py -q`
- Full suite before the stage review: `pytest tests/ -q`; `ruff check . && ruff format --check .`; `bandit -q -r app -b bandit-baseline.json`.
- Migration rehearsal on local Docker on lin-manager-2: restore a copy of a real database dump, start the new image, confirm the migration log reports the count per column, then open a host named "test", run collect-state, test a Proxmox node and a Git repository. Then `alembic downgrade -1`, start the previous image and repeat one read. Never rehearse on the live instance on lin-manager; upgrade it only after the rehearsal passed, with a database backup taken first (`docs/backup-restore.md`).

## Docs and changelog
- `docs/encryption-key-rotation.md`: the rotation script keeps the binding. `docs/security-hardening.md`: one paragraph on what the binding protects against. `docs/upgrade.md`: the upgrade re-encrypts stored secrets, needs the encryption key present, and a database restored into a different install keeps working because ids are preserved; rows copied by hand between tables do not.
- `docs/backup-restore.md`: state that a restore must keep row ids (a plain `pg_restore` does).
- `CHANGELOG.md` `[Unreleased]` / Security: "Stored secrets are bound to their row and column, so a ciphertext moved to another row no longer decrypts (SEC-50). Existing secrets are re-encrypted on upgrade."

## Risks and rollback
- A wrong context string makes a secret unreadable. The fallback read, the idempotent migration and the rehearsal on a real dump guard against this.
- Any tool that copies rows with new ids (a hand-written import, a future "duplicate host" feature) will produce unreadable secrets. Search for code that copies encrypted columns between rows before merging (`grep -rn "encrypted_" backend/app | grep -v "crypto/"`), and make such code re-store through `store()`.
- A downgrade of the application without `alembic downgrade` leaves bound ciphertexts the old code cannot read. The upgrade notes must say so.
- Rollback: `alembic downgrade` to the previous revision (re-encrypts without context), then deploy the previous version.

## Assumptions
- Assumption (not yet confirmed): the context format is `"<table>:<id>:<column>"`, as `BUGS.md` suggests. Renaming a table or column later needs a re-encrypting migration; that is accepted.
- Assumption (not yet confirmed): the unbound fallback stays for one release line and is then removed. Question for Dennis: which release removes it.
- Severity stays Low as filed: the attack needs database write access.

## Definition of done
- [ ] Binding in `app/crypto/secrets.py`, create paths fixed, migration with downgrade, rotation script updated.
- [ ] `tests/test_secret_binding.py` passes; full suite green; rehearsal on lin-manager-2 done and noted in `planning/PROGRESS.md`.
- [ ] SEC-50 deleted from `BUGS.md`; docs and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set to done.
