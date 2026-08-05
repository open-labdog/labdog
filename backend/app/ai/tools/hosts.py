"""Inventory tools — what hosts exist and what they run.

Both are read-only and both are scoped to the session's target allowlist,
so a session started against one host cannot enumerate the fleet.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.ai.tools.base import ToolContext, ToolResult, tool
from app.models.host import Host


def _scope(ctx: ToolContext):
    stmt = select(Host).order_by(Host.hostname)
    if ctx.target_host_ids:
        stmt = stmt.where(Host.id.in_(ctx.target_host_ids))
    return stmt


@tool(
    name="list_hosts",
    description=(
        "List the hosts this session is allowed to inspect, with their id, "
        "hostname, IP address, and operating system. Call this first to find "
        "the host id you need for other tools."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    classification="read_only",
)
async def _list_hosts(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    hosts = (await ctx.db.execute(_scope(ctx))).scalars().all()
    if not hosts:
        return ToolResult(
            "No hosts are in scope for this session.",
            summary="0 hosts in scope",
        )
    lines = [
        f"- id={h.id} {h.hostname} ({h.ip_address}) "
        f"{h.os_pretty_name or h.os_family or 'unknown OS'}"
        for h in hosts
    ]
    return ToolResult(
        "Hosts in scope:\n" + "\n".join(lines),
        summary=f"{len(hosts)} host(s)",
    )


@tool(
    name="get_host_facts",
    description=(
        "Get the cached operating-system facts LabDog collected for one host: "
        "OS family and version, kernel version, default network interface, and "
        "when those facts were last refreshed. This does not connect to the "
        "host — use run_ssh_command for live state."
    ),
    parameters={
        "type": "object",
        "properties": {"host_id": {"type": "integer", "description": "Host id from list_hosts."}},
        "required": ["host_id"],
        "additionalProperties": False,
    },
    classification="read_only",
)
async def _get_host_facts(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    host_id = args.get("host_id")
    if not isinstance(host_id, int):
        return ToolResult("host_id must be an integer.", ok=False)

    if ctx.target_host_ids and host_id not in ctx.target_host_ids:
        return ToolResult(
            f"Host {host_id} is not in scope for this session.",
            ok=False,
            summary="out of scope",
        )

    host = (await ctx.db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if host is None:
        return ToolResult(f"No host with id {host_id}.", ok=False)

    collected = host.os_facts_collected_at.isoformat() if host.os_facts_collected_at else "never"
    facts = [
        f"hostname: {host.hostname}",
        f"ip_address: {host.ip_address}",
        f"os: {host.os_pretty_name or 'unknown'}",
        f"os_family: {host.os_family or 'unknown'}",
        f"os_codename: {host.os_codename or 'unknown'}",
        f"kernel: {host.kernel_version or 'unknown'}",
        f"default_nic: {host.default_nic or 'unknown'}",
        f"firewall_backend: {host.firewall_backend}",
        f"facts_collected_at: {collected}",
    ]
    return ToolResult(
        "\n".join(facts), target_host_id=host.id, summary=f"facts for {host.hostname}"
    )


LIST_HOSTS = _list_hosts
GET_HOST_FACTS = _get_host_facts
