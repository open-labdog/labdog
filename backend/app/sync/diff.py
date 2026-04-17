import logging
from dataclasses import dataclass, field

from app.rules.model import ChainPolicies, FirewallRuleSpec

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
    policy_changes: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return bool(self.rules_to_add or self.rules_to_remove or self.policy_changes)

    def summary(self) -> dict:
        return {
            "add": len(self.rules_to_add),
            "remove": len(self.rules_to_remove),
            "unchanged": len(self.rules_unchanged),
            "policy_changes": self.policy_changes,
            "has_changes": self.has_changes,
        }


def compute_diff(
    current: list[FirewallRuleSpec],
    desired: list[FirewallRuleSpec],
    current_policies: ChainPolicies | None = None,
    desired_policies: ChainPolicies | None = None,
) -> RulesetDiff:
    """
    Compare current (on host) vs desired (from DB) rules and policies.
    Uses matches() for comparison (ignores comments, priority, IDs).
    """
    diff = RulesetDiff()

    # Build hash sets for O(N) comparison instead of O(N²)
    current_keys = {r._match_key() for r in current}
    desired_keys = {r._match_key() for r in desired}

    # Rules in desired but not in current → to add
    for d_rule in desired:
        if d_rule._match_key() in current_keys:
            diff.rules_unchanged.append(d_rule)
        else:
            diff.rules_to_add.append(d_rule)

    # Rules in current but not in desired → to remove
    for c_rule in current:
        if c_rule._match_key() not in desired_keys:
            diff.rules_to_remove.append(c_rule)

    # Compare chain policies
    if current_policies and desired_policies:
        if current_policies.input != desired_policies.input:
            diff.policy_changes["input"] = (current_policies.input, desired_policies.input)
        if current_policies.output != desired_policies.output:
            diff.policy_changes["output"] = (current_policies.output, desired_policies.output)

    return diff


async def fetch_current_firewall_state(host_id: int, db):
    """Fetch current firewall rules and policies from a host via SSH.

    Returns a CollectedFirewallState (rules + policies), or a default
    state if the host has no SSH key or unknown backend.
    """
    from sqlalchemy import select

    from app.crypto import decrypt_ssh_key, get_master_key
    from app.models.host import Host
    from app.models.ssh_key import SSHKey
    from app.rules.model import ChainPolicies
    from app.sync.collector import CollectedFirewallState, collect_firewall_state

    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        return CollectedFirewallState(rules=[], policies=ChainPolicies())

    backend = (
        host.firewall_backend.value
        if hasattr(host.firewall_backend, "value")
        else host.firewall_backend
    )
    if backend == "unknown" or not host.ssh_key_id:
        return CollectedFirewallState(rules=[], policies=ChainPolicies())

    key_result = await db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ssh_key = key_result.scalar_one_or_none()
    if not ssh_key:
        return CollectedFirewallState(rules=[], policies=ChainPolicies())

    master_key = get_master_key()
    private_key_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, master_key)

    try:
        return await collect_firewall_state(
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


async def fetch_current_state(host_id: int, db) -> list[FirewallRuleSpec]:
    """Fetch current firewall rules from a host via SSH.

    Looks up host details and SSH key from DB, decrypts the key,
    SSHes into the host, and parses the current firewall config.

    Returns empty list if:
    - Host has no SSH key assigned
    - Host firewall backend is "unknown"
    """
    from sqlalchemy import select

    from app.crypto import decrypt_ssh_key, get_master_key
    from app.models.host import Host
    from app.models.ssh_key import SSHKey
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
