# C-009: propose_action: let the assistant request a vetted action

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "propose_action: let the assistant request a vetted action pack"; decision D-13 |
| Tier / score | P2 / 4.0 |
| Stage | 8 |
| Effort | L |
| Autonomy | B |
| Depends on | none |

## Goal
The assistant can ask for a named action from a pack to be run, instead of a shell command, so the change gets the action's own snapshot, verify and rollback. The tool starts the run and ends the turn; the session picks up again when the run has finished and checks the result.

## Scope
In scope:
- Tools `propose_action` and `list_allowed_actions`, offered only to sessions with a non-empty `allowed_action_keys`.
- A `waiting_action` session status, and resuming the session when the run ends.
- Approval through the existing approval cards, with an action-shaped preview and the dual permission check.
- Setting `allowed_action_keys` for chat sessions and for the scheduled AI task.

Out of scope (D-13):
- Any change to `backend/app/tasks/host_lock.py`. The tool never waits inside a host claim.
- A dry run attached to the approval card (a later addition).
- An AI snapshot for this tool. The action envelope takes its own.
- Per-alert allowlists of actions, and making full-auto alert remediation prefer actions. Recorded on the board as the follow-up.
- A Proxmox status tool (named on the board as missing; separate work).

## Context
- Design write-up for D-13: https://claude.ai/artifact/AVhU8tSKwe6LeeQmfzE4AG. Decision row in `../DECISIONS.md`: the tool "creates the ActionRun and ends the turn with a new `waiting_action` session status; the session resumes when the run finishes; `host_lock.py` is unchanged. Approval uses the existing `AIApprovalRequest` cards with frozen arguments. `allowed_action_keys` limits the tool at every level, and under full_auto a listed key runs without asking. The approver needs `ai.approve` and `actions.run` on the target. No AI snapshot."
- `backend/app/ai/models.py`: `AISession.allowed_action_keys` (line 175) exists and nothing reads it. `SESSION_STATUSES` (line 65) and `TERMINAL_SESSION_STATUSES`. `AIApprovalRequest` (line 326) stores `tool_name`, `arguments`, `target_host_id`, `command_preview`.
- Tools: registered with the `tool(...)` decorator (`backend/app/ai/tools/base.py`, line 98) into `TOOL_REGISTRY`; `tools_for_session` (`backend/app/ai/tools/__init__.py`, line 58) filters by `allowed_tools` and mode. Existing run tools: `list_action_runs`, `get_action_run_output` in `backend/app/ai/tools/runs.py`.
- Gate and parking: `gate.decide` (`backend/app/ai/gate.py`, line 79); `approvals.park` (`backend/app/ai/approvals.py`, line 73) sets `waiting_approval`; `command_preview` (line 60); `execute_approved` (line 164) snapshots when `target_host_id` is set, then runs the handler with `preapproved=True`. Both runners end the turn on a park: `loop.py` line 676, `agent_sdk/runner.py` line 884. `_rollback_point` in `loop.py` (line 525) snapshots before any mutating call at full auto.
- Resume: `decide_approval` (`backend/app/api/ai.py`, line 1040) checks `ai.approve` with `require_on` and dispatches `app.tasks.ai_task.resume_session`; `_resume_session_async` (`backend/app/tasks/ai_task.py`, line 240) claims the session with a conditional update (BUG-138), runs the approved call, appends `approvals.resume_prompt`, and runs the runner. `expire_stale_approvals` in `backend/app/tasks/ai_approvals.py` is a beat task that resumes sessions.
- A scheduled session's own run ends when it parks: `_run_action_session` in `ai_task.py` (line 471) returns success with `_parked_report`, which releases the host.
- Creating a run: `create_run` in `backend/app/api/actions.py` and its helpers `_runnable_action`, `_stored_parameters`, `_require_target`, `_refuse_duplicate_run`, `_insert_run`, `_dispatch_run`. They raise `HTTPException`.
- A run reaches a terminal status in several places: `finalise_run_if_complete`, `_mark_run_failed`, `_mark_run_cancelled`, `_finalise_soft_limited` in `action_orchestrator.py`; `_aggregate_and_finalise`, `_mark_run_failed`, `_mark_run_cancelled` in `action_group.py`; and `action_sweeper.py`.
- UI: `frontend/components/ai/approval-card.tsx`, `chat-transcript.tsx`, `session-aside.tsx`. Scheduled AI task parameters: `backend/app/actions/builtins.py` (`allowed_tools`, line 168), parsed in `ai_task.py` (`_parse_allowed_tools`, line 369).

## Dependencies
- [D-13](../DECISIONS.md).
- S-002 (stage 1) may have added one internal point that hears every run end. If it did, use it in step 8.
- Latest Community migration today is `0060_schedule_owner.py`; use the next free number.

## Implementation steps
1. Migration `backend/alembic/versions/00NN_propose_action.py`: `action_runs.ai_session_id` (FK `ai_sessions`, SET NULL, indexed) and `ai_approval_requests.target_group_id` (FK `host_groups`, SET NULL). Add `"waiting_action"` to `SESSION_STATUSES` (not terminal). Add the columns to the models and to `ActionRunOut` and the approval response schema.
2. Shared run creation: move the run-creation helpers from `api/actions.py` into `backend/app/actions/run_service.py`, raising a domain error (`RunRefused(status_code, detail)`) that `create_run` maps to `HTTPException`. Behaviour of the route must not change. `_insert_run` gains `ai_session_id` and an explicit `triggered_by_user_id`.
3. New `backend/app/ai/tools/actions.py`:
   - `list_allowed_actions` (read-only): for each key in the session's allowlist that is registered, resolved and has a playbook, return key, name, description, destructive flag, host or group support and parameters.
   - `propose_action` (classification `mutating`): arguments `action_key`, exactly one of `host_id` or `group_id`, `parameters`, `purpose`. A `validate(ctx, args)` function used at proposal time and again at execution: key in the session's `allowed_action_keys`; the run-service checks (unknown, unresolved, scope support, parameters against the manifest, no identical run in flight); host in `ctx.target_host_ids`, or for a group every member in them. A failure is returned to the model as an ordinary failed result, so nobody is shown a card for a run that would be refused.
   - Execution creates the run through the run service with `ai_session_id`, dispatches it, and returns a `ToolResult` with a new field `handoff_run_id`.
   Add `allowed_action_keys` to `ToolContext` and to `tools_for_session`: both tools are left out when the list is empty or None.
4. Gate and runners, for a call to `propose_action`:
   - `read_only`: refused, as any mutating call.
   - `approval`: validate, then `approvals.park` as today. Extend `command_preview` for this tool ("Run <action name> on <target> with <parameters>") and set `target_group_id` in `park`.
   - `full_auto`: validate and execute at once.
   - Never take an AI snapshot and never take a per-command host claim (C-021) for this tool: add `snapshots: bool = True` to `ToolHandler`, set it False here, and honour it in `_rollback_point`, in the SDK runner's equivalent, and in `execute_approved`.
   - When a result carries `handoff_run_id`, both runners end the turn as they do for a park: set `session.status = "waiting_action"`, emit a `status` event, and return an outcome with that status.
5. `ai_task.py`, `_run_action_session`: treat `waiting_action` like `waiting_approval`: end the scheduled session's own run successfully with a report "Handed off to action run N. This check has paused and resumes when that run finishes." That releases the host, and the new run then takes it through the normal queue.
6. Approval path: in `decide_approval`, when `approval.tool_name == "propose_action"`, also call `require_on(user, "actions.run", hosts=…, groups=…)` for the frozen target, and include the group in the `ai.approve` check. In `_resume_session_async`, after `execute_approved` returns a result with `handoff_run_id`, set `waiting_action`, commit and return without starting the runner. A validation failure at this point (the action changed since the proposal) goes back to the model through `resume_prompt` as a failed result.
7. Attribution and audit: the run's `triggered_by_user_id` is the approver, or under full auto the session's `created_by_user_id` (for a scheduled session, the schedule's owner). Write an audit row `ai_action_proposed_run` with the session id, run id, action key, target and parameters.
8. Resume after the run: new Celery task `app.tasks.ai_task.resume_after_action(session_id)`. It claims the session with a conditional update `waiting_action -> running`, loads the run by `ai_session_id`, appends a user message narrating the outcome (run status, each host's status and error, the last part of the output formatted as `get_action_run_output` does, and stored results if S-013 has landed), then starts the runner as `_resume_session_async` does. Triggers: a beat task every 30 seconds that finds `waiting_action` sessions whose run is terminal (this is the guarantee, since a run can end in many places), plus a direct dispatch from S-002's run-end point if one exists. Route the tasks so `tests/test_task_routing.py` passes.
9. Session API (`backend/app/api/ai.py`): accept `allowed_action_keys` on session creation, refusing unknown keys and requiring the creator to hold `actions.run` on the session's target hosts when the list is non-empty. A message posted to a `waiting_action` session is refused like one posted to `waiting_approval` (line 849). Cancelling a `waiting_action` session ends the session only; the run is an ordinary run and is cancelled from its own page.
10. Scheduled AI task: add an `allowed_action_keys` parameter beside `allowed_tools` in `builtins.py` and parse it in `ai_task.py`.
11. Check `_changed_its_host` in `backend/app/ai/remediation.py` (line 153) and the cooldown and cap counters in `alert_autonomy.py`: a handed-off run must count as a change to the host.
12. Frontend: `approval-card.tsx` renders an action-shaped card for `propose_action` (action name, target, parameter table, destructive flag, link to the action); the approve button is disabled with a title for a user without `actions.run`. Status label and colour for `waiting_action` with a link to the run, in the transcript and the session list. The run page shows "Requested by AI session N". An "Actions the assistant may request" picker in the new-session form and the scheduled AI task form. Types in `frontend/lib/types.ts`.

## Acceptance criteria
- A session with an empty allowlist is not offered either tool, and a call to `propose_action` is refused.
- Full auto, key in the allowlist: one `ActionRun` is created with `ai_session_id` set, the turn ends, the session is `waiting_action`, and no AI snapshot exists for the call.
- Approval level: the call parks on an approval card showing the action, target and parameters; approving creates the run with exactly the frozen arguments, attributed to the approver; rejecting or expiry creates none.
- An approver with `ai.approve` but without `actions.run` on the target gets 403 and nothing runs.
- A key outside the allowlist, a host outside the session's targets, an unresolved key, or parameters the manifest rejects all return a failed tool result and create no card and no run.
- When the run reaches any terminal status (succeeded, partial, failed, cancelled, swept), the session resumes within about 30 seconds, exactly once, with the outcome in its transcript.
- A scheduled AI check that proposes an action for its own host does not deadlock: its run ends, the proposed run starts, and the session resumes afterwards.
- `host_lock.py` has no diff. `POST /api/actions/runs` behaves as before (its existing tests pass unchanged).

## Test plan
- New `backend/tests/ai/test_propose_action.py`: tool validation, the three autonomy levels, approval and the dual permission, hand-off status in both runners (with `fake_provider.py` and `fake_sdk_client.py`), resume after each terminal status, double-resume, and the scheduled-session case. Extend `tests/ai/test_approvals.py`, `tests/ai/test_gate.py`, `tests/ai/test_host_tools.py`, `tests/test_actions_runs_validation.py` and `tests/test_task_routing.py`.
- `cd backend && source .venv/bin/activate && pytest tests/ai/test_propose_action.py tests/ai/test_approvals.py tests/ai/test_gate.py tests/ai/test_loop_paths.py tests/ai/test_agent_sdk_runner.py tests/test_actions_runs_validation.py tests/test_task_routing.py tests/test_permissions.py tests/test_permission_scopes.py -q`, then the full suite. The `tests/ai` database tests need Docker.
- Frontend: `cd frontend && npx tsc --noEmit && npm run lint`, and extend `e2e/assistant-follow-up.spec.ts` or add `e2e/propose-action.spec.ts` with a mocked action-shaped approval.
- Running stack on lin-manager-2 with an AI provider and a host named "test": an approval-level chat allowed one harmless sample action; approve it, watch the run, watch the session resume. Then the same from a scheduled AI task at full auto. Nothing on the live instance until both pass.

## Docs and changelog
- `docs/ui/assistant.md`: "Requesting an action" (allowlist, approval card, hand-off and resume, who the run is attributed to). `docs/ui/actions.md`: runs requested by the assistant. `docs/ui/scheduled-actions.md`: the new AI task parameter.
- `CHANGELOG.md` `[Unreleased]`, Added: "**The assistant can request an action.** With an allowlist of action keys, a session can ask for a pack action instead of a shell command. At approval level it waits on an ordinary approval card and the approver needs `ai.approve` and `actions.run`; at full auto a listed action runs at once. The session pauses while the action runs and resumes to check the result."

## Risks and rollback
- A session stuck in `waiting_action` forever: only possible if the run never ends, and the action sweeper already fails such runs. The beat scan, not a single call site, is what makes resume reliable.
- Approval as a back door to running actions: closed by the dual permission check, which must be tested with the scoped stand-in resolver.
- The refactor of run creation touches a core route. Keep it mechanical and prove it with the unchanged existing tests before adding the tool.
- A resumed session starts new caps; a model that proposes again and again is bounded by the one-shot approval at approval level and by the allowlist, cooldown and daily cap at full auto (step 11).
- Rollback: empty allowlists disable the feature without a deploy. To remove it, revert the commit and downgrade the migration after confirming no session is in `waiting_action`.

## Assumptions
- [D-13](../DECISIONS.md) for the hand-off, approval shape, allowlist, permissions and no AI snapshot.
- Assumption (not yet confirmed): for a group target, every member must be in the session's `target_host_ids`.
- Assumption (not yet confirmed): alert-investigation sessions get no allowlist in this item, so the tool is not available to them yet. Question for Dennis: add a global setting now, or wait for the per-alert allowlist?
- Assumption (not yet confirmed): the tool never requests a dry run, and uses the action's default safety flags.
- Assumption (not yet confirmed): a 30-second resume latency is acceptable when no direct trigger exists.

## Definition of done
- [ ] Migration, run service refactor, tools, gate and runner changes, approval path, resume task and UI merged behind review.
- [ ] `tests/ai/test_propose_action.py` and the extended suites pass; full backend suite green; `tsc`, lint and Playwright clean.
- [ ] Docs and `CHANGELOG.md` updated.
- [ ] Stage 8 behaviour 5 verified on lin-manager-2.
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
