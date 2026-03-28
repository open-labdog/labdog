"""Collect current firewall state from remote hosts via SSH."""

import asyncssh
from app.rules.model import FirewallRuleSpec
from app.ssh_utils import ssh_connect
from app.sync.parsers.nftables import parse_nftables_json
from app.sync.parsers.iptables import parse_iptables_save

# Commands per firewall backend
_COMMANDS = {
    "nftables": "sudo /usr/sbin/nft -j list ruleset",
    "iptables": "sudo iptables-save",
}

_PARSERS = {
    "nftables": parse_nftables_json,
    "iptables": parse_iptables_save,
}


async def collect_current_rules(
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    firewall_backend: str,
    ssh_user: str = "root",
) -> list[FirewallRuleSpec]:
    """SSH into a host and collect its current firewall rules.

    Args:
        host_ip: Target host IP address
        ssh_port: SSH port
        private_key_pem: Decrypted PEM-encoded private key
        firewall_backend: One of "nftables", "iptables"
        ssh_user: SSH username (default: root)

    Returns:
        List of parsed firewall rules currently active on the host.

    Raises:
        ValueError: If firewall_backend is "unknown" or unsupported
        asyncssh.Error: If SSH connection fails
    """
    if firewall_backend not in _COMMANDS:
        raise ValueError(f"Unsupported firewall backend: {firewall_backend}")

    command = _COMMANDS[firewall_backend]
    parser = _PARSERS[firewall_backend]

    key = asyncssh.import_private_key(private_key_pem)
    async with ssh_connect(
        host_ip,
        port=ssh_port,
        username=ssh_user,
        client_keys=[key],
    ) as conn:
        result = await conn.run(command, check=True)
        return parser(result.stdout)
