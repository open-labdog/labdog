"""Shared helpers for the Grafana integration, usable from both the API
(async) and — via a sync variant — the action-dispatch layer."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.grafana.models import GrafanaInstance


async def get_default_instance(db: AsyncSession) -> GrafanaInstance | None:
    """Return the default Grafana instance, or the sole instance if exactly
    one is registered, else ``None``."""
    result = await db.execute(select(GrafanaInstance).where(GrafanaInstance.is_default.is_(True)))
    inst = result.scalars().first()
    if inst is not None:
        return inst
    # No explicit default — fall back to the sole instance, if there's one.
    all_rows = (await db.execute(select(GrafanaInstance))).scalars().all()
    return all_rows[0] if len(all_rows) == 1 else None


async def build_metrics_extra_vars(
    host_id: int,
    hostname: str,
    metrics_backend: dict[str, str] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build the extra-vars LabDog injects into a per-host action run.

    Returns ``(url_vars, identity_vars)``:

    * ``identity_vars`` — ``labdog_host_id`` / ``labdog_hostname``, always
      set so the agent stamps queryable labels. These should override any
      operator input (identity is LabDog-owned).
    * ``url_vars`` — the default Grafana instance's push URLs / org-id mapped
      onto the action's declared var names. Empty unless ``metrics_backend``
      is declared AND a default instance exists. These should NOT override
      operator-supplied values.
    """
    identity_vars = {"labdog_host_id": str(host_id), "labdog_hostname": hostname}
    url_vars: dict[str, str] = {}
    if not metrics_backend:
        return url_vars, identity_vars

    from app.db import task_session

    async with task_session() as db:
        inst = await get_default_instance(db)
        if inst is None:
            return url_vars, identity_vars
        push = inst.prometheus_push_url
        loki = inst.loki_push_url
        org = inst.org_id

    if metrics_backend.get("prometheus_push_var") and push:
        url_vars[metrics_backend["prometheus_push_var"]] = push
    if metrics_backend.get("loki_push_var") and loki:
        url_vars[metrics_backend["loki_push_var"]] = loki
    if metrics_backend.get("org_id_var") and org:
        url_vars[metrics_backend["org_id_var"]] = org
    return url_vars, identity_vars
