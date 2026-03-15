import yaml
from app.rules.model import FirewallRuleSpec
from app.rules.renderers.nftables import render_nftables_config
from app.rules.renderers.firewalld import render_firewalld_tasks
from app.rules.renderers.ufw import render_ufw_rules


def generate_nftables_playbook(
    host_ip: str, rules: list[FirewallRuleSpec], ssh_key_path: str
) -> str:
    """Generate playbook that writes nftables.conf and reloads."""
    nft_config = render_nftables_config(rules)
    tasks = [
        {
            "name": "Write nftables configuration",
            "ansible.builtin.copy": {
                "content": nft_config,
                "dest": "/etc/nftables.conf",
                "owner": "root",
                "group": "root",
                "mode": "0644",
                "validate": "nft -c -f %s",
            },
        },
        {
            "name": "Reload nftables service",
            "ansible.builtin.service": {
                "name": "nftables",
                "state": "reloaded",
                "enabled": True,
            },
        },
    ]
    playbook = [
        {
            "name": "Apply nftables firewall rules",
            "hosts": "target",
            "become": True,
            "gather_facts": False,
            "tasks": tasks,
        }
    ]
    return yaml.dump(playbook, default_flow_style=False, sort_keys=False)


def generate_firewalld_playbook(
    host_ip: str, rules: list[FirewallRuleSpec], ssh_key_path: str
) -> str:
    """Generate playbook with per-rule firewalld tasks."""
    fw_tasks = render_firewalld_tasks(rules)
    tasks = [
        {
            "name": "Gather current firewalld state",
            "ansible.posix.firewalld_info": {"active_zones": True},
            "register": "fw_before",
        },
    ]
    for i, fw_params in enumerate(fw_tasks):
        tasks.append(
            {
                "name": f"Apply firewall rule {i + 1}",
                "ansible.posix.firewalld": fw_params,
            }
        )
    playbook = [
        {
            "name": "Apply firewalld firewall rules",
            "hosts": "target",
            "become": True,
            "gather_facts": False,
            "tasks": tasks,
        }
    ]
    return yaml.dump(playbook, default_flow_style=False, sort_keys=False)


def generate_ufw_playbook(host_ip: str, rules: list[FirewallRuleSpec], ssh_key_path: str) -> str:
    """Generate playbook that writes UFW rules files and reloads."""
    user_rules, user6_rules = render_ufw_rules(rules)
    tasks = [
        {
            "name": "Write UFW user rules (IPv4)",
            "ansible.builtin.copy": {
                "content": user_rules,
                "dest": "/etc/ufw/user.rules",
                "owner": "root",
                "group": "root",
                "mode": "0640",
            },
        },
        {
            "name": "Write UFW user6 rules (IPv6)",
            "ansible.builtin.copy": {
                "content": user6_rules,
                "dest": "/etc/ufw/user6.rules",
                "owner": "root",
                "group": "root",
                "mode": "0640",
            },
        },
        {
            "name": "Reload UFW",
            "ansible.builtin.command": "ufw reload",
            "changed_when": True,
        },
    ]
    playbook = [
        {
            "name": "Apply UFW firewall rules",
            "hosts": "target",
            "become": True,
            "gather_facts": False,
            "tasks": tasks,
        }
    ]
    return yaml.dump(playbook, default_flow_style=False, sort_keys=False)


def generate_playbook(
    backend: str, host_ip: str, rules: list[FirewallRuleSpec], ssh_key_path: str
) -> str:
    """Dispatch to backend-specific generator."""
    generators = {
        "nftables": generate_nftables_playbook,
        "firewalld": generate_firewalld_playbook,
        "ufw": generate_ufw_playbook,
    }
    gen = generators.get(backend)
    if not gen:
        raise ValueError(f"Unsupported firewall backend: {backend}")
    return gen(host_ip, rules, ssh_key_path)
