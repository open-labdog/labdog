import yaml
from app.rules.model import FirewallRuleSpec
from app.rules.renderers.nftables import render_nftables_config
from app.rules.renderers.firewalld import render_firewalld_tasks
from app.rules.renderers.ufw import render_ufw_rules


def generate_nftables_playbook(
    host_ip: str, rules: list[FirewallRuleSpec], ssh_key_path: str
) -> str:
    """Generate playbook that writes nftables.conf and reloads with safe rollback.

    Strategy (deadman's switch):
    1. Backup current ruleset to /tmp
    2. Schedule an automatic revert in 60 seconds (deadman's switch)
    3. Write and validate new config
    4. Apply atomically with nft -f
    5. If we're still connected (SSH survived), cancel the revert
    6. Enable nftables service on boot

    If applying the new rules kills SSH, the scheduled revert fires
    after 60 seconds and restores the previous ruleset automatically.
    """
    nft_config = render_nftables_config(rules)
    tasks = [
        {
            "name": "Backup current nftables ruleset",
            "ansible.builtin.shell": "/usr/sbin/nft list ruleset > /tmp/nftables-backup.conf 2>/dev/null || touch /tmp/nftables-backup.conf",
        },
        {
            "name": "Schedule automatic revert in 60 seconds (deadman switch)",
            "ansible.builtin.shell": (
                "nohup bash -c '"
                "sleep 60 && "
                "/usr/sbin/nft flush ruleset && "
                "/usr/sbin/nft -f /tmp/nftables-backup.conf && "
                "cp /tmp/nftables-backup.conf.orig /etc/nftables.conf 2>/dev/null"
                "' > /tmp/nftables-revert.log 2>&1 & "
                "echo $! > /tmp/nftables-revert.pid"
            ),
        },
        {
            "name": "Backup original config file",
            "ansible.builtin.copy": {
                "src": "/etc/nftables.conf",
                "dest": "/tmp/nftables-backup.conf.orig",
                "remote_src": True,
            },
            "ignore_errors": True,
        },
        {
            "name": "Write nftables configuration",
            "ansible.builtin.copy": {
                "content": nft_config,
                "dest": "/etc/nftables.conf",
                "owner": "root",
                "group": "root",
                "mode": "0644",
                "validate": "/usr/sbin/nft -c -f %s",
            },
        },
        {
            "name": "Apply nftables rules atomically",
            "ansible.builtin.command": "/usr/sbin/nft -f /etc/nftables.conf",
        },
        {
            "name": "Cancel automatic revert (SSH still works)",
            "ansible.builtin.shell": (
                "if [ -f /tmp/nftables-revert.pid ]; then "
                "kill $(cat /tmp/nftables-revert.pid) 2>/dev/null; "
                "rm -f /tmp/nftables-revert.pid; "
                "fi"
            ),
        },
        {
            "name": "Enable nftables service on boot",
            "ansible.builtin.service": {
                "name": "nftables",
                "enabled": True,
            },
        },
        {
            "name": "Clean up backup files",
            "ansible.builtin.file": {
                "path": "{{ item }}",
                "state": "absent",
            },
            "loop": [
                "/tmp/nftables-backup.conf",
                "/tmp/nftables-backup.conf.orig",
                "/tmp/nftables-revert.log",
            ],
        },
    ]
    playbook = [
        {
            "name": "Apply nftables firewall rules (safe mode)",
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
