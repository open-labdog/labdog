"""Ansible playbook generator for CA certificate deployment.

Strategy
--------
Each cert is written to the OS-appropriate trust-anchor directory under
a deterministic ``barricade-<fingerprint16>.crt`` filename. After all
copies/removals are done, a single per-OS-family update task rebuilds
the trust store.

Reconciliation (no DB tracking of "previously deployed")
--------------------------------------------------------
The generator emits a "reconcile" task that lists existing
``barricade-*.crt`` files on the host and removes any whose filename is
not in the current desired set. This means deleting a cert from the
group config and re-running the action removes it from hosts, without
Barricade needing to remember what was previously deployed.
"""
from app.ansible.inventory import generate_inventory


# Per-OS-family configuration: drop-in directory + update command.
_OS_CONFIG: list[tuple[str, str, str, str]] = [
    # (label, ansible_os_family, drop-in dir, update command)
    ("Debian", "Debian", "/usr/local/share/ca-certificates", "update-ca-certificates"),
    ("RedHat", "RedHat", "/etc/pki/ca-trust/source/anchors", "update-ca-trust extract"),
    ("Suse", "Suse", "/etc/pki/trust/anchors", "update-ca-certificates"),
]


def _short_fingerprint(fp: str) -> str:
    """Return the first 16 hex chars of a colon-separated fingerprint."""
    return fp.replace(":", "").lower()[:16]


def cert_filename(fingerprint_sha256: str) -> str:
    return f"barricade-{_short_fingerprint(fingerprint_sha256)}.crt"


def generate_ca_cert_playbook(
    host_ip: str,
    certs: list[dict],
    ssh_key_path: str,
    ssh_port: int = 22,
    ssh_user: str = "root",
) -> dict:
    """Build an Ansible playbook + inventory for CA cert deployment.

    Parameters
    ----------
    certs:
        Effective cert list. Each entry must contain ``fingerprint_sha256``,
        ``pem_content``, ``state`` (``"present"`` or ``"absent"``), and
        ``name`` (for task labels).

    Returns
    -------
    ``{"playbook": [...], "inventory": "..."}`` ready for ansible-runner.
    """
    present_certs = [c for c in certs if c.get("state", "present") == "present"]
    absent_fps = {
        c["fingerprint_sha256"] for c in certs if c.get("state") == "absent"
    }

    # Filenames the host should retain at the end of the run.
    keep_filenames = {
        cert_filename(c["fingerprint_sha256"]) for c in present_certs
    }

    tasks: list[dict] = []

    for label, os_family, drop_in_dir, update_cmd in _OS_CONFIG:
        os_when = f"ansible_facts['os_family'] == '{os_family}'"

        tasks.append({
            "name": f"[{label}] Ensure trust-anchor directory exists",
            "ansible.builtin.file": {
                "path": drop_in_dir,
                "state": "directory",
                "mode": "0755",
            },
            "when": os_when,
        })

        # Write/copy desired (present) certs
        for cert in present_certs:
            fname = cert_filename(cert["fingerprint_sha256"])
            tasks.append({
                "name": (
                    f"[{label}] Install CA cert: {cert.get('name', fname)} "
                    f"({fname})"
                ),
                "ansible.builtin.copy": {
                    "content": cert["pem_content"],
                    "dest": f"{drop_in_dir}/{fname}",
                    "owner": "root",
                    "group": "root",
                    "mode": "0644",
                },
                "when": os_when,
                "notify": f"update-ca-{label.lower()}",
            })

        # Explicit removals (host-level state=absent overrides)
        for fp in sorted(absent_fps):
            fname = cert_filename(fp)
            tasks.append({
                "name": f"[{label}] Remove CA cert (explicit absent): {fname}",
                "ansible.builtin.file": {
                    "path": f"{drop_in_dir}/{fname}",
                    "state": "absent",
                },
                "when": os_when,
                "notify": f"update-ca-{label.lower()}",
            })

        # Reconcile: discover any barricade-*.crt files and remove
        # those not in the desired set.
        keep_full_paths = sorted(
            f"{drop_in_dir}/{fname}" for fname in keep_filenames
        )
        keep_paths_jinja = (
            "[" + ", ".join(f"'{p}'" for p in keep_full_paths) + "]"
        )

        tasks.append({
            "name": f"[{label}] Discover existing Barricade-managed certs",
            "ansible.builtin.find": {
                "paths": drop_in_dir,
                "patterns": "barricade-*.crt",
                "file_type": "file",
            },
            "register": f"barricade_existing_{label.lower()}",
            "when": os_when,
        })

        tasks.append({
            "name": f"[{label}] Remove orphaned Barricade-managed certs",
            "ansible.builtin.file": {
                "path": "{{ item.path }}",
                "state": "absent",
            },
            "loop": (
                "{{ "
                f"barricade_existing_{label.lower()}.files | default([]) "
                f"| rejectattr('path', 'in', {keep_paths_jinja}) "
                "| list "
                "}}"
            ),
            "when": os_when,
            "notify": f"update-ca-{label.lower()}",
        })

    # Handlers run once at the end if notified, ensuring the trust store
    # is only rebuilt when something actually changed.
    handlers = [
        {
            "name": f"update-ca-{label.lower()}",
            "ansible.builtin.command": update_cmd,
        }
        for label, _of, _dir, update_cmd in _OS_CONFIG
    ]

    playbook = [
        {
            "name": "Barricade CA Certificate Deployment",
            "hosts": "all",
            "become": True,
            "gather_facts": True,
            "tasks": tasks,
            "handlers": handlers,
        }
    ]

    inventory = generate_inventory(host_ip, ssh_port, ssh_key_path, ssh_user)
    return {"playbook": playbook, "inventory": inventory}
