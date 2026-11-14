# S-004: Secret provider seam for stored credentials

| | |
|---|---|
| Edition | shared |
| Source | roadmap, board item "Secrets from HashiCorp Vault" (the Community half: the seam) |
| Tier / score | P1 / 6.5 |
| Stage | 7 |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal
Every stored credential (SSH private keys, Proxmox and Grafana tokens, Git tokens and webhook secrets, AI provider keys, the SMTP password) is read and written through one small module that knows which row and column it belongs to. An extension can register a provider that answers for a credential from somewhere else. With no provider registered, behaviour is exactly as today.

## Scope
In scope:
- A new module `backend/app/crypto/secrets.py` with `reveal`, `reveal_sync`, `store` and `register_secret_provider`.
- Moving every `encrypt_ssh_key` / `decrypt_ssh_key` call in `backend/app/` (outside `app/crypto/`) to it.
- A guard test that fails when a new direct call appears, and a `PLUGIN_API` minor bump.

Out of scope:
- Binding ciphertexts to their row (C-060, the next item; it reuses the row identity this item threads through).
- Any external secret store. The Enterprise feature "Secrets from HashiCorp Vault" will use this hook.
- Changing the encryption algorithm, the key format, or the rotation script's behaviour.

## Context
- `backend/app/crypto/encryption.py` has two functions, `encrypt_ssh_key(plaintext_key, master_key, context_id="")` (line 9) and `decrypt_ssh_key(encrypted_data, master_key, context_id="")` (line 27). Despite the names they encrypt every kind of secret. `backend/app/crypto/key_management.py::get_master_key` supplies the key.
- There are 63 calls in 42 files under `backend/app/` (counted with an AST walk on 2026-11-14; `BUGS.md` SEC-50 says 67 and the plan says 52 files, so recount before starting), plus `backend/scripts/rotate_encryption_key.py`.
- Seven encrypted columns exist, listed in `_build_column_registry()` (`backend/scripts/rotate_encryption_key.py:46`): `ssh_keys.encrypted_private_key`, `proxmox_nodes.encrypted_token_secret`, `git_repositories.encrypted_https_token`, `git_repositories.encrypted_webhook_secret`, `grafana_instances.encrypted_token`, `ai_providers.encrypted_api_key`, `smtp_settings.encrypted_password` (take the real table names from each model's `__tablename__`).
- Eight calls are in synchronous functions: `app/gitops/git_service.py` (`_clone_ssh` line 76, `_clone_https` line 140), `app/gitops/webhook_secret.py` (lines 34, 46), `app/ai/snapshots.py::client_for` (108), `app/notifications/service.py::smtp_config` (80), `app/grafana/service.py::client_for_instance` (61), `app/ai/providers/factory.py::decrypt_api_key` (48). The rest are in `async def`.
- Some call sites receive ciphertext without its row: `clone_repo(..., encrypted_ssh_key=...)` in `app/gitops/git_service.py:20`, and callers that pass `ssh_key.encrypted_private_key` down. `app/ssh_utils.py::load_host_key` (line 493) is an existing place that loads the row and decrypts in one step.
- Extensions load in the API (`app/main.py:669`) and in Celery workers (`app/tasks/__init__.py:140`), so a provider registered in an extension's `register(app)` is present wherever a secret is read.
- The plugin contract is versioned by `PLUGIN_API` in `backend/app/auth/permissions.py` (1.2 today; earlier stages add minors).

## Dependencies
- None to start. C-060 builds directly on this and should be done by the same session, right after.
- The low-level functions in `encryption.py` keep their signatures: an extension already calls them directly with its own `context_id`.

## Implementation steps
1. Create `backend/app/crypto/secrets.py`:
   - `@dataclass(frozen=True) class SecretRef: table: str; row_id: int; column: str`, with `SecretRef.of(row, column)` reading `row.__tablename__` and `row.id` (raise `ValueError` when `row.id` is None).
   - `class SecretProvider(Protocol): name: str; def resolve(self, ref: SecretRef, stored: bytes | None) -> str | None`. Returning `None` means "not mine, ask the next one". A provider that owns the reference and fails raises `SecretUnavailable(ref, reason)`.
   - `register_secret_provider(provider)`, `reset_secret_providers()` (for tests), a module-level list consulted in registration order, then the built-in default.
   - Default behaviour: `decrypt_ssh_key(stored, get_master_key())`, exactly today's call.
   - `def reveal_sync(row, column) -> str | None` and `async def reveal(row, column) -> str | None`. Both return `None` when the column is NULL and no provider answers. `reveal` runs registered providers through `asyncio.to_thread` (they may do network I/O) and the default inline.
   - `def store(row, column, plaintext: str | None) -> None`: sets the column to `encrypt_ssh_key(plaintext, get_master_key())`, or NULL for an empty value on a nullable column. It does not need `row.id` yet (C-060 adds that).
2. Replace call sites, one module group per commit so each diff is reviewable: `app/api/*`, `app/tasks/*`, `app/sync/*`, `app/ai/*`, `app/gitops/*`, `app/proxmox/*`, `app/grafana/*`, `app/notifications/*`, `app/packs/*`, `app/workflows/*`, `app/ssh_utils.py`, `app/ssh_terminal/ssh_connect.py`. Rule: reveal where the row is loaded and pass the plaintext down. Functions that took ciphertext (`clone_repo`'s `encrypted_ssh_key`, `_clone_ssh`) change to take the row or the plaintext; update their callers and tests.
3. Do not log, cache or put a revealed value on an ORM object. Keep each plaintext in a local variable as today.
4. `backend/scripts/rotate_encryption_key.py` keeps calling the low-level functions: it rewrites whatever blob is stored and must not ask an external provider. Add a comment saying so.
5. Bump `PLUGIN_API`'s minor in `backend/app/auth/permissions.py` and add a line to the comment above it: "`app.crypto.secrets`: `SecretRef`, `SecretProvider`, `register_secret_provider`, `SecretUnavailable`". Add a short section to `CLAUDE.md` under "Extensions (plugins) and directory sign-in".
6. Tests in `backend/tests/test_secret_seam.py`:
   - default path round-trips each of the seven columns through `store` and `reveal`;
   - a registered fake provider is asked with the right `SecretRef`, its answer wins, and `None` falls through to the default;
   - `SecretUnavailable` surfaces as an error the caller already handles (a sync fails with a clear message, it does not fall back to the stored blob);
   - a guard test walks `backend/app/**/*.py` with `ast` and fails if `encrypt_ssh_key` or `decrypt_ssh_key` is called outside `app/crypto/`.
7. Update existing tests that patch `decrypt_ssh_key` in a call-site module (grep `tests/` for `decrypt_ssh_key`) to patch `app.crypto.secrets` instead.

## Acceptance criteria
- `grep -rn "decrypt_ssh_key(\|encrypt_ssh_key(" backend/app --include=*.py` shows matches only under `backend/app/crypto/`.
- With no provider registered, the full backend suite passes without changes to expected values, and ciphertexts written before the change still decrypt (a test stores a blob with the old direct call and reads it with `reveal`).
- A fake provider registered in a test is used by a host SSH connection path (`app/ssh_utils.py::load_host_key`) and by a Celery task path (`app/tasks/facts.py` or `app/tasks/drift_sweep.py`), without the stored column being decrypted.
- `PLUGIN_API`'s minor is one higher and the comment above it names the new hook.
- `tests/test_rotate_encryption_key.py` still passes unchanged.

## Test plan
- `cd backend && source .venv/bin/activate && pytest tests/test_secret_seam.py tests/test_crypto.py tests/test_rotate_encryption_key.py tests/test_webhook_secret_at_rest.py tests/test_packs_git_auth.py -q`
- Full suite before the stage review: `pytest tests/ -q`; `ruff check . && ruff format --check .`; `bandit -q -r app -b bandit-baseline.json`.
- On the local Docker stack on lin-manager-2, with a database restored from before the change: open a host named "test", run collect-state and a sync plan, test a Proxmox node, a Grafana instance and a Git repository connection, and send a test email. Each proves one column still reads.

## Docs and changelog
- `CLAUDE.md`: the hook, in the plugin contract section. `docs/encryption-key-rotation.md`: one sentence that rotation rewrites only what is stored in the database.
- `CHANGELOG.md` `[Unreleased]` / Changed: "Stored credentials are read through one module (`app.crypto.secrets`), which an extension can extend. No behaviour change."

## Risks and rollback
- A missed or wrongly converted call site breaks one integration at runtime, not at import. The guard test, the per-column round-trip tests and the manual pass on lin-manager-2 cover this.
- A provider that blocks would stall the event loop; `reveal` runs providers in a thread for that reason.
- Rollback: revert the PR. No data changes, so nothing to migrate back.

## Assumptions
- Assumption (not yet confirmed): the provider interface is synchronous and keyed by `(table, row_id, column)`; an extension keeps its own mapping from that key to its external path. Question for Dennis: none, unless a different reference format is wanted in the UI.
- Assumption (not yet confirmed): writes always go to the database column (`store`); an extension that holds a secret elsewhere leaves a placeholder there. The seam does not offer a "write to the external store" call.
- [D-18](../DECISIONS.md): the Enterprise feature is mentioned by its public title only.

## Definition of done
- [ ] `app/crypto/secrets.py` in place; no direct calls left outside `app/crypto/`.
- [ ] `tests/test_secret_seam.py` and the guard test pass; full suite green.
- [ ] `PLUGIN_API` bumped with its comment; `CLAUDE.md` and `CHANGELOG.md` updated.
- [ ] `planning/roadmap.yaml` status set to done; one line in `planning/PROGRESS.md`.
