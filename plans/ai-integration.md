# LabDog AI Integration — Agentic System Administration

## Context

LabDog's goal is a homelab that stays on the latest/most-secure software with minimal human intervention. This feature adds an AI/LLM integration where the user connects an LLM (local or provider) that acts as a system administrator: verifying health after upgrades, running security scans, performing upgrades, testing Proxmox backups, and investigating Grafana alerts. It operates in four modes: ad-hoc interactive chat, scheduled autonomous runs, verify-step for actionpacks, and reaction to Grafana alerts (with direct Mimir/Loki query access).

**User decisions (fixed):**
- LLM backends: **all three** — OpenAI-compatible API (Ollama/vLLM/OpenRouter/OpenAI), Anthropic Messages API, and Claude Code CLI shell-out
- Autonomy: **user-selectable per task/session** (read-only / approval-gated / full-auto) with safe defaults and strong guardrails
- Alert intake: **both** Grafana webhook receiver and Alertmanager polling fallback
- Surface: **automated runs with readable reports + interactive chat page** with live streaming

**Key existing seams (verified):**
- `_builtin.*` pseudo-actions (`backend/app/actions/builtins.py`, dispatch map `PER_HOST_TASK_FOR_BUILTIN` at `backend/app/tasks/action_orchestrator.py:22`, wrappers in `backend/app/tasks/builtin_dispatchers.py`) give any new capability scheduling (ScheduledAction + RedBeat), run history (ActionRun/ActionHostRun), per-host advisory locking (`backend/app/tasks/host_lock.py`), SSE streaming (Redis channel `actions.run.{id}` → `GET /api/actions/runs/{id}/stream`), cancel, audit, and the Proxmox snapshot/rollback envelope for free
- Integration credential pattern: `backend/app/grafana/` + `backend/app/proxmox/` (encrypted token column via AES-256-GCM `backend/app/crypto/encryption.py`, httpx client with test_connection, tri-state secret update, INTEGRATIONS sidebar page)
- Existing AI code: `backend/app/workflows/steps/ai_verify.py` shells out to `claude -p`, fail-open on every error path, and is near-dead — callers (`action_host.py:750`, `action_group.py:1025`) pass `verification_prompt=None`
- Grafana clients are minimal: instant PromQL only; Loki registered but never queried; **no alert ingestion exists**; GitOps webhook receiver (`backend/app/api/webhooks.py`) is the pattern to copy
- No LLM SDK deps; `httpx>=0.27` already present — all providers implemented over httpx/subprocess, zero new heavyweight deps

**Repo workflow note:** per repo CLAUDE.md, implementation branches from `dev`, and in-flight design docs live in `plans/` on the branch (deleted before PR). Copy this plan there when starting.

---

## Design

### Data model (new module `backend/app/ai/models.py`, all registered in `_EXTERNAL_MODELS` in `backend/app/models/__init__.py`)

1. **`AIProvider`** — per-instance encrypted credential row (Grafana clone): `name`, `provider_type` (`openai_compat`|`anthropic`|`claude_cli`), `base_url`, `model`, `encrypted_api_key` (LargeBinary, AAD `ai_provider:{id}`), `verify_ssl`, `ca_cert_pem`, `max_tokens`, `temperature`, `is_default`, `allow_cloud_egress`, **pricing: `input_cost_per_mtok` / `output_cost_per_mtok`** (user-entered USD per million tokens; default 0 — correct for local Ollama/vLLM, entered manually for cloud providers since OpenAI-compatible endpoints can't self-report pricing). Must be added to `backend/scripts/rotate_encryption_key.py` registry + `docs/encryption-key-rotation.md`.
2. **`AISession`** — one agentic run/conversation: `provider_id`, `mode` (`chat`|`scheduled`|`verify`|`alert_investigation`), `mission`, `autonomy_level` (`read_only`|`approval`|`full_auto`), `status` (`queued`|`running`|`waiting_approval`|`succeeded`|`failed`|`cancelled`), `target_host_ids` (JSONB allowlist), `action_run_id` (nullable FK — set when driven by `_builtin.ai_task`), token/cost accounting, `report_markdown`, `resume_state` (JSONB loop cursor for approval resume), `alert_event_id` (nullable).
3. **`AIMessage`** — append-only transcript: `session_id`, `seq`, `role`, `content`, `tool_calls` (JSONB, provider-normalized), `tokens`. Doubles as LLM context store and UI transcript.
4. **`AIToolCall`** — safety/audit record per executed tool: `tool_name`, `arguments`, `classification` (`read_only`|`mutating`|`denied`|`unknown`), `target_host_id`, `approval_id`, `status`, `result_summary`, `snapshot_name`. Every SSH command also writes an `AuditLog` row (`backend/app/audit/logger.py`).
5. **`AIApprovalRequest`** — gated command pause point: `session_id`, `tool_call_id`, `summary`, `command_preview`, `status` (`pending`|`approved`|`rejected`|`expired`), `decided_by_user_id`, `expires_at`.
6. **`AlertEvent`** — alert intake: `source` (`grafana_webhook`|`alertmanager_poll`), `fingerprint` (indexed, unique with `starts_at` for dedup), `alertname`, `severity`, `status`, `labels`/`annotations` (JSONB), `dedup_count`, `investigation_session_id`.
7. **`AIUsageDay`** — durable daily spend ledger, one row per `(date, provider_id)`: `prompt_tokens`, `completion_tokens`, `cost_usd`, `session_count`. Upserted by the loop after every LLM turn (atomic `INSERT … ON CONFLICT … DO UPDATE` increment) so accounting survives session deletion/retention and budget checks are one cheap aggregate query, not a scan over transcripts.

Migrations: **0014** ai_providers → **0015** sessions/messages/tool_calls + ai_usage_days → **0016** approval_requests → **0017** alert_events (one per phase, each self-contained).

### Provider abstraction (`backend/app/ai/providers/`)

One async streaming interface, three implementations:
- `base.py` — `LLMProvider` protocol: `stream_turn(messages, tools, *, max_tokens, temperature) -> AsyncIterator[StreamEvent]`; `NormalizedMessage` (stored shape in AIMessage), `StreamEvent` union (`TextDelta`, `ToolCallStart/Delta/End`, `Usage`, `TurnEnd`), `ToolSpec` (JSON-schema tool defs, translated per provider); `build_provider(row)` factory decrypting the key.
- `openai_compat.py` — httpx POST `{base_url}/chat/completions` with `stream=true` + `tools`; SSL context mirroring `PrometheusClient`. Covers Ollama/vLLM/OpenRouter/OpenAI.
- `anthropic.py` — httpx POST `/v1/messages` with native `tool_use`/`tool_result` blocks. **Read the `claude-api` skill before implementing** (streaming event names, block shapes, model ids).
- `claude_cli.py` — `asyncio.create_subprocess_exec` (never `shell=True`) with `claude -p --output-format stream-json --input-format stream-json` and all CLI-native tools disabled, so LabDog owns every tool round-trip. If the installed CLI can't do stream-json tool round-tripping, factory downgrades it to single-shot report/verify mode (current ai_verify behavior) and the UI marks it non-interactive.

API keys stored per-instance encrypted (not TOML) — matches Grafana/Proxmox, supports multiple providers, participates in key rotation, editable from UI.

### Agent loop (`backend/app/ai/loop.py` + `backend/app/tasks/ai_task.py`)

**Tools** (`backend/app/ai/tools/`, each with ToolSpec + async run + static classification):
`run_ssh_command` (via `ssh_connect_host` in `backend/app/ssh_utils.py`), `list_hosts`/`get_host_facts`, `query_mimir` (**add `query_range` to `PrometheusClient`**), `query_loki` (**new LogQL `/loki/api/v1/query_range` method** — genuinely new capability), `get_action_history`, `proxmox_status`/`check_backup` (`ProxmoxClient`), `propose_action` (routes through normal actionpack run + approval, never raw SSH), `request_approval`.

**Command safety classification** (`backend/app/ai/safety.py`): default-deny classifier — allowlist of read-only command heads (`cat`, `journalctl`, `systemctl status`, `df`, `ss`, `dpkg -l`, …) + hard denylist; pipelines split on `;`/`&&`/`||`/`|` and classified by most-dangerous segment; unknown heads → treated as `mutating`. LLM self-declared intent is advisory only, never trusted to downgrade.

**Autonomy levels**: `read_only` (mutating auto-rejected, returned to model as policy denial), `approval` (mutating creates AIApprovalRequest and parks), `full_auto` (mutating runs after mandatory snapshot; denylist still hard-blocks). Defaults: `read_only` everywhere; raising is always explicit.

**Approval without blocking a worker** — persist-and-return step machine: on gate, write pending AIToolCall + AIApprovalRequest, snapshot loop cursor into `AISession.resume_state`, set status `waiting_approval`, publish SSE `approval_required`, **return from the Celery task**. `POST /api/ai/approvals/{id}` re-dispatches `resume_session`, which rehydrates state, injects the result (output or "rejected by operator"), continues. The driving ActionHostRun releases the host advisory lock while parked (new parked status treated as not-running) and re-claims on resume, so human think-time never wedges the per-host queue. `expires_at` reaps abandoned gates.

**Snapshot-before-mutating**: before any mutating command on a VM-mapped host, `create_snapshot` via existing `backend/app/workflows/steps/snapshot.py` machinery; record on AIToolCall; cleanup on success, offer rollback on flagged failure.

**Caps** (loop-enforced, AppSetting defaults + per-session override): max_iterations 15, token budget, max_commands 20, wall-clock 900s active-time. Exceeding any → terminate with partial report.

### Cost tracking & spending limits

- **Accounting**: every provider turn yields a `Usage` StreamEvent; the loop converts tokens → USD via the provider's pricing fields, increments `AISession` counters, and upserts the `AIUsageDay` ledger. Claude CLI usage is parsed from the stream-json `result` message (which includes token usage); if unavailable, tokens recorded as 0 with a `cost_unknown` flag on the session.
- **Budgets** (AppSettings): `ai.budget_daily_usd` and `ai.budget_monthly_usd` (0 = unlimited), plus advisory `ai.budget_warn_pct` (default 80). Optional per-provider monthly cap column `monthly_budget_usd` on AIProvider for mixed local/cloud setups.
- **Enforcement — hard stop**: budget checked (a) before starting/resuming any session — over budget → session refused/blocked with a clear error, alert auto-investigations skipped with `investigation_policy_outcome="skipped_budget"`, scheduled `_builtin.ai_task` runs fail fast with an explanatory report; and (b) between loop iterations — crossing the budget mid-session terminates gracefully with a partial report. Local providers with pricing 0 are unaffected by USD budgets (token caps still apply).
- **Warning**: crossing `budget_warn_pct` publishes a warning event into active session streams and shows a banner in the UI.
- **UI**: a **Usage & budget** panel on the AI provider settings page — current day/month spend vs budget (progress bars), spend and token charts by day and by provider from `AIUsageDay`, per-session cost shown in session list and run reports. Endpoints: `GET /api/ai/usage?window=…` (aggregates) alongside the provider CRUD.

### Surfaces

**(a) `_builtin.ai_task`** — new ActionDefinition (destructive=True, parameters: `mission`, `autonomy_level` choice, `provider_id`, cap overrides) registered in `backend/app/actions/builtins.py`; per-host task `app.tasks.ai_task.run_builtin_ai_task` added to `PER_HOST_TASK_FOR_BUILTIN` and following `builtin_dispatchers.py` lock participation (`_begin_host_run`/`_finish_host_run`). Scheduling, SSE, run history, cancel come free. Final `report_markdown` shown in action-run detail.

**(b) Chat UI** — `frontend/app/(dashboard)/assistant/` (session list + `[sessionId]` live transcript); sidebar entry (`frontend/components/sidebar.tsx` ~line 139). Streaming: `POST /api/ai/sessions` dispatches `run_chat_session` → SSE `GET /api/ai/sessions/{id}/stream` relaying Redis channel `ai.session.{id}` (same idiom as actions SSE, `backend/app/api/actions.py:365`). Approvals via normal POST, rendered as inline cards on `approval_required` events. New components: `components/ai/{chat-transcript,chat-composer,approval-card,tool-call-badge,provider-form}.tsx`. Provider CRUD page follows the Grafana page pattern. Backend router `backend/app/api/ai.py` registered in `main.py`.

**(c) ai_verify rework** — rewrite `backend/app/workflows/steps/ai_verify.py` onto the provider abstraction (default provider, single-shot, no tools), keep PASS/FAIL parse. Add `ai_verify_prompt` + `ai_verify_fail_closed` to `ActionManifest` (`backend/app/actions/manifest.py`) → `ActionDefinition` (`types.py`) → verify call sites, wiring the real prompt through the currently-`None` args at `action_host.py:750` and `action_group.py:1025`. Default fail-open (preserves current behavior; missing provider / AI disabled → skip); manifests can opt into fail-closed for critical upgrades.

**(d) Alert intake** — 
- Webhook: `POST /api/webhooks/grafana-alerts` in `backend/app/api/webhooks.py` (token auth, parse Grafana contact-point JSON `alerts[]`, upsert AlertEvent by fingerprint with dedup_count, `celery_app.send_task` and return immediately — same shape as GitOps webhooks).
- Poller: `backend/app/tasks/ai_alerts.py::poll_alertmanager`, RedBeat self-registration (pattern: `tasks/drift.py:158`), hits `{mimir_base}/alertmanager/api/v2/alerts` on the default Mimir instance reusing `PrometheusClient` auth/SSL; idempotent with webhook via same fingerprint dedup.
- Auto-investigation policy: AppSettings `ai.auto_investigate_enabled` + `ai.auto_investigate_min_severity`; eligible alerts spawn `AISession(mode="alert_investigation", autonomy_level="read_only")` seeded with alert context; report linked to the alert row.

### Guardrails (defaults)

- Global kill switch: AppSetting `ai.enabled` (default off); checked at every entry point.
- Denylist (all autonomy levels incl. full_auto): `rm -rf /`(+`/*`), `mkfs*`, `dd of=/dev/*`, fork bombs, disk wipes, `chmod -R 000 /`, unscoped `iptables -F`/`nft flush ruleset`, shutdown/reboot of the LabDog control host itself, removal of ssh/critical packages.
- Target-host allowlist: `run_ssh_command` refuses hosts outside `AISession.target_host_ids` — the LLM cannot widen its own blast radius.
- Secret redaction (`backend/app/ai/redaction.py`): strip private keys, `password=`/`token=`, bearer/JWT-shaped strings, long base64 blobs from command output **before** it enters AIMessage / goes to the provider.
- Data-egress control: `AIProvider.allow_cloud_egress` + AppSetting `ai.allow_cloud_providers`; when off, only private/localhost base_urls (or claude_cli) may run; provider UI labels off-site providers clearly.

---

## Phasing (each independently shippable, branched from `dev`)

| Phase | Delivers | Useful alone as |
|---|---|---|
| **1** | Migrations 0014/0015; AIProvider/AISession/AIMessage/AIToolCall/AIUsageDay; all 3 provider impls; read-only tools (list_hosts, facts, read-only SSH, Mimir instant); `api/ai.py` CRUD + SSE + usage endpoint; chat UI; kill switch, classifier, redaction; **cost accounting + daily/monthly budget enforcement + usage panel**; rotate-script/docs | Interactive read-only investigation assistant with spend control |
| **2** | `_builtin.ai_task` + `tasks/ai_task.py` (lock participation, conf.include, task_routes); Mimir range + Loki LogQL + action-history + Proxmox tools; reports in run detail | Schedulable nightly health-check reports |
| **3** | Migration 0016; approvals (persist-and-return + resume_session); approval UI; snapshot-before-mutating; propose_action; full_auto | Approval-gated remediation |
| **4** | Migration 0017; AlertEvent; Grafana webhook + Alertmanager poller; auto-investigate policy; alert list UI | Alert-driven investigations |
| **5** | ai_verify rework; manifest `ai_verify_prompt`/`ai_verify_fail_closed`; wire prompt through dead call sites | Real AI verify step for destructive actions |

## Files

**New backend:** `app/ai/{__init__,models,schemas,service,loop,safety,redaction,builtins}.py`, `app/ai/providers/{base,openai_compat,anthropic,claude_cli,factory}.py`, `app/ai/tools/{__init__,ssh,metrics,logs,hosts,proxmox,actions,approval}.py`, `app/api/ai.py`, `app/tasks/{ai_task,ai_alerts}.py`, `alembic/versions/0014–0017`.

**New frontend:** `app/(dashboard)/assistant/{page,{sessionId}/page}.tsx`, provider settings page, `components/ai/*.tsx`, `docs/ui/assistant.md`.

**Modified:** `app/models/__init__.py` (_EXTERNAL_MODELS), `app/tasks/__init__.py` (conf.include + task_routes), `app/tasks/action_orchestrator.py:22` (dispatch map), `app/actions/builtins.py`, `app/actions/{manifest,types}.py`, `app/tasks/action_host.py:750` + `app/tasks/action_group.py:1025`, `app/workflows/steps/ai_verify.py`, `app/api/webhooks.py`, `app/grafana/client.py` (query_range + LogQL), `app/settings_service.py` (SETTING_DEFINITIONS: `ai.*` keys), `app/main.py` (router), `scripts/rotate_encryption_key.py`, `docs/encryption-key-rotation.md`, `frontend/components/sidebar.tsx`, `frontend/lib/{types,schemas,mutations}.ts`.

## Verification

- **Fake provider** `tests/ai/fake_provider.py` (scripted StreamEvent sequences via the factory seam) — backbone of all loop tests, no network.
- Phase 1: pytest (testcontainers per `tests/conftest.py`) — provider CRUD + encryption round-trip, classifier truth table, redaction units, full loop with fake provider + mocked `ssh_connect_host`; **budget tests**: Usage events roll up into AIUsageDay, session refused when daily/monthly budget exceeded, mid-loop termination on budget crossing, $0-pricing providers unaffected. Manual: chat SSE against local **Ollama** (`base_url=http://localhost:11434/v1`); Anthropic path vs recorded stream fixture.
- Phase 2: `_builtin.ai_task` registry + dispatch + ActionHostRun lifecycle/lock tests (reuse existing builtin-dispatcher test patterns); Mimir/Loki methods vs httpx MockTransport.
- Phase 3: park-and-resume round trip (resume_state persisted, approved result injected; rejection path), denylist hard-block, snapshot-called assertion (mock ProxmoxClient), lock-release-while-parked.
- Phase 4: webhook fixture → AlertEvent upsert + dedup increment; poller vs Alertmanager v2 fixture; policy gating. Manual: curl with a real Grafana contact-point payload.
- Phase 5: PASS/FAIL via fake provider; fail-open on missing provider/disabled; fail-closed opt-in; prompt threading manifest→action_host.
- CI must stay green: ruff, bandit (no `shell=True`, no logged secrets), pytest, gitleaks, pip-audit (no new SDK deps), npm audit.
