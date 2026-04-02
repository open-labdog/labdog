"""Collect current firewall state from remote hosts via SSH."""

from dataclasses import dataclass

import asyncssh
from app.rules.model import ChainPolicies, FirewallRuleSpec
from app.ssh_utils import ssh_connect
from app.sync.parsers.nftables import parse_nftables_json, parse_nftables_policies
from app.sync.parsers.iptables import parse_iptables_save, parse_iptables_policies

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
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    firewall_backend: str,
    ssh_user: str = "root",
) -> CollectedFirewallState:
    """SSH into a host and collect its current firewall rules and chain policies.

    Returns:
        CollectedFirewallState with parsed rules and policies.

    Raises:
        ValueError: If firewall_backend is "unknown" or unsupported
        asyncssh.Error: If SSH connection fails
    """
    if firewall_backend not in _COMMANDS:
        raise ValueError(f"Unsupported firewall backend: {firewall_backend}")

    command = _COMMANDS[firewall_backend]
    rule_parser = _RULE_PARSERS[firewall_backend]
    policy_parser = _POLICY_PARSERS[firewall_backend]

    key = asyncssh.import_private_key(private_key_pem)
    async with ssh_connect(
        host_ip,
        port=ssh_port,
        username=ssh_user,
        client_keys=[key],
    ) as conn:
        result = await conn.run(command, check=True)
        return CollectedFirewallState(
            rules=rule_parser(result.stdout),
            policies=policy_parser(result.stdout),
        )


async def collect_current_rules(
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    firewall_backend: str,
    ssh_user: str = "root",
) -> list[FirewallRuleSpec]:
    """SSH into a host and collect its current firewall rules.

    Backward-compatible wrapper around collect_firewall_state.
    """
    state = await collect_firewall_state(
        host_ip, ssh_port, private_key_pem, firewall_backend, ssh_user,
    )
    return state.rules
