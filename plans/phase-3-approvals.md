# Phase 3 — approval-gated remediation

Branch: `feat/ai-approvals`, from `dev` at `ff66898`.

Phase 1 gave read-only investigation, phase 2 scheduled it. Both stop at
the same wall: the model can find a problem and cannot fix it. This phase
lets a mutating command run *after a human says so*, without a Celery
worker sitting blocked for however long the human takes.

## The shape of the problem

An approval is a pause of unbounded length in the middle of an agent run.
Three things make that awkward here and they pull in different directions:

1. **A worker must not block on a person.** `long_running` has a 7200s
   limit and a finite pool; parking a slot for a lunch break is how you
   wedge the queue.
2. **Two runners own their loops differently.** `AgentLoop` calls a
   provider one turn at a time and can stop wherever it likes.
   `AgentSDKRunner` hands the loop to Claude Code and only gets a
   yes/no callback per tool. Whatever "park" means has to mean it on
   both.
3. **The transcript must stay valid at every instant.** Both the
   OpenAI and Anthropic wire formats require every `tool_call` to have a
   matching result. A transcript persisted mid-turn with a dangling call
   cannot be replayed — the resume would fail at the provider, not at
   LabDog, and would look like a backend outage.

## Decisions

### Park = end the run, not suspend it

`waiting_approval` is a terminal-ish state for the *task*, not a
suspended coroutine. The runner returns, the worker slot is freed, and
the decision endpoint dispatches a fresh task. Nothing is held open.

### Resume carries the outcome as a user message, on both paths

The obvious design — inject a `tool` result for the parked call and let
the model continue as if nothing happened — cannot work on the SDK path,
where resume is `resume=<sdk_session_id>` plus a new prompt. Rather than
have two shapes, both paths use one:

- **At park time**, every outstanding `tool_call` in the turn gets a
  result immediately ("Awaiting operator approval", or "Not run: the
  session parked on an earlier command in this turn"). The transcript is
  never left dangling, so decision 3 holds by construction.
- **At resume time**, a `user` message states what the operator decided
  and, if approved, what running it produced.

The cost is that the model reads the outcome as narration rather than as
a tool result. That is honest — it *is* narration; LabDog ran the
command, not the model — and it is one shape instead of two.

### LabDog executes the approved command, not the model

The operator approved a specific command on a specific host. Re-offering
the tool and hoping the model reissues the identical call would make the
approval advisory. So the resume task executes the stored arguments
itself, through the same helper either runner uses, and hands the model
the output.

The corollary: the approved call is a one-shot grant. If the model asks
for the same thing again afterwards it is gated again, because a second
execution is a second change. The resume prompt says the command already
ran so it has no reason to.

### One gate for both runners

`agent_sdk/gate.py::decide` already exists and already computes
`needs_approval`. `AgentLoop` currently has no gate of its own — autonomy
is enforced *inside* `tools/ssh.py`, which is too late to park. Hoist the
call to `decide` into `AgentLoop._run_tool` so both runners consult the
same function. The check inside `ssh.py` stays as defence in depth: it is
the last thing before a socket opens, and it is what a direct caller
(a test, a future non-loop caller) hits.

`gate.py` moves out of `agent_sdk/` to `app/ai/gate.py` — it stops being
SDK-specific the moment `AgentLoop` uses it.

### Snapshot before mutating, and what happens when it fails

Any mutating command against a host with a `VMMapping` gets a Proxmox
snapshot first, recorded on `AIToolCall.snapshot_name`. A host with no
mapping (bare metal, unmapped container) proceeds without one.

**A snapshot that errors blocks the command.** The alternative — run
anyway, note that the safety net is missing — quietly converts an
approval the operator gave under one set of assumptions into a riskier
change than the one they agreed to. The operator can re-approve knowing
there is no rollback; LabDog will not decide that for them.

## Work items

| # | Delivers |
|---|---|
| 1 | `AIApprovalRequest` + migration `0020` + FK on `ai_tool_calls.approval_id`; service helpers |
| 2 | Park-and-resume: `app/ai/gate.py`, park in both runners, `resume_session` task, `POST /api/ai/approvals/{id}` |
| 3 | Snapshot-before-mutating for approved and `full_auto` commands |
| 4 | `propose_action` — remediation via the action system rather than raw SSH |
| 5 | Approval cards in the transcript UI |
| 6 | Expiry reaper for abandoned gates |

Migrations `0016`–`0019` are taken (the numbering in the original
five-phase plan is stale), so this phase starts at `0020`.

## Open question, deliberately not solved here

The plan's "release the host advisory lock while parked" applies to
sessions driven by `_builtin.ai_task`, where an `ActionHostRun` holds the
per-host lock. A parked session holding that lock wedges the host queue
for the duration of human think-time. Handled by finishing the host run
at park time and letting resume run outside the lock — the alternative,
a new "parked" run status that the lock checks treat as not-running,
touches every caller of `host_lock.py` and is a larger change than this
phase should carry.
