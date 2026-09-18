import yaml

from app.rules.model import ChainPolicies, FirewallRuleSpec
from app.rules.renderers.iptables import render_iptables_rules
from app.rules.renderers.nftables import render_nftables_config

# ---------------------------------------------------------------------------
# Inactive-backend teardown
#
# On a dual-stack host (both nft and iptables installed) LabDog manages exactly
# one backend, but a LabDog ruleset can linger in the other one — left over from
# a backend switch, or from a manual edit. Collection only ever reads the active
# backend, so the stale copy is invisible in the UI yet may still filter
# traffic. Every firewall sync therefore removes LabDog's footprint from the
# *inactive* backend so there is a single source of truth.
#
# Both teardowns are strictly scoped to LabDog's own footprint and are safe to
# run unconditionally: they self-guard on the tool being installed and only act
# when the LabDog marker is present. They never touch the base INPUT/OUTPUT
# chains or any table LabDog doesn't own, so Docker/kube-proxy/firewalld rules
# are left intact.
# ---------------------------------------------------------------------------


#: Ansible fact the firewall plays register their scratch directory under.
#: Referenced as ``{{ labdog_fw_tmp.path }}`` throughout both playbooks.
_WORKDIR_VAR = "labdog_fw_tmp"

#: Jinja expression for that directory, as written into the shell
#: fragments below. Kept short so the command strings stay readable.
_TMP = "{{ " + _WORKDIR_VAR + ".path }}"


def _workdir_task() -> dict:
    """Create a private scratch directory on the managed host (SEC-33).

    The deadman's-switch machinery used fixed paths under ``/tmp`` —
    ``/tmp/nftables-backup.conf``, ``/tmp/nftables-revert.pid`` and the
    iptables equivalents — on the *managed* host, then ran
    ``kill $(cat …)`` as root against one of them. Any local user on
    that host could pre-create or symlink those names and choose what
    root read back: which ruleset gets restored when the switch fires,
    or which process id gets signalled.

    ``fs.protected_regular`` blunts the write side on modern kernels. It
    does nothing about the read side, and it is a kernel setting LabDog
    does not control on hosts it manages.

    ``ansible.builtin.tempfile`` gives a 0700 directory owned by root
    (the play runs with ``become``) whose name is unpredictable, which
    removes both.
    """
    return {
        "name": "Create private working directory for firewall rollback state",
        "ansible.builtin.tempfile": {
            "state": "directory",
            "prefix": "labdog-fw-",
        },
        "register": _WORKDIR_VAR,
    }


def _cleanup_workdir_task() -> dict:
    """Remove the scratch directory and everything in it.

    Runs after the revert has been cancelled, so the deadman's switch
    still has its backups for as long as it might need them. If the play
    dies before this point — which is exactly when the switch matters —
    the directory survives for the revert to use, as the individual
    files did before.
    """
    return {
        "name": "Clean up firewall rollback working directory",
        "ansible.builtin.file": {
            "path": _TMP,
            "state": "absent",
        },
    }


def _iptables_teardown_task() -> dict:
    """Task removing stale LabDog iptables chains (run when nftables is active).

    Drops the ``INPUT``/``OUTPUT`` jumps and flush/deletes the dedicated
    ``LABDOG-INPUT``/``LABDOG-OUTPUT`` chains for both IPv4 and IPv6, then
    re-persists so the chains don't reappear at boot. Only LabDog's own chains
    are touched — the base chains and other tools' rules are left alone.
    """
    script = r"""
for t in "$(command -v iptables || echo /usr/sbin/iptables)" \
         "$(command -v ip6tables || echo /usr/sbin/ip6tables)"; do
    [ -x "$t" ] || command -v "$t" >/dev/null 2>&1 || continue
    if "$t" -S LABDOG-INPUT >/dev/null 2>&1; then
        while "$t" -D INPUT -j LABDOG-INPUT 2>/dev/null; do :; done
        "$t" -F LABDOG-INPUT 2>/dev/null || true
        "$t" -X LABDOG-INPUT 2>/dev/null || true
    fi
    if "$t" -S LABDOG-OUTPUT >/dev/null 2>&1; then
        while "$t" -D OUTPUT -j LABDOG-OUTPUT 2>/dev/null; do :; done
        "$t" -F LABDOG-OUTPUT 2>/dev/null || true
        "$t" -X LABDOG-OUTPUT 2>/dev/null || true
    fi
done
# Persist the cleaned state so the stale chains stay gone across reboots.
if command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent save 2>/dev/null || true
fi
"""
    return {
        "name": "Remove stale LabDog iptables rules (nftables is the active backend)",
        "ansible.builtin.shell": {"cmd": script.strip() + "\n"},
        "ignore_errors": True,
    }


def _nftables_teardown_task() -> dict:
    """Task removing a stale LabDog nftables table (run when iptables is active).

    Deletes ``table inet filter`` only when it carries the LabDog marker, so an
    operator's own ``inet filter`` table is never touched, and neutralises
    ``/etc/nftables.conf`` if LabDog wrote it so the table doesn't return at
    boot. LabDog owns the whole table (its renderer does delete+recreate), so
    deleting it is the correct teardown.
    """
    script = r"""
n="$(command -v nft || echo /usr/sbin/nft)"
{ [ -x "$n" ] || command -v nft >/dev/null 2>&1; } || exit 0
if "$n" list table inet filter 2>/dev/null | grep -q "Managed by LabDog"; then
    "$n" delete table inet filter 2>/dev/null || true
fi
# If LabDog wrote the boot config, replace it with a harmless no-op so the
# table isn't recreated on the next boot. A file with no ruleset directives
# does not flush anything, so other nftables tables are unaffected.
if [ -f /etc/nftables.conf ] && grep -q "Managed by LabDog" /etc/nftables.conf; then
    printf '#!/usr/sbin/nft -f\n# Managed by LabDog: nftables backend inactive on this host.\n' \
        > /etc/nftables.conf
fi
"""
    return {
        "name": "Remove stale LabDog nftables table (iptables is the active backend)",
        "ansible.builtin.shell": {"cmd": script.strip() + "\n"},
        "ignore_errors": True,
    }


def generate_nftables_playbook(
    host_ip: str,
    rules: list[FirewallRuleSpec],
    ssh_key_path: str,
    policies: ChainPolicies | None = None,
) -> str:
    """Generate playbook that writes nftables.conf and reloads with safe rollback.

    Strategy (deadman's switch):
    1. Backup current ruleset to /tmp
    2. Schedule an automatic revert in 60 seconds (deadman's switch)
    3. Write and validate new config
    4. Apply atomically with nft -f
    5. If we're still connected (SSH survived), cancel the revert
    6. Remove any stale LabDog iptables footprint (dual-stack teardown)
    7. Enable nftables service on boot

    If applying the new rules kills SSH, the scheduled revert fires
    after 60 seconds and restores the previous ruleset automatically.
    """
    nft_config = render_nftables_config(rules, policies=policies)
    tasks = [
        _workdir_task(),
        {
            "name": "Backup current nftables ruleset",
            "ansible.builtin.shell": (
                f"/usr/sbin/nft list table inet filter > {_TMP}/nftables-backup.conf"
                f" 2>/dev/null || touch {_TMP}/nftables-backup.conf"
            ),
        },
        {
            "name": "Schedule automatic revert in 60 seconds (deadman switch)",
            "ansible.builtin.shell": (
                "nohup bash -c '"
                "sleep 60 && "
                "/usr/sbin/nft delete table inet filter 2>/dev/null; "
                f"/usr/sbin/nft -f {_TMP}/nftables-backup.conf 2>/dev/null; "
                f"cp {_TMP}/nftables-backup.conf.orig /etc/nftables.conf 2>/dev/null"
                f"' > {_TMP}/nftables-revert.log 2>&1 & "
                f"echo $! > {_TMP}/nftables-revert.pid"
            ),
        },
        {
            "name": "Backup original config file",
            "ansible.builtin.copy": {
                "src": "/etc/nftables.conf",
                "dest": f"{_TMP}/nftables-backup.conf.orig",
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
                f"if [ -f {_TMP}/nftables-revert.pid ]; then "
                f"kill $(cat {_TMP}/nftables-revert.pid) 2>/dev/null; "
                f"rm -f {_TMP}/nftables-revert.pid; "
                "fi"
            ),
        },
        # nftables is now the active backend; strip any stale LabDog iptables
        # footprint so it can't shadow this ruleset or confuse collection.
        _iptables_teardown_task(),
        {
            "name": "Enable nftables service on boot",
            "ansible.builtin.service": {
                "name": "nftables",
                "enabled": True,
            },
        },
        _cleanup_workdir_task(),
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


def generate_iptables_playbook(
    host_ip: str,
    rules: list[FirewallRuleSpec],
    ssh_key_path: str,
    policies: ChainPolicies | None = None,
) -> str:
    """Generate playbook that writes iptables rules and applies with safe rollback.

    Strategy (deadman's switch):
    1. Backup current ruleset via iptables-save
    2. Schedule an automatic revert in 60 seconds (deadman's switch)
    3. Write new IPv4 and IPv6 rules files
    4. Apply with iptables-restore / ip6tables-restore
    5. If we're still connected (SSH survived), cancel the revert
    6. Remove any stale LabDog nftables footprint (dual-stack teardown)
    7. Install iptables-persistent for boot persistence
    8. Save rules for persistence

    If applying the new rules kills SSH, the scheduled revert fires
    after 60 seconds and restores the previous ruleset automatically.
    """
    ipv4_content, ipv6_content = render_iptables_rules(rules, policies=policies)
    tasks = [
        _workdir_task(),
        {
            "name": "Backup current iptables ruleset",
            "ansible.builtin.shell": (
                f"iptables-save > {_TMP}/iptables-backup.rules"
                f" 2>/dev/null || touch {_TMP}/iptables-backup.rules"
            ),
        },
        {
            "name": "Backup current ip6tables ruleset",
            "ansible.builtin.shell": (
                f"ip6tables-save > {_TMP}/ip6tables-backup.rules"
                f" 2>/dev/null || touch {_TMP}/ip6tables-backup.rules"
            ),
        },
        {
            "name": "Schedule automatic revert in 60 seconds (deadman switch)",
            "ansible.builtin.shell": (
                "nohup bash -c '"
                "sleep 60 && "
                f"iptables-restore < {_TMP}/iptables-backup.rules && "
                f"ip6tables-restore < {_TMP}/ip6tables-backup.rules"
                f"' > {_TMP}/iptables-revert.log 2>&1 & "
                f"echo $! > {_TMP}/iptables-revert.pid"
            ),
        },
        {
            "name": "Write iptables rules (IPv4)",
            "ansible.builtin.copy": {
                "content": ipv4_content,
                "dest": "/etc/iptables.rules",
                "owner": "root",
                "group": "root",
                "mode": "0644",
            },
        },
        {
            "name": "Write ip6tables rules (IPv6)",
            "ansible.builtin.copy": {
                "content": ipv6_content,
                "dest": "/etc/ip6tables.rules",
                "owner": "root",
                "group": "root",
                "mode": "0644",
            },
        },
        {
            "name": "Apply iptables rules (IPv4)",
            "ansible.builtin.shell": "iptables-restore --noflush < /etc/iptables.rules",
        },
        {
            "name": "Apply ip6tables rules (IPv6)",
            "ansible.builtin.shell": "ip6tables-restore --noflush < /etc/ip6tables.rules",
        },
        {
            "name": "Ensure INPUT jumps to LABDOG-INPUT",
            "ansible.builtin.shell": (
                "iptables -C INPUT -j LABDOG-INPUT 2>/dev/null || "
                "iptables -I INPUT 1 -j LABDOG-INPUT"
            ),
        },
        {
            "name": "Ensure OUTPUT jumps to LABDOG-OUTPUT",
            "ansible.builtin.shell": (
                "iptables -C OUTPUT -j LABDOG-OUTPUT 2>/dev/null || "
                "iptables -I OUTPUT 1 -j LABDOG-OUTPUT"
            ),
        },
        {
            "name": "Ensure INPUT jumps to LABDOG-INPUT (IPv6)",
            "ansible.builtin.shell": (
                "ip6tables -C INPUT -j LABDOG-INPUT 2>/dev/null || "
                "ip6tables -I INPUT 1 -j LABDOG-INPUT"
            ),
        },
        {
            "name": "Ensure OUTPUT jumps to LABDOG-OUTPUT (IPv6)",
            "ansible.builtin.shell": (
                "ip6tables -C OUTPUT -j LABDOG-OUTPUT 2>/dev/null || "
                "ip6tables -I OUTPUT 1 -j LABDOG-OUTPUT"
            ),
        },
        {
            "name": "Cancel automatic revert (SSH still works)",
            "ansible.builtin.shell": (
                f"if [ -f {_TMP}/iptables-revert.pid ]; then "
                f"kill $(cat {_TMP}/iptables-revert.pid) 2>/dev/null; "
                f"rm -f {_TMP}/iptables-revert.pid; "
                "fi"
            ),
        },
        # iptables is now the active backend; strip any stale LabDog nftables
        # table so it can't shadow this ruleset or confuse collection.
        _nftables_teardown_task(),
        {
            "name": "Install iptables-persistent for boot persistence",
            "ansible.builtin.package": {
                "name": "iptables-persistent",
                "state": "present",
            },
            "ignore_errors": True,
        },
        {
            "name": "Install netfilter-persistent for boot persistence (fallback)",
            "ansible.builtin.package": {
                "name": "netfilter-persistent",
                "state": "present",
            },
            "ignore_errors": True,
        },
        {
            "name": "Save iptables rules for persistence",
            "ansible.builtin.shell": (
                "if command -v netfilter-persistent >/dev/null 2>&1; then "
                "netfilter-persistent save; "
                "else "
                "cp /etc/iptables.rules /etc/iptables/rules.v4 2>/dev/null; "
                "cp /etc/ip6tables.rules /etc/iptables/rules.v6 2>/dev/null; "
                "fi"
            ),
            "ignore_errors": True,
        },
        _cleanup_workdir_task(),
    ]
    playbook = [
        {
            "name": "Apply iptables firewall rules (safe mode)",
            "hosts": "target",
            "become": True,
            "gather_facts": False,
            "tasks": tasks,
        }
    ]
    return yaml.dump(playbook, default_flow_style=False, sort_keys=False)


def generate_playbook(
    backend: str,
    host_ip: str,
    rules: list[FirewallRuleSpec],
    ssh_key_path: str,
    policies: ChainPolicies | None = None,
) -> str:
    """Dispatch to backend-specific generator."""
    generators = {
        "nftables": generate_nftables_playbook,
        "iptables": generate_iptables_playbook,
    }
    gen = generators.get(backend)
    if not gen:
        raise ValueError(f"Unsupported firewall backend: {backend}")
    return gen(host_ip, rules, ssh_key_path, policies=policies)
