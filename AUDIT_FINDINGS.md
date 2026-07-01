# LabDog Full-Code Audit — Findings

_Generated 2026-07-01 from a four-agent parallel audit (backend security, backend correctness/dead-code, frontend, dependencies)._

Items marked **✓** were re-verified against source by hand. Severity: 🔴 High · 🟠 Medium · 🟢 Low.
Status column is for tracking work on this branch: `todo` / `in-progress` / `done` / `wontfix`.

Overall the codebase is disciplined — parameterized SQL throughout, AES-256-GCM with AAD, HMAC-verified
webhooks, allow-list input validation, closed registration. The items below are the real gaps.

---

## Security

| ID | Sev | Status | Finding | Location |
|----|-----|--------|---------|----------|
| S1 ✓ | 🔴 | **done** | **Interactive SSH terminal does no host-key verification** (`known_hosts=None`). MITM on the LAN path impersonates a managed host and captures the root PTY + any secrets typed. Sync path already uses TOFU `ssh_connect_host`; terminal bypasses it. _Fixed: routed `open_ssh_shell` through `ssh_connect_host` + `HostKeyMismatchError` handling._ | `backend/app/ssh_terminal/ssh_connect.py:77` (root: `backend/app/ssh_utils.py:85`) |
| S2 ✓ | 🔴 | todo | **Action-pack CRUD is "Superuser-only" per its docstring but gated only by `current_active_user`.** Any active user can create/enable packs; "local" packs load arbitrary filesystem paths whose manifests drive root Ansible runs against the fleet — privilege escalation / potential RCE. | `backend/app/api/action_packs.py` (lines 132/161/201/220/270/301/331) |
| S3 | 🟠 | **in-progress** | **Systemic missing host-key verification** on all collectors + drift tasks + `execute_service_command` (connect by IP with `known_hosts=None`). Same root cause as S1 — migrate to `ssh_connect_host`. **Done:** firewall-state path (`sync/collector.py` `collect_firewall_state`/`collect_current_rules` + callers in `sync/diff.py`, `host_state._collect_firewall`) and the `tasks/drift.py` source-IP probe. **Remaining (identical transform, needs pytest validation):** `services/collector.py` (4 fns), `packages/collector.py` (2), `cron/collector.py`, `resolver/collector.py`, `user_mgmt/collector.py` (2), `hosts_mgmt/collector.py` + their ~25 callers in `sync/plan.py`, `api/*_sync.py`, `api/*_drift.py`, `host_state.py`; and the source-IP probes in `hosts_drift.py:104`, `package_drift.py:76`, `resolver_drift.py:83/184`, `service_drift.py:79`, `user_drift.py:92`, `cron_drift.py:91`, plus `service_live.py:164`. **Keep bare `ssh_connect` (first-contact, no Host row):** `api/hosts.py:131`, `discovery/verify.py:73`, `host_state.py:653` fallback. | see Remaining |
| S4 ✓ | 🟠 | todo | **`PUT /git-repos/{id}` bypasses the SSRF host-block** applied at create. Authenticated user can point a repo at `169.254.169.254` / `127.0.0.1:<port>` → attacker-directed outbound clone/fetch. | `backend/app/api/git_repos.py:94`, `backend/app/schemas/git_repos.py:113` |
| S5 | 🟠 | todo | **`branch`/`ref` unvalidated** into `git clone --branch <ref>` argv + gitpython (also flows into webhook ref comparisons). Option-injection largely defused by list args, but the value is entirely unconstrained. Add `^[\w./-]+$` validator on create + update. | `backend/app/actions/git_sync.py:122/127`, `backend/app/schemas/git_repos.py` |
| S6 | 🟢 | todo | `conn.run(f"...")` interpolates a few **remote-derived** values (home dir, crontab user) without `shlex.quote` (self-injection on an already-owned host only). Use `shlex.quote` uniformly. | `backend/app/user_mgmt/collector.py:59/67`, `backend/app/api/host_state.py:520` |
| S7 | 🟢 | todo | No CSP / security headers possible with `output:'export'` — must be enforced at the reverse proxy (release-checklist item). | `frontend/next.config.ts` |
| S8 | 🟢 | todo | CORS `allow_origins` operator-configurable with `allow_credentials=True` (safe default); XFF rate-limit key can be padded to rotate the login limiter when a proxy is trusted. Informational. | `backend/app/main.py:281/117`, `config.py:57` |

---

## Correctness Bugs

| ID | Sev | Status | Finding | Location |
|----|-----|--------|---------|----------|
| B1 ✓ | 🔴 | todo | **Package hold/unhold drift is never detected.** `compute_diff` collects hold flags but never compares them, and `has_drift` ignores them — a host needing `apt-mark hold`/`versionlock` reads as *in-sync* while a sync run *would* change it. Collector also never reports actual hold (would false-positive if compared). Drift status and sync behavior disagree. | `backend/app/packages/diff.py:27,53-77`; collector `backend/app/packages/collector.py`; gated at `tasks/package_drift.py:63` |
| B2 | 🔴 | todo | **`InstallFirewallSection` poll loop leaks / setState after unmount.** Up-to-2-min `for(i<60)` poll with no unmount guard or AbortController; navigating away keeps polling + updates unmounted state; remount can race two loops. | `frontend/app/(dashboard)/hosts/[id]/client-page.tsx:461-517` |
| B3 | 🟠 | todo | **Host-override firewall rules sort *below* group rules.** Overrides never get `group_priority` set → treated as 0 in the final reverse sort, so with any positive-priority group they land lower in the (first-match) ruleset. Only rules with *distinct* signatures affected (dedup handles identical). Confirm against backend ordering. | `backend/app/rules/merge.py:71,109`; `backend/app/rules/converter.py` |
| B4 | 🟠 | todo | **Two divergent rule-equivalence keys.** `merge_group_rules` dedups on a raw 8-field tuple *before* host-ref resolution; diff engine `_match_key()` normalizes CIDRs/ports and includes `action`. Literal CIDR vs host-ref resolving to same CIDR → kept by merge, collapsed by diff → duplicate in effective-rules display; merge also dedups allow-vs-deny on the same tuple. | `backend/app/rules/merge.py:59-104` vs `backend/app/rules/model.py:54-66` |
| B5 | 🟠 | todo | **SSH terminal reconnect effect ignores `connect`/`onData` deps** (exhaustive-deps disabled); captures first `connect`; reconnect relies on synchronous `wsRef` nulling — fragile, latent double-connect/race. | `frontend/components/ssh-terminal.tsx:31-84`, `frontend/hooks/use-terminal-websocket.ts` |
| B6 | 🟢 | todo | Optimistic-update helper assumes cache is always a flat array (silent no-op if `undefined`, can corrupt non-array caches). | `frontend/lib/mutations.ts:30-48` |
| B7 | 🟢 | todo | Anti-lockout SSH rule isn't run through dedup → duplicates a user rule with the same signature in the effective set. | `backend/app/rules/merge.py:11-22,111` |
| B8 | 🟢 | todo | `ipRegex`/`cidrRegex` accept invalid octets (`300.1.1.1` passes); register form skips the existing zod min-length schema. | `frontend/lib/schemas.ts:4-8`, `frontend/app/(auth)/register/register-form.tsx` |
| B9 | 🟢 | todo | SPA route handler does a **global** `content.replace('"placeholder"', …)` — corrupts any legitimate `"placeholder"` in the payload. | `backend/app/main.py:452-453` |

---

## Dependencies & Versions

**Backend pinning hygiene: POOR** — no lockfile, all constraints lower-bound `>=`, so builds are
non-reproducible and can resolve to known-vulnerable versions. **Frontend: GOOD** — `package-lock.json`
current, versions near-latest; Next.js 16.2.9 is ahead of the May-2026 security line.

> ⚠️ CVE identifiers below were gathered via live web lookups by the audit agent and cited to advisory URLs,
> but have **not** been independently re-verified in-repo. Confirm each against its advisory before acting.

| ID | Sev | Status | Action | Detail |
|----|-----|--------|--------|--------|
| D1 | 🔴 | todo | **Add a backend lockfile** (`uv.lock` / pinned `requirements.txt`) | Single biggest supply-chain fix; makes builds reproducible. |
| D2 | 🔴 | todo | Raise vulnerable floors | `cryptography>=49` (private-key leak / buffer overflow), `gitpython>=3.1.49` (path-traversal + RCE), `asyncssh>=2.23.1` (SSH auth path-traversal — directly relevant). |
| D3 | 🔴 | todo | Force `starlette>=1.0.1` + `python-multipart>=0.0.30` | Via `fastapi>=0.138`. "BadHost" advisory is a critical path-auth-bypass; LabDog uses path-based auth middleware. |
| D4 | 🟠 | todo | Audit the **Redis server** version in deploy | Client `redis-py` is fine; server had 2026 RCE advisories. |
| D5 | 🟠 | todo | `fastapi-users` → 15.x; refresh `ansible-core` off near-EOL 2.16 branch | Maintenance-only / EOL drift. |
| D6 | 🟠 | todo | Frontend: migrate **ESLint 9 → 10** before ~2026-08-06 EOL | Time-boxed, low urgency. |
| D7 | 🟢 | todo | Frontend minor bumps | `lucide-react` 0.577 → 1.x is breaking (brand icons removed) — plan it; react-query / tailwind / zod / react-hook-form minor bumps are safe. |

---

## Dead Code / Cleanup

| ID | Status | Finding | Location |
|----|--------|---------|----------|
| C1 | todo | **Stale duplicate source tree** from a prior `setup.py build` — not imported, but pollutes greps. Safe to delete. | `backend/build/lib/app/` |
| C2 | todo | **Duplicated `cronToHuman`** — inline copy duplicates the exported one; will drift. | `frontend/app/(dashboard)/hosts/[id]/client-page.tsx:67` vs `frontend/lib/cron.ts` |
| C3 | todo | Duplicated enum-coercion (`_firewall_backend_str` pattern) across orchestrator/diff/converter — centralize. | `backend/app/tasks/host_sync_orchestrator.py:100-111`, `sync/diff.py:99-103/155-159`, `rules/converter.py:10-12` |
| C4 | todo | `formatPorts` possibly unused in client-page; redundant double `close()` in terminal hook. | `frontend/app/(dashboard)/hosts/[id]/client-page.tsx:59-65`, `frontend/hooks/use-terminal-websocket.ts:68-73` |

---

## Verified Clean (scope boundary)

No SQL injection (all `text()` parameterized); AES-256-GCM crypto with AAD + 32-byte key validation; keys
written only to `/dev/shm` 0600 with cleanup; HMAC-verified webhooks; JWT algorithm pinned; nft/iptables
content written as data (not shell) and validated with `nft -c`; cron via parameterized Ansible module;
`safe_load` YAML, no pickle, no untrusted archive extraction; SPA path-traversal guarded; no committed
secrets; no `dangerouslySetInnerHTML`; auth via httpOnly cookie + double-submit CSRF; all frontend deps used.

---

## Suggested batching for fixes

1. **SSH host-key verification** — S1 + S3 together (one systemic fix: route SSH through `ssh_connect_host`).
2. **Authz + SSRF** — S2, S4, S5.
3. **Correctness** — B1 (hold drift), B2 (unmount leak), then B3/B4.
4. **Dependencies** — D1 lockfile + D2/D3 floor bumps.
5. **Cleanup** — C1–C4 (low-risk, quick).
