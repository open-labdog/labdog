"""Tests for the dual-stack inactive-backend teardown in the firewall playbook.

On a host with both nft and iptables installed, LabDog manages one backend and
every sync must strip its own footprint from the *other* backend so collection
has a single source of truth (see app.ansible_runtime.generator).
"""

import yaml

from app.ansible_runtime.generator import generate_playbook
from app.rules.model import ChainPolicies, FirewallRuleSpec

_RULES = [
    FirewallRuleSpec(
        action="allow",
        protocol="tcp",
        direction="input",
        source_cidr="10.10.3.3/32",
        port_start=22,
        comment="ssh",
    )
]


def _tasks(backend: str) -> list[dict]:
    pb = generate_playbook(backend, "10.0.0.1", _RULES, "/tmp/key", policies=ChainPolicies())
    plays = yaml.safe_load(pb)  # must be valid YAML
    return plays[0]["tasks"]


def _task_named(tasks: list[dict], needle: str) -> dict | None:
    return next((t for t in tasks if needle in t["name"]), None)


class TestNftablesActiveTearsDownIptables:
    def test_teardown_task_present(self):
        task = _task_named(_tasks("nftables"), "Remove stale LabDog iptables rules")
        assert task is not None
        assert task["ignore_errors"] is True

    def test_runs_after_revert_is_cancelled(self):
        # The stale store must only be removed once the new ruleset is safely
        # applied and the deadman revert is cancelled.
        names = [t["name"] for t in _tasks("nftables")]
        cancel = next(i for i, n in enumerate(names) if "Cancel automatic revert" in n)
        teardown = next(i for i, n in enumerate(names) if "Remove stale LabDog iptables" in n)
        assert teardown > cancel

    def test_only_touches_labdog_chains(self):
        script = _task_named(_tasks("nftables"), "Remove stale LabDog iptables rules")[
            "ansible.builtin.shell"
        ]
        # Guarded on our own chain existing; only LABDOG-* chains are removed.
        assert "LABDOG-INPUT" in script and "LABDOG-OUTPUT" in script
        # Never flush/delete the base chains.
        assert "-F INPUT" not in script
        assert "-X INPUT" not in script
        # Covers IPv6 too.
        assert "ip6tables" in script


class TestIptablesActiveTearsDownNftables:
    def test_teardown_task_present(self):
        task = _task_named(_tasks("iptables"), "Remove stale LabDog nftables table")
        assert task is not None
        assert task["ignore_errors"] is True

    def test_runs_after_revert_is_cancelled(self):
        names = [t["name"] for t in _tasks("iptables")]
        cancel = next(i for i, n in enumerate(names) if "Cancel automatic revert" in n)
        teardown = next(i for i, n in enumerate(names) if "Remove stale LabDog nftables" in n)
        assert teardown > cancel

    def test_deletes_table_only_when_labdog_owned(self):
        script = _task_named(_tasks("iptables"), "Remove stale LabDog nftables table")[
            "ansible.builtin.shell"
        ]
        # Marker-guarded: only delete inet filter when it is LabDog's.
        assert 'grep -q "Managed by LabDog"' in script
        assert "delete table inet filter" in script
        # Never issues a global flush that would nuke other tables.
        assert "flush ruleset" not in script
