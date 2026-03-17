import yaml
from app.ansible.inventory import generate_inventory


def generate_service_playbook(
    host_ip: str,
    ssh_port: int,
    services: list[dict],  # list of {"service_name": str, "state": str, "enabled": bool}
    ssh_key_path: str,
) -> tuple[str, str]:
    """
    Generate Ansible playbook and inventory for service management.
    
    Returns (playbook_yaml, inventory_json) tuple.
    
    State mapping:
      "running" -> "started"
      "stopped" -> "stopped"
    
    Never use state: restarted or reloaded (those are transient).
    """
    STATE_MAP = {
        "running": "started",
        "stopped": "stopped",
    }
    
    tasks = []
    for svc in services:
        state_str = svc["state"].value if hasattr(svc["state"], "value") else str(svc["state"])
        tasks.append({
            "name": f"Manage service {svc['service_name']}",
            "ansible.builtin.service": {
                "name": svc["service_name"],
                "state": STATE_MAP.get(state_str, "started"),
                "enabled": svc["enabled"],
            },
        })
    
    playbook = [
        {
            "name": "Barricade service management",
            "hosts": "all",
            "become": True,
            "gather_facts": False,
            "tasks": tasks,
        }
    ]
    
    playbook_yaml = yaml.dump(playbook, default_flow_style=False, sort_keys=False)
    inventory_json = generate_inventory(host_ip, ssh_port, ssh_key_path)
    
    return playbook_yaml, inventory_json
