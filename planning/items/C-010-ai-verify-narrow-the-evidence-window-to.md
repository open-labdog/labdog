# C-010: AI verify: narrow the evidence window to before the action

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "AI verify evidence: narrow the window, add a second producer" (first half) |
| Tier / score | P2 / 9.0 |
| Stage | 8 |
| Effort | S |
| Autonomy | A |
| Depends on | none |

## Goal
AI verify no longer mistakes the log noise of the change itself for a fault. The journal evidence is cut at the moment the action started and the moment it finished, and the verdict is asked about what was logged afterwards, with what was already there before as a baseline.

## Scope
In scope:
- Passing the action's start and finish times to the verify collector.
- Splitting the journal reading into "before the change started" and "after the change finished".
- Adjusting the verify system prompt and the item labels to match.

Out of scope:
- A second evidence producer and stored evidence packs (C-011).
- Changing the hard checks (services, packages, load, disk).
- Changing how a verdict is resolved (`app/ai/verdict.py`).

## Context
- `backend/app/workflows/steps/verify.py`: `run_verification` (line 93) opens one SSH connection and collects the readings. `_read_journal_errors` (line 28) runs `journalctl --since '10 minutes ago' -p err --no-pager -q`, with a privilege probe first; `None` means the journal could not be read.
- `backend/app/workflows/steps/ai_verify.py`: `evidence_from_state` (line 67) turns the collected state into `list[EvidenceItem]`. The journal item is labelled "Errors logged in the last 10 minutes" with source `ssh: journalctl --since '10 minutes ago' -p err` (lines 92 to 103).
- `backend/app/ai/verify.py`: `SYSTEM_PROMPT` tells the model that "the recent log entries cover the window the change itself ran in, so some of them were caused by it doing its work" (line 59). That wording is the current mitigation.
- Known limitation as shipped, `CHANGELOG.md` (line 2436): "AI verify judges a window that includes the action's own work. Prompt wording mitigates this and the shipped default accounts for it, but narrowing the window to before the action started is the real fix and is not built."
- Callers: `_verify_with_builtin_checks` in `backend/app/tasks/action_host.py` (line 1019, the `run_verification` call at 1037) and `_verify_member_with_builtin_checks` in `backend/app/tasks/action_group.py` (line 1405). Neither passes a time. The playbook runs in `_phase_b_playbook` (per host) and `_run_group_playbook` (group).
- Tests: `backend/tests/ai/test_evidence.py`, `backend/tests/ai/test_verify_session.py`.

## Dependencies
- None.

## Implementation steps
1. Record two UTC timestamps in each executor: just before `run_ansible` is called for the main playbook and just after it returns. Per host: on `_PlaybookResult` (`started_at`, `finished_at`). Group: on `_GroupPlaybookResult`.
2. `run_verification`: add keyword arguments `action_started_at: datetime | None = None` and `action_finished_at: datetime | None = None`, and pass them from both callers.
3. `_read_journal_errors`: accept `since` and `until` as optional datetimes and build `--since @<epoch>` and `--until @<epoch>` from them (epoch seconds avoid any time-zone question on the host). Keep the `'10 minutes ago'` form when both are None. Return type unchanged.
4. In `run_verification`, when both times are known, take two readings over the same connection:
   - before: `since = action_started_at - 10 min`, `until = action_started_at`;
   - after: `since = action_finished_at`, no `until`.
   Store them as `journal_errors_before` and `journal_errors_after` with their reasons. Keep `journal_errors` as the "after" reading so the existing rule "AI verification also runs when the journal had errors" now keys on errors after the change. When the times are not known, behave exactly as today.
5. `evidence_from_state`: when the split readings exist, emit two items in place of the one: "Errors logged in the 10 minutes before the change started" and "Errors logged since the change finished", each with its real command as `source`, and each using `EvidenceItem.missing` when its reading is None. Otherwise emit today's single item.
6. `SYSTEM_PROMPT`: replace the paragraph at line 59 with wording for the split: errors from before the change were already there and are not caused by it; errors since the change finished are the ones to judge; nothing logged while the change was running is shown. Keep "Judge the host as it stands now."
7. Remove the known-limitation paragraph's claim from the docs (see Docs), and update the module docstrings that mention the ten-minute window.

## Acceptance criteria
- With both times given, the collector issues two `journalctl` commands whose `--since` and `--until` values match the times, and none that covers the interval between them.
- The evidence pack has the two journal items with the new labels, and an unreadable journal renders as UNAVAILABLE in both.
- An error logged only while the playbook ran does not appear in the pack and does not by itself trigger AI verification.
- An error logged after the playbook finished appears under "since the change finished" and triggers AI verification as before.
- With no times given (older callers, tests), the output is byte-for-byte what it is today.
- The system prompt no longer says the entries cover the window the change ran in.

## Test plan
- Extend `backend/tests/ai/test_evidence.py` (item labels and the fallback) and add `backend/tests/test_verify_journal_window.py` with a fake SSH connection that records the commands.
- `cd backend && source .venv/bin/activate && pytest tests/test_verify_journal_window.py tests/ai/test_evidence.py tests/ai/test_verify_session.py tests/test_action_host_phases.py tests/test_action_group_paths.py -q`, then the full suite once before the stage review.
- Running stack on lin-manager-2, host named "test" with a Proxmox mapping: run a destructive sample action whose playbook does `logger -p user.err "noise from the change"`, and confirm in the verify session's transcript that the line is absent. Then log an error after the playbook (a verify-time `logger` from a second terminal) and confirm it is present.

## Docs and changelog
- `docs/ui/assistant.md` (verify section) and `docs/ui/actions.md` where AI verify is described: state the two windows.
- `CHANGELOG.md` `[Unreleased]`, Changed: "**AI verify judges the journal from after the change.** Errors logged while the action ran are no longer shown to the verdict; errors from before it started are shown as a baseline."

## Risks and rollback
- Clock skew: the times come from the unbork server's clock and are applied on the host. A host whose clock is behind could have the tail of the change's noise fall into the "after" window, and one that is ahead could lose real errors. Hosts are expected to run NTP; add a 5 second margin after `action_finished_at` only if the real-host test shows a need, and say so in the code.
- A fault that the change causes and that logs only during the run, then stays silent, is no longer visible in the journal evidence. The hard checks (service state, packages) still catch a service that stayed down.
- Rollback: revert the commit; the fallback path is today's behaviour.

## Assumptions
- Assumption (not yet confirmed): the board's phrase "narrow the window to before the action started" means cutting the action's own run out of the judged window, keeping a before-baseline and an after-reading. Judging only pre-action logs could not tell whether the change worked. Question for Dennis: is this reading right, or was a different cut meant?
- Assumption (not yet confirmed): a ten-minute baseline before the start is enough.
- Assumption (not yet confirmed): the pack-supplied `verify_playbook` path is untouched; it does not use this collector.

## Definition of done
- [ ] Timestamps, collector, evidence items and prompt merged.
- [ ] New and extended tests pass; full backend suite green.
- [ ] Docs and `CHANGELOG.md` updated, including removal of the stale limitation wording.
- [ ] Stage 8 behaviour 1 verified on a host named "test".
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
