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


def build_ssh_common_args() -> str:
    """SSH options injected into every Ansible inventory entry.

    * ``ConnectTimeout`` — from the ``ssh.connect_timeout`` setting, so
      the operator-facing knob actually governs Ansible connections
      (initial connect AND mid-play reconnects), not just the asyncssh
      paths.
    * ``ServerAliveInterval/CountMax`` — a host that freezes mid-task
      while keeping the TCP session open is detected in ~3 minutes
      instead of riding out the full playbook wall-clock timeout. A
      busy-but-alive host (e.g. dist-upgrade pegging CPU) still answers
      keepalives, so this can't kill legitimate long tasks.

    Failure-safe: any error reading the setting (no DB, tests) falls
    back to Ansible-compatible defaults — inventory generation must
    never crash.
    """
    try:
        from app.settings_service import get_setting_sync_typed  # noqa: PLC0415

        connect_timeout = int(get_setting_sync_typed("ssh.connect_timeout"))
    except Exception:
        connect_timeout = 10
    return (
        "-o StrictHostKeyChecking=accept-new "
        f"-o ConnectTimeout={connect_timeout} "
        "-o ServerAliveInterval=30 -o ServerAliveCountMax=6"
    )


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
                    "ansible_ssh_common_args": build_ssh_common_args(),
                }
            }
        }
    }
    return json.dumps(inventory, indent=2)
