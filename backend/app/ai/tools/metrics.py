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
                "description": "The PromQL expression, e.g. up{labdog_host_id=\"3\"}",
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
        return ToolResult(
            f"The metrics query failed: {exc}", ok=False, summary="query failed"
        )

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


QUERY_MIMIR = _query_mimir
