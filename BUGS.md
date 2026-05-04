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

ID counter as of last housekeeping pass: `BUG-43`, `SEC-06`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Medium

- [ ] **BUG-43** `frontend/app/(dashboard)/groups/[id]/rules/` — system firewall rules render with enabled Edit/Delete buttons

  The Rules page renders Edit and Delete buttons on every row, including
  rows where `is_system = TRUE` (auto-injected rules like the SSH
  lockout-prevention rule). Clicking Edit or Delete on a system row
  presumably hits a server-side guard and returns an error — but the
  operator gets to that point thinking the action is allowed, which is
  a confusing UX and a violation of the documented contract that system
  rules are read-only.
  Root cause: the SortableRow / row-action component does not check
  `rule.is_system` when deciding whether to enable Edit/Delete. The E2E
  test at `e2e/rules.spec.ts:100` asserts the disabled-on-system
  behaviour and fails (received `enabled`); the test reflects the design
  intent.
  Severity: Medium. No data corruption (server-side guard catches the
  attempt), but UX violates the documented invariant.
  Trigger: log in, navigate to any group with at least one host (so the
  SSH lockout rule appears) → `/groups/{id}/rules` → observe Edit/Delete
  buttons enabled on the system row.

### Low

- [ ] **BUG-42** `frontend/e2e/dashboard.spec.ts:21,83` — stale test references "Check All" button label after rename to "Collect State"

  The dashboard's primary action button was renamed from "Check All" to
  "Collect State" in commit `6d62d31`, but the two corresponding
  Playwright tests (`Check All button is visible` and
  `Check All button triggers drift check`) still look up the button by
  its old `name: "Check All"` accessible label. Both fail with
  `element(s) not found`.
  Root cause: test staleness — the tests were not updated when the UI
  string changed. The button still works correctly in the app.
  Severity: Low (test-only; no production impact).
  Trigger: `npx playwright test dashboard.spec.ts -g "Check All"`.
