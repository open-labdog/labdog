"""Query Loki for logs.

Cost note, because it drives the design of this tool. Reading logs
through Loki and reading them over SSH are not simply cheaper/dearer than
one another — it depends on the shape of the question:

* **Aggregates** (how many errors, which service is noisiest, did the
  rate change) are dramatically cheaper here. ``count_over_time`` makes
  Loki do the counting and returns a handful of numbers; the SSH
  equivalent ships thousands of log lines into the context window so the
  model can count them itself.
* **Cross-host questions** are cheaper here too: one query spans the
  fleet, where SSH needs one connection, one tool call, and one full
  context replay per host.
* **"Show me the actual error text"** is a wash. ``journalctl -n 50`` is
  bounded by construction, which is why the SSH tool stays useful.

The failure mode this tool has to defend against is an unbounded stream
selector — ``{job="nginx"}`` over an hour can be a million lines. Hence a
hard line cap, a default time window, and a description that steers the
model to aggregate first and drill down second.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ai.redaction import redact
from app.ai.tools.base import ToolContext, ToolResult, tool
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.grafana.client import PrometheusClient, PrometheusError
from app.grafana.schemas import derive_query_url
from app.grafana.service import get_default_instance

logger = logging.getLogger(__name__)

# Hard ceiling on log lines returned to the model, whatever it asked for.
MAX_LINES = 200

# Characters of log text; a few hundred very long lines would blow the
# context window even under the line cap.
MAX_CHARS = 12_000

MAX_SERIES = 25

DEFAULT_WINDOW_MINUTES = 60


async def _client_for(ctx: ToolContext, kind: str) -> PrometheusClient | None:
    instance = await get_default_instance(ctx.db, kind)
    if instance is None:
        return None
    token: str | None = None
    if instance.encrypted_token:
        try:
            token = decrypt_ssh_key(instance.encrypted_token, get_master_key())
        except Exception:
            logger.warning("ai: could not decrypt %s token for instance %s", kind, instance.id)
    return PrometheusClient(
        query_url=derive_query_url(instance.url, instance.kind),
        org_id=instance.org_id,
        token=token,
        verify_ssl=instance.verify_ssl,
        ca_cert_pem=instance.ca_cert_pem,
        auth_type=instance.auth_type,
        username=instance.username,
    )


@tool(
    name="query_loki",
    description=(
        "Run a LogQL query against the Loki instance registered in LabDog. "
        "Prefer an aggregate first — for example "
        'sum by (level) (count_over_time({job="nginx"} [1h])) — to find out '
        "which service or level is interesting, then drill into raw lines "
        "with a narrow selector and a filter. A bare stream selector over a "
        "wide window returns an enormous number of lines, is expensive, and "
        "will be truncated. Always include a label selector in braces."
    ),
    parameters={
        "type": "object",
        "properties": {
            "logql": {
                "type": "string",
                "description": (
                    'LogQL expression, e.g. {job="nginx"} |= "error" or '
                    'count_over_time({job="nginx"}[5m])'
                ),
            },
            "minutes": {
                "type": "integer",
                "description": (
                    f"How far back to look, in minutes. Defaults to {DEFAULT_WINDOW_MINUTES}."
                ),
            },
            "limit": {
                "type": "integer",
                "description": f"Maximum log lines to return (capped at {MAX_LINES}).",
            },
        },
        "required": ["logql"],
        "additionalProperties": False,
    },
    classification="read_only",
)
async def _query_loki(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    logql = (args.get("logql") or "").strip()
    if not logql:
        return ToolResult("logql must not be empty.", ok=False)
    if "{" not in logql:
        return ToolResult(
            'LogQL needs a stream selector in braces, e.g. {job="nginx"}. '
            "A query without one would scan every stream.",
            ok=False,
            summary="missing selector",
        )

    minutes = args.get("minutes") or DEFAULT_WINDOW_MINUTES
    try:
        minutes = max(1, min(int(minutes), 60 * 24 * 7))
    except (TypeError, ValueError):
        minutes = DEFAULT_WINDOW_MINUTES

    limit = args.get("limit") or MAX_LINES
    try:
        limit = max(1, min(int(limit), MAX_LINES))
    except (TypeError, ValueError):
        limit = MAX_LINES

    client = await _client_for(ctx, "loki")
    if client is None:
        return ToolResult(
            "No default Loki instance is registered in LabDog, so logs are "
            "unavailable through this tool. Read them on the host instead, "
            "with a bounded journalctl command.",
            ok=False,
            summary="no loki instance",
        )

    end = datetime.now(UTC)
    start = end - timedelta(minutes=minutes)

    try:
        result_type, result = await client.query_loki_range(logql, start, end, limit=limit)
    except PrometheusError as exc:
        return ToolResult(f"The log query failed: {exc}", ok=False, summary="query failed")

    if not result:
        return ToolResult(
            f"No log lines matched in the last {minutes} minutes.\n\nquery: {logql}",
            summary="0 results",
        )

    # A metric query — Loki already did the aggregation, so this is the
    # cheap shape and needs only a compact rendering.
    if result_type == "matrix":
        lines = []
        for series in result[:MAX_SERIES]:
            labels = ",".join(f"{k}={v}" for k, v in sorted(series.get("metric", {}).items()))
            values = series.get("values") or []
            last = values[-1][1] if values else "?"
            peak = max((float(v[1]) for v in values), default=0.0)
            lines.append(f"{{{labels}}} latest={last} peak={peak:g} points={len(values)}")
        body = "\n".join(lines)
        if len(result) > MAX_SERIES:
            body += f"\n\n... and {len(result) - MAX_SERIES} more series"
        return ToolResult(body, summary=f"{len(result)} series (aggregate)")

    # A stream query — this is the expensive shape, so it is bounded twice:
    # once by the line cap and again by total characters.
    rendered: list[str] = []
    total_lines = 0
    for stream in result:
        labels = ",".join(f"{k}={v}" for k, v in sorted(stream.get("stream", {}).items()))
        rendered.append(f"--- {{{labels}}} ---")
        for ts_ns, line in stream.get("values", []):
            when = datetime.fromtimestamp(int(ts_ns) / 1_000_000_000, UTC).isoformat()
            rendered.append(f"{when} {line}")
            total_lines += 1

    body = redact("\n".join(rendered))
    truncated = False
    if len(body) > MAX_CHARS:
        body = body[:MAX_CHARS]
        truncated = True

    if truncated or total_lines >= limit:
        body += (
            f"\n\n... output truncated at {total_lines} lines. Narrow the "
            f"selector, add a filter, shorten the window, or aggregate with "
            f"count_over_time instead of listing lines."
        )

    return ToolResult(body, summary=f"{total_lines} log lines")


QUERY_LOKI = _query_loki
