import json
import re

# Ansible inventory names must be a single token — no spaces, no commas.
# Hostnames we receive can be arbitrary user input, so sanitise anything that
# isn't safe. Keep it simple: letters, digits, dot, dash, underscore; replace
# everything else with an underscore.
_SAFE_INVENTORY_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitise_inventory_name(name: str) -> str:
    cleaned = _SAFE_INVENTORY_NAME_RE.sub("_", name).strip("_.")
    return cleaned or "target"


def generate_inventory(
    host_ip: str,
    ssh_port: int,
    ssh_key_path: str,
    ssh_user: str = "root",
    hostname: str | None = None,
) -> str:
    """Generate Ansible inventory JSON for a single host.

    When ``hostname`` is provided, it is used as the inventory name so that
    Ansible's play recap and per-task output lines read
    ``ok: [myhost]`` instead of ``ok: [target]``. Callers that don't pass
    a hostname keep the legacy ``"target"`` alias — existing generators
    (firewall / hosts-file / packages / services / CA-certs / resolver)
    hardcode ``hosts: "target"`` in their playbooks and still work.
    """
    inv_name = _sanitise_inventory_name(hostname) if hostname else "target"
    inventory = {
        "all": {
            "hosts": {
                inv_name: {
                    "ansible_host": host_ip,
                    "ansible_port": ssh_port,
                    "ansible_user": ssh_user,
                    "ansible_ssh_private_key_file": ssh_key_path,
                    "ansible_ssh_common_args": "-o StrictHostKeyChecking=accept-new",
                }
            }
        }
    }
    return json.dumps(inventory, indent=2)


def generate_group_inventory(
    members: list[dict],
) -> str:
    """Generate Ansible inventory JSON for a multi-host cluster run.

    ``members`` is a list of dicts shaped like::

        {
            "hostname": "cp-1",
            "host_ip": "10.0.0.10",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_key_path": "/dev/shm/labdog-action-XXXX",
            "role": "control_plane",        # or "worker"
        }

    The output groups hosts under ``all.children.control_plane`` and
    ``all.children.workers`` so the cluster-mode playbook
    (``actions/k8s-upgrade/site.yml``) can target them with
    ``hosts: control_plane`` / ``hosts: workers`` and Ansible's
    ``serial: 1`` keyword.

    Roles other than the two known values raise ``ValueError`` —
    upstream validators (orchestrator + API submit) gate this, so
    reaching here with a bad value is a programmer error.
    """
    if not members:
        raise ValueError("generate_group_inventory: members must not be empty")

    inventory: dict = {
        "all": {
            "children": {
                "control_plane": {"hosts": {}},
                "workers": {"hosts": {}},
            }
        }
    }
    for m in members:
        role = m.get("role")
        if role not in ("control_plane", "worker"):
            raise ValueError(
                f"generate_group_inventory: unknown role {role!r} for "
                f"host {m.get('hostname')!r}; expected control_plane or worker"
            )
        bucket = "control_plane" if role == "control_plane" else "workers"
        inv_name = _sanitise_inventory_name(m["hostname"])
        inventory["all"]["children"][bucket]["hosts"][inv_name] = {
            "ansible_host": m["host_ip"],
            "ansible_port": m["ssh_port"],
            "ansible_user": m["ssh_user"],
            "ansible_ssh_private_key_file": m["ssh_key_path"],
            "ansible_ssh_common_args": "-o StrictHostKeyChecking=accept-new",
        }
    return json.dumps(inventory, indent=2)
