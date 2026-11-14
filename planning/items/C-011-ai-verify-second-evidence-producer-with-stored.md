# C-011: AI verify: second evidence producer with stored evidence packs

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "AI verify evidence: narrow the window, add a second producer" (second half) |
| Tier / score | P2 / 3.5 |
| Stage | 8 |
| Effort | M |
| Autonomy | B |
| Depends on | C-010 |

## Goal
A pack author can declare the read-only commands whose output AI verify should judge for their action, alongside the built-in health readings. Every verify step's evidence is stored on its session in structured form, so "which verifications ran with an unavailable reading" is a query and not a search through transcripts.

## Scope
In scope:
- A second producer of `list[EvidenceItem]`: commands declared in the action's manifest, run over SSH at verify time.
- A JSONB evidence column on `AISession`, written for every verify session.
- Returning the stored evidence from the session API and showing it on the session page.

Out of scope:
- Letting the model choose commands. The arguments are fixed when the action is written; that is the safety property `app/ai/evidence.py` describes.
- Evidence for other session modes (chat, alert investigation).
- Loki or Mimir queries as evidence. They can be a third producer later.

## Context
- `backend/app/ai/evidence.py`: `EvidenceItem` (`label`, `value`, `source`, `unavailable_reason`; constructors `reading`, `missing`, `optional`), `render` and `summarise`. Its docstring names the seam: "One producer exists today … and an operator-declared pack of commands would plug in at exactly the same place, with no change to the verdict path."
- `backend/app/workflows/steps/ai_verify.py`: `evidence_from_state` (line 67) is the one producer; `run_ai_verification` (line 132) calls `run_verify_session`. The module docstring (lines 25 to 37) is the instruction for this item: "If you are adding that second producer, persist the pack while you are here … a JSONB column on `AISession`, written by `app.ai.verify.run_verify_session`."
- `backend/app/ai/verify.py`: `run_verify_session` (line 140) builds the prompt with `render(evidence)` and creates the session in `create_verify_session` (line 101).
- `backend/app/workflows/steps/verify.py`: `run_verification` (line 93) holds the SSH connection and collects the built-in readings; `_run_ai_verification` (line 471) hands the state on.
- `backend/app/actions/manifest.py`: `ai_verify_prompt` (line 122) and `ai_verify_fail_closed` (line 138) are the existing verify fields. `verify_playbook` and AI verify are alternatives: AI verify fires only when no `verify_playbook` is declared.
- Command safety: `classify_command` in `backend/app/ai/safety.py` and the policy in `backend/app/ai/command_policy.yaml`; secret redaction in `backend/app/ai/redaction.py` (`redact`).
- Session API: `AISessionDetail` in `backend/app/ai/schemas.py`, served by `GET /api/ai/sessions/{session_id}` (`backend/app/api/ai.py`, line 640, permission `ai.use`).

## Dependencies
- C-010, which changes the journal items this producer sits beside and adds the action times.
- Latest Community migration today is `0060_schedule_owner.py`; use the next free number.
- The manifest change is additive but is a public pack contract; review it as such.

## Implementation steps
1. `manifest.py`: add `ai_verify_commands: list[VerifyCommand]` (default empty), where `VerifyCommand` has `label: str` (max 80) and `command: str` (max 500). Validators: at most 10 entries; no template delimiters (reuse `_TEMPLATE_DELIMITERS` from `app/actions/validation.py`); and each command must classify as `read_only` under the shipped policy (`classify_command(command, command_policy.DEFAULT)`), otherwise the manifest fails to load with the classifier's reason. Carry the list on the action definition (`backend/app/actions/types.py`, `packs.py`).
2. New module `backend/app/ai/evidence_commands.py`: `async def evidence_from_commands(conn, commands, *, timeout=20) -> list[EvidenceItem]`. For each command: classify it again against the default policy at run time and emit `EvidenceItem.missing(label, "the command is no longer classified as read-only", source)` if that fails; run it over the given connection with the timeout; exit status 0 gives `EvidenceItem.reading(label, redact(stdout) or "(no output)", source=f"ssh: {command}")`; anything else gives `missing` with the exit status and the first line of stderr. Never raise.
3. `run_verification`: accept `verify_commands` and run `evidence_from_commands` over the same connection, inside the existing `try`, storing the items in the returned state under `extra_evidence`. Pass the manifest list from `_verify_with_builtin_checks` (`action_host.py`) and `_verify_member_with_builtin_checks` (`action_group.py`). Declared commands also count as a reason to run AI verification, like a prompt does.
4. `run_ai_verification`: `evidence = evidence_from_state(system_state) + system_state.get("extra_evidence", [])`.
5. Migration `backend/alembic/versions/00NN_ai_session_evidence.py`: add nullable JSONB `evidence` to `ai_sessions`. Add the column to `AISession`.
6. `evidence.py`: add `to_json(items) -> list[dict]` producing, per item, `label`, `source`, `available`, `unavailable_reason`, `producer` (`"state"` or `"commands"`; add a `producer: str = ""` field to `EvidenceItem`), `chars` (length of the value) and `truncated` (whether `render` would cut it). Store the value itself only up to `MAX_ITEM_CHARS`.
7. `verify.py`: `create_verify_session` takes `evidence` and writes `session.evidence = to_json(evidence)`. Every verify session gets it, with either producer.
8. API and UI: add `evidence: list[dict] | None` to `AISessionDetail`. On the assistant's session page (`frontend/components/ai/`), for a verify session show an "Evidence" table: label, source, available or the reason, producer. `frontend/lib/types.ts` gains the field.
9. Queryability: add `GET /api/ai/sessions?mode=verify&evidence_unavailable=true` as a filter on the existing list route (it already declares `ai.use`), implemented with a JSONB containment query on `evidence @> '[{"available": false}]'`. Add a GIN index on `ai_sessions.evidence` in the same migration.
10. Update the docstrings in `evidence.py` and `ai_verify.py` that say only one producer exists and that the pack is not persisted.
11. Sample pack: one action with `ai_verify_commands` for the tests.

## Acceptance criteria
- A manifest with `ai_verify_commands: [{label: "Listening sockets", command: "ss -tlnp"}]` loads; one with `command: "systemctl restart nginx"` or with `{{ }}` fails to load with a clear reason.
- A verify step for that action produces a prompt containing a "Listening sockets" reading whose source is `ssh: ss -tlnp`, after the built-in readings.
- A declared command that exits non-zero or times out is rendered as UNAVAILABLE with the reason; the verify step still completes.
- Every new verify session has `evidence` stored, one object per item, with `available` false for missing readings, including sessions for actions that declare no commands.
- `GET /api/ai/sessions/{id}` returns the evidence, and the list filter returns exactly the verify sessions that had an unavailable reading.
- Output of a declared command passes through `redact` before it is stored or sent to the provider.

## Test plan
- New `backend/tests/ai/test_evidence_commands.py` (producer, re-classification, redaction, failure cases) and extend `tests/ai/test_evidence.py` (`to_json`), `tests/ai/test_verify_session.py` (column written), `tests/ai/test_migration_columns.py`, and the manifest tests in `tests/test_action_packs.py`.
- `cd backend && source .venv/bin/activate && pytest tests/ai/test_evidence_commands.py tests/ai/test_evidence.py tests/ai/test_verify_session.py tests/ai/test_migration_columns.py tests/test_action_packs.py tests/test_permissions.py -q`, then the full suite once before the stage review.
- Frontend: `cd frontend && npx tsc --noEmit && npm run lint`.
- Running stack on lin-manager-2 with an AI provider configured and a Proxmox-mapped host named "test": run the sample action, open its verify session, check the evidence table and the list filter.

## Docs and changelog
- `docs/ui/actions.md`, manifest reference: `ai_verify_commands`, the read-only rule, the limits. `docs/ui/assistant.md`: the evidence table and the filter.
- `CHANGELOG.md` `[Unreleased]`, Added: "**Pack-declared evidence for AI verify, and stored evidence.** An action can list read-only commands whose output the verify step judges. Each verify session now stores its evidence in structured form, shown on the session and filterable by unavailable readings."

## Risks and rollback
- A declared command is a command a pack author runs on every target as the SSH user. Restricting it to what the shipped policy calls read-only, at load and again at run time, keeps it to what the assistant could already run without approval. Operator-added rules (C-019) are deliberately not consulted: a pack must not gain reach from one site's overlay.
- Stored evidence holds host output. It is redacted, capped, shown only to `ai.use`, and deleted with the session.
- Prompt size: ten more readings of up to 4000 characters fit inside `MAX_PACK_CHARS` only because `render` cuts the pack; the stored `truncated` flag records when that happened.
- Rollback: revert the commit and downgrade the migration; manifests that declare the field then fail or are ignored according to the unknown-key policy.

## Assumptions
- Assumption (not yet confirmed): the second producer is pack-declared commands in the manifest. The code comments say "an operator-declared pack of commands"; the board says only "a second producer". Question for Dennis: manifest (pack author) or a per-action setting (operator)?
- Assumption (not yet confirmed): the list filter is enough to make the evidence "queryable"; no separate reporting page.
- Assumption (not yet confirmed): values are stored, not only metadata. If storage of host output is unwanted, drop the value and keep the rest.

## Definition of done
- [ ] Manifest field, producer, column, API field, filter and session page table merged behind review.
- [ ] New and extended tests pass; full backend suite green; `tsc` and lint clean.
- [ ] Docs and `CHANGELOG.md` updated; stale docstrings corrected.
- [ ] Stage 8 behaviour 2 verified on lin-manager-2.
- [ ] `planning/roadmap.yaml` status set, and a line added to `planning/PROGRESS.md`.
