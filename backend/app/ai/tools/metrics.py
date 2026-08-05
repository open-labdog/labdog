"""Query the Mimir/Prometheus backend LabDog already knows about.

Reuses the registered :class:`app.grafana.models.GrafanaInstance` and its
client, so the model queries metrics through the same credentials and TLS
settings the rest of LabDog uses — there is no second endpoint to
configure and no second place for a token to live.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai.tools.base import ToolContext, ToolResult, tool
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.grafana.client import PrometheusClient, PrometheusError
from app.grafana.schemas import derive_query_url
from app.grafana.service import get_default_instance

logger = logging.getLogger(__name__)

# A PromQL result set can be enormous; the model needs a readable sample,
# not the whole series list.
MAX_SERIES = 25


@tool(
    name="query_mimir",
    description=(
        "Run an instant PromQL query against the Mimir/Prometheus backend "
        "registered in LabDog and return the current value of each matching "
        "series. Host metrics carry a labdog_host_id label matching the ids "
        "from list_hosts. Use this for resource usage, uptime, and any alerting "
        "signal, rather than shelling into the host."
    ),
    parameters={
        "type": "object",
        "properties": {
            "promql": {
                "type": "string",
                "description": 'The PromQL expression, e.g. up{labdog_host_id="3"}',
            }
        },
        "required": ["promql"],
        "additionalProperties": False,
    },
    classification="read_only",
)
async def _query_mimir(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    promql = (args.get("promql") or "").strip()
    if not promql:
        return ToolResult("promql must not be empty.", ok=False)

    instance = await get_default_instance(ctx.db, "mimir")
    if instance is None:
        return ToolResult(
            "No default Mimir instance is registered in LabDog, so metrics are "
            "unavailable. Fall back to reading state on the host directly.",
            ok=False,
            summary="no mimir instance",
        )

    token: str | None = None
    if instance.encrypted_token:
        try:
            token = decrypt_ssh_key(instance.encrypted_token, get_master_key())
        except Exception:
            logger.warning("ai: could not decrypt Mimir token for instance %s", instance.id)

    client = PrometheusClient(
        query_url=derive_query_url(instance.url, instance.kind),
        org_id=instance.org_id,
        token=token,
        verify_ssl=instance.verify_ssl,
        ca_cert_pem=instance.ca_cert_pem,
        auth_type=instance.auth_type,
        username=instance.username,
    )

    try:
        series = await client.query(promql)
    except PrometheusError as exc:
        return ToolResult(f"The metrics query failed: {exc}", ok=False, summary="query failed")

    if not series:
        return ToolResult(
            f"The query returned no series. Either nothing matches those labels, "
            f"or the metric is not being scraped.\n\nquery: {promql}",
            summary="0 series",
        )

    lines = []
    for entry in series[:MAX_SERIES]:
        labels = entry.get("metric", {})
        name = labels.pop("__name__", "value")
        label_text = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        value = (entry.get("value") or [None, "?"])[1]
        lines.append(f"{name}{{{label_text}}} = {value}")

    body = "\n".join(lines)
    if len(series) > MAX_SERIES:
        body += f"\n\n... and {len(series) - MAX_SERIES} more series (narrow the query)"

    return ToolResult(body, summary=f"{len(series)} series")


@tool(
    name="query_mimir_range",
    description=(
        "Run a PromQL query over a time window and return how each series "
        "moved: first and last value, minimum, maximum, and the number of "
        "sample points. Use this for 'when did this change', 'is it "
        "trending', or 'was it already like this before the upgrade' — "
        "questions an instant query cannot answer. Only the summary is "
        "returned, not every sample, so a wide window is inexpensive."
    ),
    parameters={
        "type": "object",
        "properties": {
            "promql": {"type": "string", "description": "The PromQL expression."},
            "minutes": {
                "type": "integer",
                "description": "How far back to look, in minutes. Defaults to 60.",
            },
            "step": {
                "type": "string",
                "description": "Sample interval, e.g. 60s or 5m. Defaults to 60s.",
            },
        },
        "required": ["promql"],
        "additionalProperties": False,
    },
    classification="read_only",
)
async def _query_mimir_range(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from datetime import UTC, datetime, timedelta

    promql = (args.get("promql") or "").strip()
    if not promql:
        return ToolResult("promql must not be empty.", ok=False)

    try:
        minutes = max(1, min(int(args.get("minutes") or 60), 60 * 24 * 30))
    except (TypeError, ValueError):
        minutes = 60
    step = str(args.get("step") or "60s")

    instance = await get_default_instance(ctx.db, "mimir")
    if instance is None:
        return ToolResult(
            "No default Mimir instance is registered in LabDog, so metrics are unavailable.",
            ok=False,
            summary="no mimir instance",
        )

    token: str | None = None
    if instance.encrypted_token:
        try:
            token = decrypt_ssh_key(instance.encrypted_token, get_master_key())
        except Exception:
            logger.warning("ai: could not decrypt Mimir token for instance %s", instance.id)

    client = PrometheusClient(
        query_url=derive_query_url(instance.url, instance.kind),
        org_id=instance.org_id,
        token=token,
        verify_ssl=instance.verify_ssl,
        ca_cert_pem=instance.ca_cert_pem,
        auth_type=instance.auth_type,
        username=instance.username,
    )

    end = datetime.now(UTC)
    start = end - timedelta(minutes=minutes)
    try:
        series = await client.query_range(promql, start, end, step=step)
    except PrometheusError as exc:
        return ToolResult(f"The metrics query failed: {exc}", ok=False, summary="query failed")

    if not series:
        return ToolResult(
            f"The query returned no series over the last {minutes} minutes.\n\nquery: {promql}",
            summary="0 series",
        )

    # Summarised rather than dumped: every sample of a 24h window at 60s
    # is 1440 numbers per series, which would cost far more than it tells.
    lines = []
    for entry in series[:MAX_SERIES]:
        labels = dict(entry.get("metric", {}))
        name = labels.pop("__name__", "value")
        label_text = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        points = entry.get("values") or []
        numbers = []
        for _ts, raw in points:
            try:
                numbers.append(float(raw))
            except (TypeError, ValueError):
                continue
        if not numbers:
            continue
        lines.append(
            f"{name}{{{label_text}}} first={numbers[0]:g} last={numbers[-1]:g} "
            f"min={min(numbers):g} max={max(numbers):g} points={len(numbers)}"
        )

    body = "\n".join(lines) or "Series matched but carried no numeric samples."
    if len(series) > MAX_SERIES:
        body += f"\n\n... and {len(series) - MAX_SERIES} more series (narrow the query)"
    return ToolResult(body, summary=f"{len(series)} series over {minutes}m")


QUERY_MIMIR = _query_mimir
QUERY_MIMIR_RANGE = _query_mimir_range
