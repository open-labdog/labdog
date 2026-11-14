# S-015: Extension hook: register Celery tasks and periodic jobs

| | |
|---|---|
| Edition | shared |
| Source | roadmap, found while writing the stage 5 specs (no board item) |
| Tier / score | P0 / 13.0 |
| Stage | 1 |
| Effort | S |
| Autonomy | B |
| Depends on | none |

## Goal

An installed extension can add its own Celery task module and have a task run on a fixed
interval, on a queue a worker consumes. A Community install with no extension starts the
same processes and registers the same schedules as before. Enterprise audit export,
scheduled directory sync and verified backups will be the first users.

## Scope

In scope:
- `register_task_module()` and `register_periodic_task()` in a new `app.tasks.extensions`
  module, with a reset function for tests.
- Loading extensions in the worker parent process and in the beat process, once per
  process.
- Routing for extension tasks, limited to queues a worker consumes and never
  `orchestrator`.
- Removing a periodic entry whose extension is no longer installed.
- A `PLUGIN_API` minor bump, `CLAUDE.md`, the plugin loader's docstring.

Out of scope:
- Any extension task, outbox or schedule. Those belong to the extension.
- Cron expressions, per-entry arguments and operator-editable intervals. An extension
  that wants a configurable interval registers a short tick and decides in the task.
- A new queue, a new worker role, or any change to `ROLE_PROCESSES`.
- Changing how core modules register their schedules.

## Context

- `backend/app/tasks/__init__.py` builds `celery_app`. `celery_app.conf.include` (line
  279) is a fixed list of `app.tasks.*` modules, and `task_routes` (line 42) maps name
  patterns to queues, with `task_default_queue="default"` (line 41).
- Extensions load in two places only: `create_app()` (`backend/app/main.py`, line 669)
  and `_load_extensions` (`tasks/__init__.py`, line 126), a `worker_process_init` handler
  that runs in each pool child. They do not load in the worker parent, which is the
  process that decides whether a received task name is known, and they do not load in
  beat.
- `backend/app/tasks/beat_registry.py`: `register_all(app)` (line 87) walks
  `app.conf.include`, imports each module and calls its `_register_*schedules` functions.
  It runs from the `beat_init` handler (`tasks/__init__.py`, line 219). `ensure_entry()`
  (line 46) writes a RedBeat entry only when it differs, so a restart keeps `due_at`.
  Nothing removes an entry that is no longer registered.
- Celery 5.7 (checked in `backend/.venv`): both the worker parent
  (`celery/worker/worker.py`, `WorkController.__init__`) and beat (`celery/apps/beat.py`,
  `init_loader`) call `loader.init_worker()`, which calls `import_default_modules()`.
  That sends the `import_modules` signal and then imports `conf.include`. A handler on
  that signal therefore runs in both processes before the task modules are imported, and
  before `beat_init`. An exception returned by such a handler is re-raised by Celery, so
  the handler must catch everything.
- `backend/app/celery_manager.py`: `CeleryManager.QUEUES` (line 162) is every consumed
  queue. `orchestrator` is reserved for `run_action` (`CLAUDE.md`, "Celery workers and
  queues"). `backend/tests/test_task_routing.py` asserts every registered task routes to
  a consumed queue; `tests/test_orchestrator_queue.py` asserts the separation.
- `backend/app/plugins.py`: `load_plugins(app, eps=None)` (line 86) has no guard against
  running twice in one process.
- Task bodies reach the database with `asyncio.run(with_task_engine(...))` and
  `task_session()` (`backend/app/db.py`, lines 85 and 128).
- `PLUGIN_API = "1.2"` (`backend/app/auth/permissions.py`, line 71); other stage 1 items
  bump it as well.

## Dependencies

- No item dependency and no new package.
- S-001 and S-002 also add hooks in stage 1. Each takes the next free minor at the time
  it merges.

## Implementation steps

1. `backend/app/plugins.py`: add `load_plugins_once(app=None)`. It keeps a module-level
   flag, calls `load_plugins(app or FastAPI())` the first time and returns the stored
   list afterwards. `create_app()` keeps calling `load_plugins(app)` directly.
2. Create `backend/app/tasks/extensions.py`:
   - `EXTENSION_QUEUES`: `CeleryManager.QUEUES` without `orchestrator`.
   - `register_task_module(module: str, *, queue: str = "default")`. It refuses a module
     under `app.` and a queue outside `EXTENSION_QUEUES` with `ValueError`, appends the
     module to `celery_app.conf.include` if absent, sets
     `celery_app.conf.task_routes[f"{module}.*"] = {"queue": queue}` and calls
     `celery_app.amqp.flush_routes()`. Calling it twice with the same arguments is a
     no-op. The docstring says task names must start with the module path to get the
     route; any other name falls to `default`, which is consumed.
   - `@dataclass(frozen=True) class PeriodicTask` (`name`, `task`, `every_seconds`) and
     `register_periodic_task(name, task, every_seconds)`. The stored RedBeat name is
     `ext-<name>`. It refuses an interval under 10 seconds and a second registration of
     the same name with a different task or interval.
   - `periodic_tasks()` and `reset_extension_tasks()` (for tests; it also removes the
     include and route entries it added).
3. `backend/app/tasks/__init__.py`: add a handler on `celery.signals.import_modules` that
   calls `load_plugins_once()` inside `try`/`except Exception` with `logger.exception`.
   Change `_load_extensions` (line 126) to call `load_plugins_once()` too, so a prefork
   child that inherited the parent's state does not register everything a second time,
   while a pool with no parent import still loads.
4. `backend/app/tasks/beat_registry.py`, in `register_all()` after the module loop: call
   `ensure_entry("ext-" + p.name, p.task, p.every_seconds, app)` for each registered
   periodic task, with the result key `extension:<name>` and the same log-and-collect
   failure handling. Then delete every RedBeat entry whose key starts with
   `redbeat:ext-` and is not registered now (`RedBeatSchedulerEntry.from_key(...).delete()`),
   logging each removal at info level.
5. Bump `PLUGIN_API` to the next free minor and add the comment line:
   "`app.tasks.extensions`: `register_task_module`, `register_periodic_task`; extensions
   also load in the worker parent and in beat; `app.db.task_session` and
   `with_task_engine` may be used by extension tasks."
6. `CLAUDE.md`, section "Extensions (plugins) and directory sign-in": one bullet for the
   hook (which processes load extensions, the queue rule, the `ext-` prefix and pruning).
   In "Celery workers and queues", one sentence that extension tasks may use every queue
   except `orchestrator`. Update the module docstring of `backend/app/plugins.py`, which
   still says plugins load once per process in `create_app()`.
7. Tests, in a new `backend/tests/test_extension_tasks.py`, with a stand-in extension in
   the test module (a `register` function with `requires_plugin_api`, loaded through
   `load_plugins(FastAPI(), [EntryPoint(...)])` as `tests/test_external_auth.py` does at
   line 315) and a stand-in task module under `tests/fixtures/`. A fixture calls
   `reset_extension_tasks()` before and after each test.
8. Ask for review before merge (class B). The reviewer checks the queue rule and the
   load-once guard.

## Acceptance criteria

- After the stand-in registers its module with `queue="long_running"` and
  `celery_app.loader.import_default_modules()` runs, its task is in `celery_app.tasks`
  and resolves to `long_running` with the router (`celery_app.amqp.router.route`).
- `register_task_module("x.tasks", queue="orchestrator")` and an unknown queue both raise
  `ValueError`; so does a module under `app.`.
- `tests/test_task_routing.py` and `tests/test_orchestrator_queue.py` pass with the
  stand-in registered.
- With a periodic task registered, `register_all()` returns `extension:<name>: "ok"` and
  writes one entry named `ext-<name>`; a second call writes nothing.
- A stored `redbeat:ext-old` entry that nothing registers is deleted by `register_all()`;
  a core entry is never deleted.
- An interval of 5 seconds raises `ValueError`.
- `load_plugins_once()` called twice runs the stand-in's `register` once.
- A stand-in whose `register` raises does not stop `import_default_modules()`.
- With no extension installed, `celery_app.conf.include` and `task_routes` equal what
  they were before this change, and `register_all()` returns the same keys.
- `PLUGIN_API` is one minor higher (or more), and `is_compatible("1.2")` is still true.

## Test plan

- `cd backend && source .venv/bin/activate && pytest tests/test_extension_tasks.py tests/test_beat_registry.py tests/test_task_routing.py tests/test_orchestrator_queue.py tests/test_worker_roles.py tests/test_external_auth.py -q`
- `ruff check . && ruff format --check .`
- The full suite runs once before the stage review.
- Real processes are checked once by the stage review on local Docker on lin-manager-2:
  install a small test plugin that registers a task and a 30-second entry, start the
  `all` role and the split roles (`dev/docker-compose.split.yml`), and see the task run
  in the worker log; uninstall it, restart beat, and see the entry removed. No managed
  host is involved.

## Docs and changelog

- `CLAUDE.md` and the `app/plugins.py` docstring as in step 6. There is no extension
  developer page under `docs/` at the time of writing; if one exists by then, add the
  hook there. `docs/scaling-out.md`: one sentence that extension tasks run on the
  existing worker roles.
- `CHANGELOG.md`, `[Unreleased]`, under "Added": "Extension hook for background work.
  An extension can register its own task module and periodic jobs. A Community install
  behaves exactly as before. Plugin API minor bump."

## Risks and rollback

- A broken extension now loads in the worker parent and in beat. `load_plugins` already
  logs and skips a plugin that fails to import or register, and step 3 catches the rest,
  so neither process can fail to start because of one.
- An extension that floods a queue delays core work on that queue. The contract limits
  the queues and forbids `orchestrator`; it does not limit volume.
- An entry left behind by an uninstalled extension would fire a task no worker knows.
  Step 4 removes it at the next beat start. Until then the worker logs and discards it.
- Mutating `task_routes` after the router was built would be ignored; step 2 calls
  `flush_routes()` for that reason, and the first acceptance criterion proves it.
- Rollback: revert the commit and set `PLUGIN_API` back. An extension built against the
  new minor is then skipped at load with a log line. Its `ext-` entries stay in Redis
  until removed by hand (`redbeat:ext-*`), which the revert PR must mention.

## Assumptions

- Assumption (not yet confirmed): fixed intervals are enough for the first users. All of
  them either run a short tick or a daily or weekly job.
- Assumption (not yet confirmed): a 10-second floor on intervals. Question for Dennis:
  is there a reason to allow less?
- Assumption (not yet confirmed): extension tasks share the existing queues and no
  `extensions` queue is added, because a new queue would need a consumer in every role
  layout.
- Naming `app.db.task_session` and `with_task_engine` in the contract makes them harder
  to change later. They are already used by every core task.
- The minor bump follows the rule in `CLAUDE.md`: additions bump the minor.

## Definition of done

- [ ] `app/tasks/extensions.py`, `load_plugins_once`, the signal handler and the
      `register_all` change are in place.
- [ ] `tests/test_extension_tasks.py` passes; the routing, beat and plugin tests still
      pass.
- [ ] `PLUGIN_API` bumped with its comment; `CLAUDE.md`, the `plugins.py` docstring and
      `CHANGELOG.md` updated.
- [ ] Reviewed before merge.
- [ ] `planning/roadmap.yaml`: S-015 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
