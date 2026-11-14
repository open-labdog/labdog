# C-038: Assistant usage: plan quota and session limits

| | |
|---|---|
| Edition | community |
| Source | roadmap, board item "Assistant usage: plan quota in the usage panel, and session limits" |
| Tier / score | P3 / 3.0 |
| Stage | parked |
| Effort | M |
| Autonomy | B |
| Depends on | none |

## Goal
The usage panel shows the last known plan quota per window for providers billed by plan and not by token, and an operator sees before sending a message that a session is close to its limit and can give that one session more room.

## Why it is parked
The board rates it P3. A refused run already names the window and its reset time, and a session that has hit a limit already says why, so nothing is unsafe or silent. What remains is making the numbers visible earlier.

## What would un-park it
- Free capacity in a stage that already changes the assistant's provider or usage code.
- A report that operators are surprised by a quota stop or a session limit.

## Context
- Quota half (design decided on the board, 2026-09-11): add `ai_providers.rate_limit_state` (JSONB keyed by `rate_limit_type`; each entry holds `status`, `utilization`, `resets_at`, `seen_at`, `source`). The column does not exist yet; the latest migration is `backend/alembic/versions/0060_schedule_owner.py`.
- Writers: the `RateLimitEvent` branch in `backend/app/ai/agent_sdk/runner.py` (line 958) and `_rate_limit_hit` (line 1065), which reads `utilization` (line 1099) and never stores it; and the provider Test probe. The board says to reassign the dict, not mutate it, or SQLAlchemy will not flush it.
- The window-label map `_RATE_LIMIT_WINDOWS` (runner.py line 101, used by `_rate_limit_window` at line 144) should move to a shared module.
- Reader: `get_usage` in `backend/app/api/ai.py` (line 1155, `GET /api/ai/usage`, model `AIUsageSummary`) gains a `quotas` list for `claude_agent` providers. The panel shows one bar per window with `seen_at` and `source`, because the figure is stale by construction.
- Session half: the cap is `max_tokens_total` in `backend/app/ai/loop.py` (lines 193, 201, 254, 374) and `runner.py` (line 1131), read from the instance setting `ai.max_tokens_total`. There is no per-session allowance. BUG-149 in `BUGS.md` (a session that hits a cap) is nearby.

## Assumptions
- Assumption (not yet confirmed): the two halves split into two items when promoted; the quota half is ready to build as decided, the session half needs a design.
- Assumption (not yet confirmed): a per-session allowance is a nullable column on the session row, raised by a user with the permission that already lets them change `ai.max_tokens_total`. Question for Dennis: who may grant it?
- Assumption (not yet confirmed): the quota list covers `claude_agent` providers only, as the board says. Under [D-04](../DECISIONS.md) that is the operator's own Claude Code sign-in; this item adds no token field.
