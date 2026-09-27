# Bug Registry

Open bugs in LabDog. New entries are added as bugs are surfaced.

## Convention: open-only

**Only open bugs belong in this file.** When a bug is fixed:

1. Land the fix and write a descriptive commit message that references
   the bug ID (e.g. `fix(sync): BUG-37 — dispatch Celery tasks after
   commit`). That commit message is the canonical record (symptom,
   root cause, fix).
2. Delete the entry from this file in the same commit. Do **not** mark
   bugs `[x]` and leave them here — fixed entries belong in git history,
   not in the registry.

To retrace a historical bug ID referenced elsewhere
(`BUG-NN`, `SEC-NN`, `TYPE-NN`, `DEAD-NN`), search the commit log:

```
git log --grep BUG-37
git log -- backend/app/api/sync.py
```

## How to file an entry

Format each entry as:

    - [ ] **BUG-NN** `path/to/file.ext:LINE` — one-line summary

      Symptom, root cause, severity tier (Critical / High / Medium /
      Low). If reproduced from a specific scenario, note it. Group
      related bugs under the same severity heading.

ID counter as of last housekeeping pass: `BUG-86`, `SEC-35`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Security findings — High

Filed 2026-05-21 from a `security-auditor` whitebox source-level
review (see the `code-audit` branch history for the baseline). Each entry was
spot-checked against current HEAD before filing.

_No bugs are currently open._

---

## Open — 2026-09 whitebox audit

Filed 2026-09-03 from a full whitebox review (security, correctness,
performance) of the backend, frontend, packaging and CI. Every entry was
verified against source at `c784afb` before filing; the ones already
fixed are absent rather than ticked, per the open-only convention.

Findings from the same pass that have already landed are absent rather
than listed: the reflected XSS in the SPA dynamic-route rewrite, the AI
command-classifier bypasses, action-parameter template injection, pack
manifest path containment, and the quick-win batch (unauthenticated
resolver reads, registration privilege flags, `retention_days=0` wiping
the audit log, the scheduler tick poisoning itself, two temp-dir leaks,
the sync-tray request loop, the CSRF-less password change). Search the
log: `git log --grep "SEC"`.

**Note on the privilege model.** LabDog is deliberately flat — every
authenticated user has the same permissions and `is_superuser` gates only
user administration. That is a confirmed design decision, not a finding.
Its consequence shapes the severities below: the AI classifier, the
action-parameter validator and pack content policy are the *only* controls
protecting host root from an ordinary account. All three are now in
place: the classifier hardening, the extra-vars validator, and the pack
content policy.

### Security — Medium

### Security — Low

- [ ] **SEC-35** Grafana, Loki, Mimir and AI-provider base URLs are
      scheme-checked but may point at loopback, RFC1918 or 169.254.169.254
      (`app/grafana/schemas.py:16-25`, `app/ai/schemas.py:25-33`). Close to
      blind — responses must parse as the expected shape and httpx does not
      follow redirects by default — and a documented homelab decision. Git
      repo URLs *do* block these (`app/schemas/git_repos.py:18-25`). Filed
      so it is not re-discovered as new, not because it needs changing.

### Correctness — High

_No bugs are currently open._

### Correctness — Medium

_No bugs are currently open._

---

## Open — 2026-09-27 production investigation

Filed 2026-09-27 from the lin-manager instance (`openlabdog/labdog:test`,
alembic at `0038_drop_is_system_columns`), after adding an action pack
raised two 500s before the add succeeded (BUG-84, BUG-85), and the pack's
first action then failed before its first task (BUG-86). Each entry was
confirmed against the live database and the container log, not just read
from source.

### Correctness — Medium

- [ ] **BUG-84** `backend/alembic/versions/0018_repair_id_sequences.py:79-81` —
      the sequence repair ignores `is_called`, so it skips exactly the
      tables seeded with a single row at id=1

      Symptom: on an instance installed before `a371fe0` (2026-05-19),
      the first insert into `git_repositories` and the first insert into
      `action_packs` each fail once with
      `duplicate key value violates unique constraint "pk_git_repositories"`
      / `"pk_action_packs"`, `DETAIL: Key (id)=(1) already exists`.
      Reproduced on lin-manager 2026-09-27 09:07:18 (POST creating git
      repo `action_packs_private`) and 09:07:31 (POST creating pack
      `labdog-private`).

      Root cause: the repair reads `last_value` and compares it with
      `IF max_id > COALESCE(cur, 0)`. A serial sequence that has never
      been advanced reports `last_value = 1, is_called = false`, and
      `nextval` on it still returns 1 — so for a table whose highest
      seeded id *is* 1, the guard evaluates `1 > 1`, is false, and the
      table is skipped. `0001_initial_schema.py:986-987` seeds exactly
      one row at id=1 into both `git_repositories` and `action_packs`,
      so neither is ever repaired. `app_settings` is seeded with nine
      rows, so `9 > 1` fires and that table *is* fixed — which is why
      0018 appears to work, and why its own docstring's claim that
      "`app_settings` is where this bites" hid the other two tables.

      Self-healing but user-visible: `nextval` is non-transactional, so
      the failed insert consumes id 1 and marks the sequence called, and
      the retry gets id 2. That costs one opaque 500 per affected table,
      once per install, on a first-run path.

      The fix cannot be an edit to 0018 — every affected database has
      already run it. It needs a new migration whose guard accounts for
      `is_called`, e.g.
      `IF max_id > 0 AND (NOT is_called OR max_id > last_value) THEN`.

      State of lin-manager as of filing: both sequences are now at 2
      against `max(id) = 2`, and a sweep of every sequence-backed table
      found the only four still uncalled (`cron_jobs`, `linux_groups`,
      `package_repositories`, `resolver_configs`) to be empty, so
      `max_id = 0` and no latent collision remains there. Nothing needs
      repairing on that instance; the migration bug still affects any
      other pre-2026-05-19 install.

      Related coverage gap: `backend/tests/` has no migration tests.
      The test DB is migrated fresh, where 0001's compensating `setval`s
      are present, so 0018's repair path is never exercised against the
      state it exists to fix.

- [ ] **BUG-85** `backend/app/api/git_repos.py:34`,
      `backend/app/api/action_packs.py:168` — neither create endpoint
      catches `IntegrityError`, so constraint violations surface as raw
      500s

      Symptom: a failed insert escapes as `Exception in ASGI
      application` with a SQLAlchemy traceback and no usable message in
      the UI. Surfaced while investigating BUG-84, but it is not
      specific to that bug — the ordinary duplicate-name case on
      `uq_action_packs_name` / the `git_repositories` name constraint
      takes the same path, as does the
      `ck_action_packs_source_shape` check constraint.

      Root cause: both modules have zero `IntegrityError` handlers,
      unlike `app/api/packages.py`, `app/api/cron_jobs.py`,
      `app/api/ca_certs.py`, `app/api/groups.py` and `app/api/sync.py`,
      which all catch it and return a 4xx. The pre-insert `select` for
      an existing name in `create_git_repo` narrows the window but does
      not close it and does not cover the other constraints.

- [ ] **BUG-86** `backend/app/ansible_runtime/runner.py:108-115` —
      `run_ansible` stages only the playbook file, so nothing else in the
      action directory exists at run time

      Symptom: an action whose playbook imports a file next to it fails
      before its first task with `Unable to retrieve file contents. Could
      not find or access '/tmp/labdog-action-*/project/tasks/preflight.yml'
      on the Ansible Controller`. Reproduced on lin-manager 2026-09-27 at
      09:10 and 09:11 UTC (action runs 163 and 164, `docker-compose-update`
      from pack `labdog-private`): both failed within seconds, before any
      task reached the host. The same playbook runs fine from a checkout
      with plain `ansible-playbook`.

      Root cause: `run_ansible` reads `playbook_path` and writes its text,
      alone, to `<private_data_dir>/project/playbook.yml`. The only other
      pack content a run can reach is roles, through `ANSIBLE_ROLES_PATH`
      (`app/actions/packs.py:112`). So everything Ansible resolves against
      the playbook's directory misses: `import_tasks`/`include_tasks`,
      `vars_files`, `include_vars`, a `template`/`copy` `src:`. Host runs,
      group runs (`app/tasks/action_group.py:571`), verify playbooks
      (`action_host.py:934`, `action_group.py:1058`) and workflow steps
      (`app/workflows/steps/update.py:66`) all stage this way, and
      `load_pack` only checks that the playbook file exists, so nothing
      flags such a pack before its first run.

      The docs promise otherwise. `docs/ui/actions.md:379` says "An action
      is a directory: copy `actions/<key>/` into another pack to override
      that action wholesale", and `:382-385` credits action-private roles
      to "Ansible's playbook-adjacent role search". They work only because
      `packs.py:112` puts `actions/<key>/roles/` on `ANSIBLE_ROLES_PATH`;
      the staged playbook is adjacent to nothing.

      Fix direction: either stage the whole action directory, or keep
      single-file staging, say so beside the pack layout in the docs, and
      have `load_pack` refuse a playbook whose static imports resolve
      outside a role. Staging the directory has to be squared with
      `pack_policy` first: ansible-core reads some files next to a
      playbook on its own (`group_vars/`, `host_vars/`), and today none of
      those can reach a run.

      Worked around in the private pack by moving its task files into an
      action-private role (`actions/docker-compose-update/roles/cu/`). Its
      `scripts/validate_pack.py` now syntax-checks each playbook staged the
      way `run_ansible` stages it, which catches this before a push.
