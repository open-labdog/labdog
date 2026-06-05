"""Shared helpers for the Grafana integration, usable from both the API
(async) and the action-dispatch layer."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.grafana.models import GrafanaInstance


async def get_default_instance(db: AsyncSession, kind: str) -> GrafanaInstance | None:
    """Return the default instance of ``kind`` (mimir/loki), or the sole
    instance of that kind if exactly one is registered, else ``None``."""
    result = await db.execute(
        select(GrafanaInstance).where(
            GrafanaInstance.kind == kind, GrafanaInstance.is_default.is_(True)
        )
    )
    inst = result.scalars().first()
    if inst is not None:
        return inst
    rows = (
        (await db.execute(select(GrafanaInstance).where(GrafanaInstance.kind == kind)))
        .scalars()
        .all()
    )
    return rows[0] if len(rows) == 1 else None


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
    * ``url_vars`` — the default **Mimir** instance's URL (and org-id) and
      the default **Loki** instance's URL, mapped onto the action's declared
      var names. Empty unless ``metrics_backend`` is declared AND a matching
      default instance exists. These should NOT override operator-supplied
      values.
    """
    identity_vars = {"labdog_host_id": str(host_id), "labdog_hostname": hostname}
    url_vars: dict[str, str] = {}
    if not metrics_backend:
        return url_vars, identity_vars

    from app.db import task_session

    async with task_session() as db:
        mimir = await get_default_instance(db, "mimir")
        loki = await get_default_instance(db, "loki")
        mimir_url = mimir.url if mimir else None
        mimir_org = mimir.org_id if mimir else None
        loki_url = loki.url if loki else None

    if metrics_backend.get("prometheus_push_var") and mimir_url:
        url_vars[metrics_backend["prometheus_push_var"]] = mimir_url
    if metrics_backend.get("loki_push_var") and loki_url:
        url_vars[metrics_backend["loki_push_var"]] = loki_url
    if metrics_backend.get("org_id_var") and mimir_org:
        url_vars[metrics_backend["org_id_var"]] = mimir_org
    return url_vars, identity_vars
