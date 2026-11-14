# C-047: Audit rows for firewall rule changes (BUG-147)

| | |
|---|---|
| Edition | community |
| Source | proposed, accepted proposal PR-01 (`planning/PROPOSALS.md`), `BUGS.md` BUG-147 |
| Tier / score | P0 / 12.0 |
| Stage | 1 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal

Creating, editing, deleting or reordering a firewall rule, on a group or on a host, and
changing a group's chain policies, each writes an audit row naming the user. BUG-147 is
closed.

## Scope

In scope:
- Audit rows for the eight writing endpoints in `backend/app/api/rules.py`.
- Deleting the BUG-147 entry from `BUGS.md`.

Out of scope:
- Any change to what the endpoints accept or return.
- GitOps imports, which already write `gitops.import.firewall`
  (`backend/app/gitops/importers/firewall.py`, line 116).
- BUG-143 and other open bugs in the neighbouring modules.

## Context

- `backend/app/api/rules.py` (280 lines) imports only `require_group`, `require_host` and
  `scoped_row_or_404` from `backend/app/api/_scoped_rules.py`. It has no `log_action` call.
- Every other configuration module audits through `RuleAudit` and the three tails in
  `_scoped_rules.py`: `commit_created` (line 97), `commit_updated` (line 112) and
  `delete_and_commit` (line 130). `backend/app/api/cron_jobs.py` is the shortest example:
  `_audit_state` (line 35), `_AUDIT = RuleAudit("cron_job", _audit_state)` (line 39), and
  the endpoints take `user: User = Depends(require("config.edit"))` and end with the tail.
- The writing endpoints in `rules.py`, all declaring `config.edit` with the user bound
  to `_`:
  - `create_rule` (`POST /groups/{group_id}/rules`), `update_rule` (`PUT
    .../rules/{rule_id}`), `delete_rule` (`DELETE .../rules/{rule_id}`),
    `reorder_rules` (`PUT /groups/{group_id}/rules/reorder`).
  - `create_host_rule`, `update_host_rule`, `delete_host_rule` under
    `/hosts/{host_id}/firewall-rules`.
  - `update_group_policies` (`PUT /groups/{group_id}/policies`), which sets
    `input_policy` and `output_policy` on the group and also writes no row.
- `reorder_rules` loops over `body.rule_ids` and sets `priority` on each rule it finds in
  the group. Ids that are not in the group are skipped silently.
- `FirewallRule` is in `backend/app/models/firewall_rule.py`. Its columns include
  `group_id`, `host_id`, `action`, `protocol`, `direction`, `source_cidr`,
  `destination_cidr`, `source_host_id`, `destination_host_id`, the port fields,
  `priority` and a comment. Read the model for the full list before writing the state
  function.
- The commit helpers call `db.refresh(row)` after the commit, so the endpoints can return
  what they return today.

## Dependencies

None. S-001 benefits: an extension that listens to the audit stream sees firewall changes.

## Implementation steps

1. In `rules.py`, add `_audit_state(rule: FirewallRule) -> dict` returning every
   operator-set column as plain JSON values (enums through `str()`), plus `group_id` and
   `host_id` so a row says where the rule lives. Add
   `_AUDIT = RuleAudit("firewall_rule", _audit_state)`.
2. In the six create, update and delete endpoints, bind the dependency to `user` and
   replace the tail:
   - create: `db.add(rule)`, `await db.flush()`, `return await commit_created(db, rule,
     _AUDIT, user)`.
   - update: take `before = _audit_state(rule)` before `_apply_rule_update`, then
     `await db.flush()` and `return await commit_updated(db, rule, _AUDIT, user, before)`.
   - delete: `await delete_and_commit(db, rule, _AUDIT, user)`.
3. `reorder_rules`: record `{rule_id: priority}` before and after for the rules that were
   found, and write one row with `log_action(db, action="reorder",
   entity_type="firewall_rule", entity_id=None, user_id=user.id,
   before_state={"group_id": group_id, "priorities": ...}, after_state={...})` before the
   commit. Write no row when nothing changed. JSON keys are strings, so store the map as
   a list of `{"id": ..., "priority": ...}` objects to keep ids as numbers.
4. `update_group_policies`: write one `log_action(db, action="update",
   entity_type="firewall_policy", entity_id=group_id, user_id=user.id,
   before_state={"input": ..., "output": ...}, after_state={...})` before the commit.
5. Delete the BUG-147 entry from `BUGS.md` in the fixing commit, and name BUG-147 in the
   commit message, as `CONTRIBUTING.md` asks.

## Acceptance criteria

- Each of create, update and delete, on a group and on a host, writes exactly one
  `audit_log` row with `entity_type="firewall_rule"`, the matching `action`
  (`create`, `update`, `delete`), `entity_id` equal to the rule id and `user_id` equal to
  the caller.
- An update row has a `before_state` and an `after_state` that differ in the changed field.
- A delete row keeps the deleted rule's fields in `before_state`.
- A reorder that changes at least one priority writes exactly one row with
  `action="reorder"`; a reorder that changes nothing writes none.
- A policy change writes one `firewall_policy` row with the old and new policies.
- A request that fails (404, 400, GitOps lock 409) writes no row.
- The response bodies and status codes of all eight endpoints are unchanged.
- `grep -n "BUG-147" BUGS.md` prints nothing.

## Test plan

- New file `backend/tests/test_rules_audit.py`, built like the existing
  `backend/tests/test_rules.py` (same fixtures), selecting from `AuditLog` after each
  call. One test per acceptance line.
- `cd backend && source .venv/bin/activate && pytest tests/test_rules_audit.py tests/test_rules.py -q`
- `pytest tests/test_permissions.py tests/test_permission_scopes.py -q` to confirm the
  routes still declare `config.edit` with the same scope.
- `ruff check . && ruff format --check .`
- The full suite runs once before the stage review. Nothing here needs a running stack or
  a real host.

## Docs and changelog

- `docs/ui/operations.md`, section "Audit": add `firewall_rule` to the examples of typed
  objects, and mention the `reorder` action.
- `CHANGELOG.md`, `[Unreleased]`, under "Fixed": "Firewall rule and chain policy changes
  are now written to the audit log, like every other module's (BUG-147)."

## Risks and rollback

- `commit_created` needs a flushed row. Forgetting the flush gives `entity_id=None`; the
  create test catches it.
- The xor CHECK constraints on source and destination can fail at the new flush instead
  of at the commit. The error path is the same (an `IntegrityError`), but confirm with
  the existing validation tests in `test_rules.py` that the status codes do not change.
- Rollback: revert the commit and restore the BUGS.md entry. No migration.

## Assumptions

- Assumption (not yet confirmed): chain policy changes belong in this item. BUG-147 names
  rules only, but the policy endpoint is in the same file, has the same hole, and a
  default-accept input policy matters as much as any rule. Question for Dennis only if he
  wants it split out.
- Assumption (not yet confirmed): one row per reorder, not one per moved rule, as
  BUG-147's fix direction says ("write one row for a reorder").
- Entity type `firewall_rule` follows `cron_job` and `package_rule`.

## Definition of done

- [ ] Eight endpoints audit; responses unchanged.
- [ ] `tests/test_rules_audit.py` passes with `tests/test_rules.py`.
- [ ] BUG-147 deleted from `BUGS.md`; docs and changelog updated.
- [ ] `planning/roadmap.yaml`: C-047 set to done with its branch.
- [ ] One line appended to `planning/PROGRESS.md`.
