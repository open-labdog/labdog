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

ID counter as of last housekeeping pass: `BUG-57`, `SEC-19`,
`TYPE-03`, `DEAD-01`. Pick the next number in the relevant series
when filing a new entry.

---

## Open

### Security findings — High

Filed 2026-05-21 from a `security-auditor` whitebox source-level
review (see the `code-audit` branch history for the baseline). Each entry was
spot-checked against current HEAD before filing.

_No bugs are currently open._

### Merge correctness — Medium

- [ ] **BUG-57** `backend/app/hosts_mgmt/merge.py:118` — per-entry
      `priority` is decorative in five modules; a same-key entry
      silently overwrites instead.

      **Symptom.** Adding a hosts-file override for an IP that already
      has an entry replaces the existing one without warning. Raising
      the new entry's **Priority** does not change the outcome, and
      neither does lowering it.

      **Root cause — two defects that compound.**

      1. *The merge keys on identity alone and overwrites.* Host-level
         entries are applied as `merged[ip] = ...` with no membership
         check ([`merge.py:118-129`](backend/app/hosts_mgmt/merge.py)),
         so the last row read wins. Which row that is depends on an
         unordered `SELECT`, so the winner is not even stable. Group
         entries use the opposite rule — `if ip not in merged`,
         first-wins in `HostGroup.priority DESC` order.
      2. *`priority` is never consulted.* `HostsEntry.priority` exists
         as a column, is validated `ge=0, le=10000` in the schema, and
         is rendered as a form field — but no merge engine reads it.
         The only priority that affects any outcome is
         `HostGroup.priority`, which orders the groups.

      **Same issue, other modules.** The dead per-entry `priority`
      column is not specific to hosts entries. In each of these the
      merge reads `HostGroup.priority` only:

      | Module | Merge key | Reads entry `priority`? | Unique constraint? |
      |---|---|---|---|
      | `hosts_mgmt` | `ip_address` | No | **No** |
      | `services` | `service_name` | No | **No** |
      | `cron` | `(name, user)` | No — passthrough to response only | **No** |
      | `user_mgmt` | `username` / `groupname` | No | **No** |
      | `packages` | `package_name` | No — passthrough only | Yes |

      `packages` is the one that already behaves: it carries
      `uq_package_rules_group_pkg` / `uq_package_rules_host_pkg`, so a
      duplicate is refused at write time rather than silently resolved
      at merge time. The other four have no unique constraint on
      `(scope, key)`, which is why the collision surfaces as a silent
      overwrite.

      `rules` (firewall) is **not** affected — it genuinely orders by
      group priority then `rule.priority`
      ([`rules/merge.py:83`](backend/app/rules/merge.py)).

      **Fix direction** (not yet done). Either honour `priority` in the
      merge or remove it from the UI and schema — but not leave a
      control that reads as if it disambiguates and does nothing. The
      narrower fix the reporter asked for is to make the field
      unavailable and say so. Adding the missing unique constraints,
      following the `packages` precedent, would turn the silent
      overwrite into an honest error at the point of entry. Note that
      keying hosts entries on `ip_address` alone also forbids two
      hostnames sharing an IP, which is legitimate in `/etc/hosts`.

      Reported 2026-08-21 from the **Add Hosts Entry Override** dialog.
