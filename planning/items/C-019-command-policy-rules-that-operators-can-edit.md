# C-019: Command policy rules that operators can edit

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Command policy: operator-editable rules, and diagnostics counted as reads" (first half); decision D-14 |
| Tier / score | P2 / 3.7 |
| Stage | 8 |
| Effort | L |
| Autonomy | B |
| Depends on | none |

## Goal
An admin can teach the assistant a new read-only command (a vendor CLI, a site tool) from the UI, without waiting for a release. The rule is checked by the same loader as the shipped rules, can never make a write look like a read, survives upgrades, and every change is in the audit log.

## Scope
In scope:
- A database overlay on the shipped policy file: added rules, and shipped rules switched off.
- A new permission `ai.policy.manage`, admins only, global scope, with a `PLUGIN_API` minor bump (D-14).
- API routes and a "Command policy" page showing the effective rules and who changed what.
- Building each session's policy once, at start and at resume, and using it at both gates.
- "Allow this" on a refused command, which pre-fills a rule. It is the last step and can ship as its own PR.

Out of scope:
- Scoping a rule to a host group, and policy as code in GitOps (board: "Later").
- Operator-defined gates, wrappers or environment variables. An operator rule is a path, a mode and an option table.
- Any change to the denylist in `safety.py`.
- Standing approval for changes. A rule says a command only reads; `approvals.py` stays one-shot.

## Context
- `backend/app/ai/command_policy.yaml`: the shipped policy (`version`, `read_only`, `options`, `gates`, `wrappers`, `environment`). It ships in the image, so a new read needs a release.
- `backend/app/ai/policy.py`: `load_data` (line 690) parses the file; `CommandPolicy.from_data` (line 496) validates and builds; it refuses a read-only rule whose first word is in `NEVER_READ_ONLY` (line 41) or is a wrapper, refuses a path listed twice, and requires gates for `NEEDS_GATES`. `DEFAULT = CommandPolicy.load()` (line 920).
- `backend/app/ai/safety.py`: `classify_command(command, policy=None)` (line 430) falls back to `command_policy.DEFAULT`; the denylist (`DENYLIST_PATTERNS`) is checked first in every segment.
- No session uses anything but `DEFAULT` today. `backend/app/ai/tools/ssh.py` calls `classify_command(command)` at line 120 and again in the tool's `preclassify` lambda (line 104), which `gate.decide` uses through `handler.verdict_for(arguments)` (`backend/app/ai/gate.py`, line 107). `ToolContext` (`backend/app/ai/tools/base.py`, line 21) carries no policy. `ToolContext` is built in three places: `loop.py` line 625, `agent_sdk/runner.py` line 835, `approvals.py` line 212.
- Permissions: `backend/app/auth/permissions.py`: `Permission` enum (line 81), `_OPERATOR` (line 101), `_ADMIN = frozenset(Permission)` (line 112), `PLUGIN_API = "1.2"` (line 71) with its history comment.
- Settings UI: `frontend/app/(dashboard)/settings/client-page.tsx`, section `ai` (line 52). Refused calls are rendered in `frontend/components/ai/chat-transcript.tsx`.
- Board guardrails, quoted: precedence is "the denylist outranks the shipped gates and option tables, which outrank operator rules, which outrank default-deny. An operator rule can only turn 'unrecognised' into 'read-only'". "A session reads the policy once when it starts or resumes."

## Dependencies
- D-14 (`../DECISIONS.md`): `ai.policy.manage`, admins only, checked at global scope, with a `PLUGIN_API` minor bump. It replaces the 2026-10-02 "any signed-in user" decision in the board text.
- Latest Community migration today is `0060_schedule_owner.py`; use the next free number.
- C-020 adds shipped rules to the same file; no conflict, but rebase on it.

## Implementation steps
1. `permissions.py`: add `AI_POLICY_MANAGE = "ai.policy.manage"` to `Permission`. It lands in `_ADMIN` automatically and must not be added to `_OPERATOR`. Bump `PLUGIN_API`'s minor and add the history line ("the `ai.policy.manage` permission"). Add the label wherever the frontend lists permissions.
2. Migration `backend/alembic/versions/00NN_ai_command_policy_rules.py` and model `AICommandPolicyRule` in `backend/app/ai/models.py`: `id`, `kind` (`add | disable`), `path` (the command words, `String(200)`, unique per kind), `mode` (`any | operands | exact`, NULL for `disable`), `options` (JSONB: `flags`, `values`, `optional`, `clusters`; NULL for `disable`), `note` (text), `source` (`ui`, reserved for GitOps later), `created_by_user_id`, `updated_by_user_id`, `created_at`, `updated_at`.
3. New module `backend/app/ai/policy_overlay.py`:
   - `merge(shipped: dict, rules: list) -> dict`: a deep copy of the shipped data, with each `disable` path removed from `read_only` (and its `options` and `gates`), and each `add` rule inserted into `read_only` and `options`.
   - `validate_rule(shipped: dict, rule) -> None`, raising `PolicyError`: an `add` rule's first word must not be in `NEVER_READ_ONLY`, a wrapper, or `NEEDS_GATES`, and must not be a command the shipped file lists at all (so an operator can never add `systemctl restart` or `kubectl exec` beside the shipped read-only forms); the path must be plain words; a `disable` rule must name an existing shipped path. Then `CommandPolicy.from_data(merge(shipped, existing + [rule]))` must succeed.
   - `async def load_effective(db) -> CommandPolicy`: builds from the file plus all rows; on `PolicyError` (a rule made invalid by an upgrade) logs it, skips the offending rule, and falls back to the shipped policy if it still fails. A broken overlay must never stop sessions or widen what is allowed.
4. Thread the policy: add `policy: CommandPolicy | None = None` to `ToolContext`; load it once with `load_effective` where each runner builds its context (the three places above), which covers session start and resume. `ssh.py` passes `ctx.policy` to `classify_command`. Change `ToolHandler.verdict_for` and the `preclassify` callable to accept the policy, and pass it from `gate.decide` (new keyword argument) so the gate and the tool always judge with the same policy.
5. API, new router `backend/app/api/ai_policy.py`, mounted under `/api/ai/command-policy`:
   - `GET ""`: `Depends(require("ai.use"))`. Returns the effective rules: every shipped read-only path with mode, whether it is disabled, and every operator rule with who changed it and when.
   - `POST "/rules"`, `PUT "/rules/{id}"`, `DELETE "/rules/{id}"`: `Depends(require("ai.policy.manage"))`. Each runs `validate_rule`, returns 422 with the `PolicyError` text on failure, and writes an audit row (`ai_policy_rule_created | updated | deleted`, `entity_type="ai_policy_rule"`, before and after state).
   - `POST "/check"`: `Depends(require("ai.use"))`. Body `{command}`; returns the verdict and reason under the effective policy, for the page's "try a command" box and for "Allow this".
6. Frontend: `frontend/components/ai/command-policy.tsx`, shown in Settings, section `ai`. A table of effective rules with a filter, source (shipped or added), changed by and when; "Add rule" dialog (command words, mode, flags, value options, note) and a switch to disable a shipped rule, both only with `can(user, "ai.policy.manage")`; the "try a command" box. Types in `frontend/lib/types.ts`.
7. "Allow this": on a tool call refused as "not a known read-only command", `chat-transcript.tsx` shows an "Allow this…" button to users with `ai.policy.manage`. It opens the Add rule dialog pre-filled from the refused command (first words as the path, mode `exact`, the options it used as flags), for the admin to narrow and confirm. The current session does not pick the rule up until it resumes; say so in the dialog.
8. Docstrings: correct `safety.py` and `policy.py` where they describe the file as the only source.

## Acceptance criteria
- An admin adds a rule for `acmecli status` with mode `exact`; a session started afterwards classifies `acmecli status` and `sudo acmecli status` as `read_only`, and `acmecli apply` as `mutating`.
- A session already running when the rule was added keeps its old policy until it resumes.
- An operator and a viewer get 403 from the three write routes; `tests/test_permissions.py` passes with the new routes; `GET /api/users/me` lists `ai.policy.manage` for admins only.
- Rejected with 422: a rule for `bash`, `sudo`, `xargs`, `awk`; a rule under a command the shipped file lists (`systemctl restart`, `kubectl exec`); a path listed twice.
- A command on the denylist stays `denied` even with an operator rule naming its first word.
- Disabling a shipped rule makes that form need approval; deleting the overlay row restores it.
- After an upgrade that makes an operator rule invalid, sessions still start, the rule is skipped, and the page marks it as invalid.
- Every create, update and delete appears in the audit log with the user.
- `PLUGIN_API`'s minor is one higher, with a history line.

## Test plan
- New `backend/tests/ai/test_policy_overlay.py` (merge, validation, fallback) and `backend/tests/ai/test_command_policy_api.py` (routes, permissions, audit). Extend `tests/ai/test_gate.py`, `tests/ai/test_ssh_tool_paths.py` and `tests/ai/test_agent_sdk_runner.py` to prove the gate and the tool use the session's policy.
- `cd backend && source .venv/bin/activate && pytest tests/ai/test_policy_overlay.py tests/ai/test_command_policy_api.py tests/ai/test_command_policy.py tests/ai/test_gate.py tests/ai/test_ssh_tool_paths.py tests/ai/test_agent_sdk_runner.py tests/test_permissions.py tests/test_permission_scopes.py -q`, then the full suite.
- Frontend: `cd frontend && npx tsc --noEmit && npm run lint`, and a new Playwright spec `e2e/command-policy.spec.ts` with mocked routes (add, 422 message, hidden controls for an operator).
- Running stack on lin-manager-2, host named "test": add a rule for a harmless script placed on the host, confirm a read-only chat can run it, then delete the rule and confirm it needs approval again.

## Docs and changelog
- `docs/ui/assistant.md`: a "Command policy" section (what a rule can and cannot do, precedence, who may edit). `docs/ui/admin.md`: the new permission in the role table. `docs/security-hardening.md`: replace any statement that the policy is fixed in the image.
- `CLAUDE.md`, "Access control": add `ai.policy.manage` to the examples and the new contract version.
- `CHANGELOG.md` `[Unreleased]`, Added: "**Editable command policy.** Admins can add read-only command rules and switch shipped ones off under Settings › AI. Rules are checked like shipped ones and cannot cover shells, wrappers or commands the shipped policy already lists. New permission `ai.policy.manage` (admins). Plugin API minor bump."

## Risks and rollback
- This widens what runs unattended, so the validation is the security boundary: `validate_rule` must be the only way a row is written, and `load_effective` must fail closed to the shipped policy.
- A rule with mode `any` for a tool that has a writing subcommand is the realistic mistake. The dialog defaults to `exact`, and the page shows a warning on `any`.
- Gate and tool judging with different policies would let a call pass one and fail the other; step 4's tests cover it.
- Rollback: revert the code; the table can stay. Reverting the permission after release needs a `PLUGIN_API` note.

## Assumptions
- [D-14](../DECISIONS.md): admins only, global scope, minor bump.
- Assumption (not yet confirmed): an operator rule may only name a command the shipped file does not list. That is the simplest rule that guarantees "cannot make `systemctl restart` read-only", at the cost that a new read-only subcommand of a shipped tool still needs a release. Question for Dennis: acceptable for the first cut?
- Assumption (not yet confirmed): reading the effective policy needs `ai.use`, not the manage permission.
- Assumption (not yet confirmed): operator rules carry no gates. A tool that needs a gate to be safe is not a candidate for an operator rule.

## Definition of done
- [ ] Permission, table, overlay module, policy threading, routes and page merged behind review; "Allow this" merged or split into a follow-up PR noted in `PROGRESS.md`.
- [ ] New and extended tests pass; full backend suite green; `tsc`, lint and the Playwright spec clean.
- [ ] Docs, `CLAUDE.md`, `PLUGIN_API` comment and `CHANGELOG.md` updated.
- [ ] Stage 8 behaviour 4 verified on lin-manager-2.
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
