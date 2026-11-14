"""Shared helpers for the Grafana integration, usable from both the API
(async) and the action-dispatch layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


async def get_default_client(db: AsyncSession, kind: str = "mimir"):
    """A query client for the default instance of ``kind``, or ``None``."""
    instance = await get_default_instance(db, kind)
    return client_for_instance(instance) if instance is not None else None


def client_for_instance(instance: GrafanaInstance):
    """A query client for one registered instance.

    Building one from a row means decrypting the token, deriving the
    query URL, and carrying four TLS/auth fields across, so every caller
    goes through here.

    A token that will not decrypt is logged and dropped rather than
    raised: an unauthenticated query against a backend that does not
    require auth still works, and failing the whole poll because of it
    would turn a misconfiguration into an outage.
    """
    from app.grafana.client import PrometheusClient
    from app.grafana.schemas import derive_query_url

    token: str | None = None
    if instance.encrypted_token:
        try:
            from app.crypto import get_master_key
            from app.crypto.encryption import decrypt_ssh_key

            token = decrypt_ssh_key(instance.encrypted_token, get_master_key())
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "grafana: could not decrypt the token for instance %s; querying without it",
                instance.id,
            )

    return PrometheusClient(
        query_url=derive_query_url(instance.url, instance.kind),
        org_id=instance.org_id,
        token=token,
        verify_ssl=instance.verify_ssl,
        ca_cert_pem=instance.ca_cert_pem,
        auth_type=instance.auth_type,
        username=instance.username,
    )


# ---------------------------------------------------------------------------
# Per-host destinations
# ---------------------------------------------------------------------------

#: Instance kinds a host or group can be assigned, and the column on
#: ``hosts`` / ``host_groups`` that holds each assignment.
DESTINATION_KINDS = ("mimir", "loki")


def _column(kind: str) -> str:
    if kind not in DESTINATION_KINDS:
        raise ValueError(f"unknown destination kind {kind!r}")
    return f"{kind}_instance_id"


@dataclass(frozen=True)
class ResolvedDestination:
    """The instance a host ships ``kind`` data to, and why.

    ``source`` is ``"host"``, ``"group"`` or ``"default"``; ``group_id`` /
    ``group_name`` are set when it is ``"group"``. ``instance`` is ``None``
    when nothing resolves (no assignment and no default).
    """

    instance: GrafanaInstance | None
    source: str
    group_id: int | None = None
    group_name: str | None = None


async def resolve_host_instance(db: AsyncSession, host_id: int, kind: str) -> ResolvedDestination:
    """Where a host's Alloy ships ``kind`` (``mimir`` / ``loki``) data.

    Same precedence as every other merged module: the host's own
    assignment, then the highest-priority group that sets one (ties by
    group id), then the default instance. Every host-scoped consumer —
    the action dispatch, the host page, the AI tools — asks this, so they
    agree on where a host's data lives.
    """
    from app.models.host import Host, HostGroupMembership
    from app.models.host_group import HostGroup

    column = _column(kind)
    host_choice = await db.scalar(select(getattr(Host, column)).where(Host.id == host_id))
    if host_choice is not None:
        return ResolvedDestination(await db.get(GrafanaInstance, host_choice), "host")

    group_col = getattr(HostGroup, column)
    row = (
        await db.execute(
            select(HostGroup.id, HostGroup.name, group_col)
            .join(HostGroupMembership, HostGroupMembership.c.group_id == HostGroup.id)
            .where(HostGroupMembership.c.host_id == host_id, group_col.is_not(None))
            .order_by(HostGroup.priority.desc(), HostGroup.id)
            .limit(1)
        )
    ).first()
    if row is not None:
        group_id, group_name, instance_id = row
        return ResolvedDestination(
            await db.get(GrafanaInstance, instance_id), "group", group_id, group_name
        )

    return ResolvedDestination(await get_default_instance(db, kind), "default")


async def resolve_all_hosts(db: AsyncSession, kind: str) -> dict[int, int | None]:
    """``{host_id: instance_id}`` for every host, by the same precedence as
    :func:`resolve_host_instance`, in three queries rather than one per
    host. ``None`` means the host resolves to nothing."""
    from app.models.host import Host, HostGroupMembership
    from app.models.host_group import HostGroup

    column = _column(kind)
    hosts = dict((await db.execute(select(Host.id, getattr(Host, column)))).tuples().all())

    group_col = getattr(HostGroup, column)
    group_rows = (
        await db.execute(
            select(HostGroupMembership.c.host_id, group_col)
            .join(HostGroup, HostGroupMembership.c.group_id == HostGroup.id)
            .where(group_col.is_not(None))
            .order_by(HostGroup.priority.desc(), HostGroup.id)
        )
    ).tuples()
    from_group: dict[int, int] = {}
    for host_id, instance_id in group_rows:
        from_group.setdefault(host_id, instance_id)

    default = await get_default_instance(db, kind)
    default_id = default.id if default is not None else None
    return {
        host_id: own if own is not None else from_group.get(host_id, default_id)
        for host_id, own in hosts.items()
    }


async def instance_for_query(
    db: AsyncSession, kind: str, host_id: int | None
) -> GrafanaInstance | None:
    """The instance to query for ``kind``: the one ``host_id`` ships to, or
    the default when no host is named."""
    if host_id is None:
        return await get_default_instance(db, kind)
    return (await resolve_host_instance(db, host_id, kind)).instance


async def instance_in_use(db: AsyncSession, instance_id: int) -> list[str]:
    """Names of the hosts and groups assigned ``instance_id``, for refusing
    to delete an instance something still ships to."""
    from sqlalchemy import or_

    from app.models.host import Host
    from app.models.host_group import HostGroup

    names: list[str] = []
    for model, label in ((Host, "host"), (HostGroup, "group")):
        name_col = model.hostname if model is Host else model.name
        rows = await db.scalars(
            select(name_col).where(
                or_(model.mimir_instance_id == instance_id, model.loki_instance_id == instance_id)
            )
        )
        names.extend(f"{label} {n}" for n in rows)
    return names


#: Reserved key, inside ``ActionRun.parameters`` / ``ScheduledAction.parameters``,
#: carrying the destinations the operator picked in the run dialog:
#: ``{"mimir": <instance id>, "loki": <instance id>}``. Like ``__dry_run`` it
#: is not an action parameter: the API sets it from the request body's
#: ``metrics_destinations`` and strips any inbound copy. A pick applies to
#: every host in the run and overrides their assignments; a kind left out
#: falls back to each host's own resolved destination.
METRICS_DESTINATIONS_PARAM = "__metrics_destinations"


async def _instance_for_url(
    db: AsyncSession, kind: str, url: str, preferred: GrafanaInstance | None
) -> GrafanaInstance | None:
    """The registered instance an operator-typed URL refers to, if any.

    A run started without ``metrics_destinations`` (an API caller, or a
    schedule saved before it existed) names its destination only by URL.
    Prefer the host's own resolved instance when it has that URL, so two
    destinations sharing a URL with different tenants keep each host's
    tenant; then the default; then the lowest id.
    """
    if preferred is not None and preferred.url == url:
        return preferred
    rows = (
        (
            await db.execute(
                select(GrafanaInstance)
                .where(GrafanaInstance.kind == kind, GrafanaInstance.url == url)
                .order_by(GrafanaInstance.is_default.desc(), GrafanaInstance.id)
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if rows else None


async def build_metrics_extra_vars(
    host_id: int,
    hostname: str,
    metrics_backend: dict[str, str] | None,
    operator_params: dict | None = None,
    picked: dict[str, int] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build the extra-vars unbork injects into a per-host action run.

    Returns ``(destination_vars, identity_vars)``:

    * ``identity_vars``: ``labdog_host_id`` / ``labdog_hostname``, always
      set so the agent stamps queryable labels. These should override any
      operator input (identity is unbork-owned).
    * ``destination_vars``: for each of Mimir and Loki, the URL, tenant and
      TLS-verification flag of the instance this host ships to, mapped onto
      the action's declared var names. Empty unless ``metrics_backend`` is
      declared. These should NOT override operator-supplied values.

    Which instance, per kind: the one the operator ``picked`` for the run;
    else, when the operator typed a URL param, the registered instance
    with that URL (its tenant and TLS settings travel with it; an
    unregistered URL gets none); else the host's resolved destination.
    """
    identity_vars = {"labdog_host_id": str(host_id), "labdog_hostname": hostname}
    out: dict[str, Any] = {}
    if not metrics_backend:
        return out, identity_vars

    operator_params = operator_params or {}
    picked = picked or {}

    from app.db import task_session

    var_names = {
        "mimir": (
            metrics_backend.get("prometheus_push_var"),
            metrics_backend.get("org_id_var"),
            metrics_backend.get("prometheus_tls_skip_verify_var"),
        ),
        "loki": (
            metrics_backend.get("loki_push_var"),
            metrics_backend.get("loki_tenant_var"),
            metrics_backend.get("loki_tls_skip_verify_var"),
        ),
    }

    async with task_session() as db:
        for kind, (url_var, tenant_var, tls_var) in var_names.items():
            if not (url_var or tenant_var or tls_var):
                continue
            resolved = (await resolve_host_instance(db, host_id, kind)).instance
            typed_url = operator_params.get(url_var) if url_var else None
            if picked.get(kind) is not None:
                instance = await db.get(GrafanaInstance, picked[kind])
                if instance is None or instance.kind != kind:
                    raise ValueError(f"picked {kind} destination {picked[kind]} no longer exists")
            elif typed_url:
                instance = await _instance_for_url(db, kind, typed_url, resolved)
            else:
                instance = resolved
            if instance is None:
                continue
            if url_var:
                out[url_var] = instance.url
            if tenant_var and instance.org_id:
                out[tenant_var] = instance.org_id
            if tls_var:
                out[tls_var] = not instance.verify_ssl
    return out, identity_vars
