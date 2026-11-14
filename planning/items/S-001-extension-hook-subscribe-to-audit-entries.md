# S-001: Extension hook: subscribe to audit entries

| | |
|---|---|
| Edition | shared |
| Source | roadmap, split from the board item "Send the audit log to a SIEM" (the Community-side hook) |
| Tier / score | P0 / 13.0 |
| Stage | 1 |
| Effort | S |
| Autonomy | B |
| Depends on | C-008 |

## Goal

An installed extension can register a subscriber and is handed every audit entry once its
transaction has committed, and none that was rolled back. A Community install with no
extension behaves exactly as before. Enterprise audit export will be the first user.

## Scope

In scope:
- `register_audit_subscriber()` and an immutable `AuditEntry` value in the audit package.
- Delivery after commit, in the API process and in Celery workers.
- Isolation: a subscriber that raises is logged and never affects the request or task.
- `PLUGIN_API` 1.3, with the comment above it and `CLAUDE.md` updated.

Out of scope:
- Any consumer of the hook, any outbox, retry or network delivery. Those belong to the
  extension.
- Changing what is audited (C-008 and C-047 do that) or the `audit_log` schema.
- A guarantee that survives a process crash between commit and delivery (see Risks).

## Context

- `backend/app/audit/logger.py` holds `log_action`. It is the only place in `backend/app`
  that constructs `AuditLog(` (checked with grep), so it is the single choke point. It
  adds the row to the caller's session and leaves the commit to the caller.
- `backend/app/db_after_commit.py` already has what delivery needs:
  `run_after_commit(db, callback)` stores a callback in `session.info`, a listener on
  `Session` `after_commit` runs the callbacks and swallows their exceptions, and a
  listener on `after_transaction_end` drops them when the outermost transaction rolls
  back. `backend/app/db.py` imports the module (line 175), so the listeners are active
  for `get_db` sessions and for `task_session()` in workers.
- Six places in `backend/app` use `begin_nested()`. A callback registered inside a
  savepoint that is then rolled back still runs when the outer transaction commits, so
  delivery must check that the row was really stored.
- Extensions load in the API process (`backend/app/main.py`, line 669) and in every
  Celery worker process (`backend/app/tasks/__init__.py`, line 140, a
  `worker_process_init` handler). A subscriber registered in `register(app)` is therefore
  present wherever audit rows are written, including `sync_completed` from
  `backend/app/tasks/host_sync_orchestrator.py`.
- The contract version is `PLUGIN_API = "1.2"` in `backend/app/auth/permissions.py`
  (line 71), with one comment line per version above it. `backend/app/plugins.py`
  (`is_compatible`) loads a plugin when the major matches and its minor is not newer.
- Existing hook shapes to copy: `register_login_provider_source` in
  `backend/app/auth/external.py` (line 89) and `register_password_login_path` /
  `reset_password_login_paths` in `backend/app/auth/login_paths.py` (lines 25 and 36).
- The test `db` fixture (`backend/tests/conftest.py`, line 151) joins an outer
  transaction with `join_transaction_mode="create_savepoint"`. `session.commit()` still
  fires the session's `after_commit` event, which is how the existing after-commit
  callbacks are tested.

## Dependencies

- C-008, so that the stream this hook exposes includes action runs.
- No external dependency and no new package.

## Implementation steps

1. Create `backend/app/audit/subscribers.py`:
   - `@dataclass(frozen=True) class AuditEntry` with `id`, `created_at`, `user_id`,
     `action`, `entity_type`, `entity_id`, `before_state`, `after_state`, `ip_address`.
     The two state fields are deep copies, so a subscriber cannot change what is stored.
   - `AuditSubscriber = Callable[[AuditEntry], None]`. Synchronous on purpose: it is
     called from a commit hook and must return quickly (hand the entry to a queue, a
     Celery task or a thread). The docstring says so, and says a subscriber must not use
     the session that just committed.
   - `register_audit_subscriber(fn)`, `reset_audit_subscribers()` for tests, and
     `deliver(entry)` which calls each subscriber inside its own `try`/`except Exception`
     and logs with `logger.exception`, naming the subscriber.
2. In `log_action`, after `db.add(entry)`: when at least one subscriber is registered,
   call `run_after_commit(db, lambda: _deliver_committed(entry))`. With no subscriber,
   register nothing, so Community does no extra work.
3. `_deliver_committed(row)`: return unless `sqlalchemy.inspect(row).persistent` is true
   and `row.id` is set. This drops rows whose savepoint was rolled back. Build the
   `AuditEntry` from the row's loaded attributes only (no lazy load, no query: the
   session is mid-commit) and call `deliver`.
4. Export `AuditEntry`, `register_audit_subscriber` and `reset_audit_subscribers` from
   `backend/app/audit/__init__.py`.
5. Set `PLUGIN_API = "1.3"` and add the comment line: "1.3  `app.audit`:
   `register_audit_subscriber`, `AuditEntry` (audit entries delivered after commit)."
   If S-002 lands in the same release, both share 1.3 or S-002 takes 1.4, whichever
   merges second; never two meanings for one number.
6. Update `CLAUDE.md`, section "Extensions (plugins) and directory sign-in": the contract
   sentence becomes 1.3 and gains one bullet for the audit hook, including the delivery
   promise in step 3 and the limit in Risks.
7. Ask for review before merge (class B): the reviewer checks the delivery promise and
   the wording of the contract comment.

## Acceptance criteria

- A subscriber registered in a test receives one `AuditEntry` for each `log_action` call
  whose session then commits, with the stored `id` and field values equal to the row's.
- A `log_action` call followed by `rollback()` delivers nothing.
- A `log_action` call inside `begin_nested()` that is rolled back, followed by an outer
  commit, delivers nothing for that row and still delivers rows written outside the
  savepoint.
- Two subscribers both receive each entry; when the first raises, the second still
  receives it, the request returns its normal status, and one error is logged.
- Changing `entry.after_state` in a subscriber does not change the stored row, and
  assigning to a field raises `FrozenInstanceError`.
- An API call through the `client` fixture that audits (for example creating a group)
  delivers its entry, proving the path through `get_db`.
- With no subscriber registered, `session.info` holds no audit callback after
  `log_action`.
- `PLUGIN_API == "1.3"` (or higher), and a plugin declaring `requires_plugin_api = "1.2"`
  still loads (`is_compatible("1.2")` is true).

## Test plan

- New file `backend/tests/test_audit_subscribers.py`. A fixture calls
  `reset_audit_subscribers()` before and after each test so nothing leaks into the rest
  of the suite.
- `cd backend && source .venv/bin/activate && pytest tests/test_audit_subscribers.py tests/test_audit_logger.py tests/test_audit.py -q`
- `pytest tests/test_external_auth.py tests/test_permissions.py -q` for the plugin
  loader and the version check.
- `ruff check . && ruff format --check .`
- The full suite runs once before the stage review.
- Worker delivery is checked once on local Docker on lin-manager-2 by the stage review:
  install a ten-line test plugin that logs each entry, run a sync against a host named
  "test", and see `sync_completed` in the worker log. No other host is touched.

## Docs and changelog

- `CLAUDE.md` as in step 6. There is no extension developer page under `docs/`, and this
  item does not add one.
- `CHANGELOG.md`, `[Unreleased]`, under "Added": "Extension hook for the audit log.
  Groundwork for Enterprise audit export; a Community install behaves exactly as before.
  An extension can subscribe to audit entries, which are handed over after they are
  committed. Plugin API 1.3."

## Risks and rollback

- Delivery is at most once. If the process dies between the commit and the callback, the
  subscriber never sees that entry. The contract must say this, and say how a consumer
  closes the gap: `AuditEntry.id` is the `audit_log` primary key, so a consumer keeps the
  highest id it has handled and reads later rows from the table when it starts.
- A slow subscriber delays the response of every audited request. The contract says
  subscribers must only enqueue. Community does not enforce a time limit.
- Reading unloaded attributes in the commit hook would raise. Step 3 reads only what
  `log_action` set, plus `id`, which the flush filled in.
- Rollback: revert the commit and set `PLUGIN_API` back. An extension built against 1.3
  is then skipped at load with a log line, which is the designed failure.

## Assumptions

- Assumption (not yet confirmed): at-most-once delivery after commit, with the id as a
  catch-up cursor, is enough for the first consumer. The alternative is a subscriber that
  runs inside the transaction and can write its own outbox row atomically; it was not
  chosen because a failing subscriber could then break the request, which the stage
  forbids. Question for Dennis: is this the right trade?
- Assumption (not yet confirmed): subscribers are synchronous callables.
- Entries are delivered whole, including `before_state` and `after_state`. Redaction for
  an outside system is the consumer's job.
- The minor bump follows the rule in `CLAUDE.md`: additions bump the minor.

## Definition of done

- [ ] `app/audit/subscribers.py`, the `log_action` change and the exports are in place.
- [ ] `tests/test_audit_subscribers.py` passes; the audit and plugin tests still pass.
- [ ] `PLUGIN_API` bumped with its comment; `CLAUDE.md` and `CHANGELOG.md` updated.
- [ ] Reviewed before merge.
- [ ] `planning/roadmap.yaml`: S-001 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
