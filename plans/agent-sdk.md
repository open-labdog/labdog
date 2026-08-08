# Agentic sessions on a subscription: the Claude Agent SDK

## Why this exists

A subscription token authenticates exactly one thing: Claude Code's own
harness. There is no way to point the Messages API at a subscription. So
"agentic work billed against a flat subscription" means driving Claude
Code programmatically, and the supported front door for that is the
**Claude Agent SDK** (`claude-agent-sdk`, Anthropic-maintained).

This replaces the hand-rolled `--input-format stream-json` transport that
`plans`/TODO previously carried as a known gap. The SDK *is* that
transport, already written and version-matched.

## What was verified (SDK 0.2.134, bundled CLI 2.1.226)

Everything below was run live against the real binary, not reasoned from
docs. Probes are in the session scratchpad; the findings are here because
they were expensive to establish.

### 1. In-process tools work, and are the only tools that need to exist

`create_sdk_mcp_server(...)` + `@tool` exposes plain async Python
functions to the model as `mcp__labdog__<name>`. Verified: the model
called `list_hosts`, got our JSON back, and reported it accurately.

### 2. Built-in tools are ON by default — this is the sharp edge

With `tools` left unset, the init handshake advertised **the entire
Claude Code toolset** — `Bash`, `Read`, `Write`, `Edit`, `WebFetch`,
`Task`, and more — alongside ours. The model did not merely see them: it
**ran `Bash`** before finding our tool.

`strict_mcp_config=True` does *not* prevent this. It governs MCP servers
only; the built-ins are not MCP servers.

The lever is `tools=[]`, which the SDK maps to `--tools ""`:

| `tools` value | CLI flag | Result |
|---|---|---|
| unset (default) | *omitted* | **all built-ins exposed** |
| `[]` | `--tools ""` | only our MCP tools |
| `["a","b"]` | `--tools a,b` | named set |
| `{"type":"preset","preset":"claude_code"}` | `--tools default` | all built-ins |

Verified with `tools=[]`: init advertised exactly
`['mcp__labdog__list_hosts', 'mcp__labdog__restart_service']`. Nothing
else.

**LabDog must always pass `tools=[]`.** A LabDog agent session with
`Bash` and `Write` in the container is a different product with a
different threat model.

### 3. `allowed_tools` silently shadows the permission callback

The SDK warns (`CanUseToolShadowedWarning`), and the warning is load-bearing:

> An `allowed_tools` entry that allows a whole tool auto-approves it
> **before the callback is consulted**.

So listing our tools in `allowed_tools` — the obvious thing to do —
disables the approval gate without failing. Leave `allowed_tools` empty
and let every call fall through to `can_use_tool`. Treat any future
addition to `allowed_tools` as a safety change.

### 4. The permission gate works, allow and deny

`can_use_tool(name, input, ctx)` is consulted for **every** call.
Returning `PermissionResultDeny(message=...)` stops execution — verified
that the denied tool's Python function never ran — and the message is
delivered to the model, which routed around it and reported the denial
honestly. Denials also land in `ResultMessage.permission_denials` with
tool name, input, and `tool_use_id`: a ready-made audit record.

**Constraint:** `can_use_tool` requires *streaming mode*. One-shot
`query(prompt="...")` raises `ValueError`. Sessions must use
`ClaudeSDKClient`. Not a problem — LabDog wants the persistent client
anyway — but it forecloses the simpler API.

### 5. Park-and-resume works across process death

The Phase 3 approval design needs a session to end, wait hours for a
human, and continue. Verified in a **fresh process** with no in-memory
state: `resume=<session_id>` restored the conversation, the model
remembered the host and service from the earlier turn without re-running
`list_hosts`, executed the now-approved action, and kept the same
`session_id`.

This is the persist-and-return step machine, supplied rather than built.

### 6. Suppressing built-ins is also a large cost win

Same trivial task, measured:

| Configuration | cache_creation | cache_read | cost |
|---|---|---|---|
| built-ins exposed | 16,041 | 36,967 | **$0.0385** |
| `tools=[]` | 0 | 0 | **$0.0050** |

~7.7× cheaper, because Claude Code's system prompt and tool definitions
stop being sent. (`total_cost_usd` is reported even on a subscription —
it is notional list price, useful for comparison, not a bill.)

### 7. Binary resolution: bundled first

`_find_cli()` order is **bundled → PATH → explicit `cli_path`**. The
wheel ships `_bundled/claude` (284 MB apparent, ~131 MB actual — sparse)
pinned by `_cli_version.py` to a matching CLI version. The SDK therefore
ignores the apt-installed binary the image currently carries.

Consequences:
- Version drift stops being our problem: `uv.lock` pins the SDK, the SDK
  pins its CLI.
- The image should not carry **both**. Drop the apt fetcher stage and let
  the legacy single-shot provider resolve the bundled binary too.

### 8. Rate limits are observable

`RateLimitInfo` carries `status`, `utilization`, `resets_at`,
`rate_limit_type`, `overage_status`. On a subscription the money budget
is meaningless but *quota* is not — this is what the budget UI should
show for SDK-backed providers.

## Design

The SDK **owns the agent loop**. That is the whole point, and it means
the new backend is not an `LLMProvider` — it cannot implement
`stream_turn`, because it is never asked for one turn at a time.

So it enters one level up, as a second **session runner** behind the same
persistence and streaming surfaces:

```
            AISession / AIMessage / AIToolCall / SSE  (unchanged)
                       |                    |
                 AgentLoop            AgentSDKRunner        <- new
              (openai_compat,        (claude_agent)
               anthropic)                   |
                       \                    /
                        app.ai.tools.TOOL_REGISTRY          (shared)
                        app.ai.safety     app.ai.redaction  (shared)
```

Safety is unaffected by who drives the loop, because it lives **inside
the tool functions**: classifier, redaction, target-host allowlist, and
`AuditLog` rows all execute in LabDog's code either way. The SDK never
gets a capability we did not hand it — and with `tools=[]` it has none of
its own.

### New module `backend/app/ai/agent_sdk/`

- `bridge.py` — wraps each `ToolHandler` as an `SdkMcpTool`; builds the
  in-process server. One adapter, no per-tool special casing.
- `gate.py` — the `can_use_tool` callback. Resolves the handler, runs the
  same classifier the loop uses, applies the session's autonomy level,
  returns allow/deny. This is where `read_only` / `approval` / `full_auto`
  is enforced, and it now happens *before* execution rather than around it.
- `runner.py` — drives `ClaudeSDKClient`, persists `AIMessage` /
  `AIToolCall` rows, emits the existing SSE event shapes, returns a
  `LoopOutcome`.

### Packaging

`claude-agent-sdk` goes in an **optional extra**, not the base
dependencies: the wheel is platform-specific and carries a ~131 MB
binary, which has no business inside `.deb` / `.rpm` artefacts. The
Docker image installs the extra; package installs do not, and the import
is guarded so a missing SDK degrades to "provider unavailable" rather
than an import error at startup.

## Open questions

1. **Terms.** Confirm that SDK-driven headless use on a subscription is
   sanctioned, and that billing lands on the subscription. `claude
   setup-token` exists for automation, but read the fine print.
2. **Does `claude_agent` supersede `claude_cli`?** The SDK does
   everything the single-shot provider does, including single-shot
   (`tools=[]`, one prompt). Keeping both means two subprocess backends.
   Decide after the runner works; a merge is a migration plus a UI change.
3. **Where does resume state live?** `resume=<session_id>` relies on the
   CLI's own session files under `CLAUDE_CONFIG_DIR`, which must survive
   container restarts (a volume). `ClaudeAgentOptions.session_store`
   accepts a custom `SessionStore`, so Postgres-backed sessions are
   possible — better durability, more code. Volume first.
