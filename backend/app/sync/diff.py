import asyncio
import logging
from dataclasses import dataclass, field
from app.rules.model import FirewallRuleSpec

logger = logging.getLogger(__name__)


class SSHFetchError(Exception):
    """Raised when SSH connection to a host fails during rule collection."""

    def __init__(self, hostname: str, ip_address: str, detail: str):
        self.hostname = hostname
        self.ip_address = ip_address
        self.detail = detail
        super().__init__(f"SSH to {hostname} ({ip_address}) failed: {detail}")


@dataclass
class RulesetDiff:
    """Result of comparing current vs desired firewall rules."""

    rules_to_add: list[FirewallRuleSpec] = field(default_factory=list)
    rules_to_remove: list[FirewallRuleSpec] = field(default_factory=list)
    rules_unchanged: list[FirewallRuleSpec] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.rules_to_add or self.rules_to_remove)

    def summary(self) -> dict:
        return {
            "add": len(self.rules_to_add),
            "remove": len(self.rules_to_remove),
            "unchanged": len(self.rules_unchanged),
            "has_changes": self.has_changes,
        }


def compute_diff(
    current: list[FirewallRuleSpec],
    desired: list[FirewallRuleSpec],
) -> RulesetDiff:
    """
    Compare current (on host) vs desired (from DB) rules.
    Uses matches() for comparison (ignores comments, priority, IDs).
    """
    diff = RulesetDiff()

    # Find rules in desired but not in current (to add)
    for d_rule in desired:
        found = any(d_rule.matches(c_rule) for c_rule in current)
        if found:
            diff.rules_unchanged.append(d_rule)
        else:
            diff.rules_to_add.append(d_rule)

    # Find rules in current but not in desired (to remove)
    for c_rule in current:
        found = any(c_rule.matches(d_rule) for d_rule in desired)
        if not found:
            diff.rules_to_remove.append(c_rule)

    return diff


async def fetch_current_state(host_id: int, db) -> list[FirewallRuleSpec]:
    """Fetch current firewall rules from a host via SSH.

    Looks up host details and SSH key from DB, decrypts the key,
    SSHes into the host, and parses the current firewall config.

    Returns empty list if:
    - Host has no SSH key assigned
    - Host firewall backend is "unknown"
    """
    from sqlalchemy import select
    from app.models.host import Host
    from app.models.ssh_key import SSHKey
    from app.crypto import decrypt_ssh_key, get_master_key
    from app.sync.collector import collect_current_rules

    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        return []

    backend = (
        host.firewall_backend.value
        if hasattr(host.firewall_backend, "value")
        else host.firewall_backend
    )
    if backend == "unknown" or not host.ssh_key_id:
        return []

    key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ssh_key = key_result.scalar_one_or_none()
    if not ssh_key:
        return []

    master_key = get_master_key()
    private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

    try:
        return await collect_current_rules(
            host_ip=host.ip_address,
            ssh_port=host.ssh_port,
            private_key_pem=private_key_pem,
            firewall_backend=backend,
            ssh_user=ssh_key.ssh_user,
        )
    except Exception as exc:
        logger.warning(
            "Failed to fetch current state from host %s (%s): %s",
            host.hostname,
            host.ip_address,
            exc,
        )
        raise SSHFetchError(host.hostname, host.ip_address, str(exc)) from exc
