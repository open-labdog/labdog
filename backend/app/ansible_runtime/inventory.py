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


