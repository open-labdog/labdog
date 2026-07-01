"""Collect current firewall state from remote hosts via SSH."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncssh

from app.rules.model import ChainPolicies, FirewallRuleSpec
from app.ssh_utils import ssh_connect_host
from app.sync.parsers.iptables import parse_iptables_policies, parse_iptables_save
from app.sync.parsers.nftables import parse_nftables_json, parse_nftables_policies

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host

# Commands per firewall backend
_COMMANDS = {
    "nftables": "sudo /usr/sbin/nft -j list ruleset",
    "iptables": "sudo iptables-save",
}

_RULE_PARSERS = {
    "nftables": parse_nftables_json,
    "iptables": parse_iptables_save,
}

_POLICY_PARSERS = {
    "nftables": parse_nftables_policies,
    "iptables": parse_iptables_policies,
}


@dataclass
class CollectedFirewallState:
    """Rules and chain policies collected from a host."""

    rules: list[FirewallRuleSpec]
    policies: ChainPolicies


async def collect_firewall_state(
    host: "Host",
    db: "AsyncSession",
    private_key_pem: str,
    firewall_backend: str,
) -> CollectedFirewallState:
    """SSH into a host and collect its current firewall rules and chain policies.

    Connects via ssh_connect_host so the stored host key is verified (TOFU).

    Returns:
        CollectedFirewallState with parsed rules and policies.

    Raises:
        ValueError: If firewall_backend is "unknown" or unsupported
        asyncssh.Error: If SSH connection fails
        HostKeyMismatchError: If the server host key does not match the stored key
    """
    if firewall_backend not in _COMMANDS:
        raise ValueError(f"Unsupported firewall backend: {firewall_backend}")

    command = _COMMANDS[firewall_backend]
    rule_parser = _RULE_PARSERS[firewall_backend]
    policy_parser = _POLICY_PARSERS[firewall_backend]

    key = asyncssh.import_private_key(private_key_pem)
    async with ssh_connect_host(host, db, client_keys=[key]) as conn:
        result = await conn.run(command, check=True)
        return CollectedFirewallState(
            rules=rule_parser(result.stdout),
            policies=policy_parser(result.stdout),
        )


async def collect_current_rules(
    host: "Host",
    db: "AsyncSession",
    private_key_pem: str,
    firewall_backend: str,
) -> list[FirewallRuleSpec]:
    """SSH into a host and collect its current firewall rules.

    Backward-compatible wrapper around collect_firewall_state.
    """
    state = await collect_firewall_state(
        host,
        db,
        private_key_pem,
        firewall_backend,
    )
    return state.rules
