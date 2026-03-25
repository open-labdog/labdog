import yaml
from app.ansible.inventory import generate_inventory


def generate_hosts_file_playbook(
    host_ip: str,
    ssh_port: int,
    rendered_content: str,
    ssh_key_path: str,
    ssh_user: str = "root",
) -> tuple[str, str]:
    """
    Generate Ansible playbook to deploy /etc/hosts via atomic copy.
    
    Uses ansible.builtin.copy with content parameter (not template).
    Validates result contains '127.0.0.1 localhost' before committing.
    
    Returns (playbook_yaml, inventory_json) tuple.
    """
    tasks = [
        {
            "name": "Deploy /etc/hosts",
            "ansible.builtin.copy": {
                "content": rendered_content,
                "dest": "/etc/hosts",
                "owner": "root",
                "group": "root",
                "mode": "0644",
                "validate": 'grep -q "127.0.0.1 localhost" %s',
            },
        }
    ]

    playbook = [
        {
            "name": "Barricade /etc/hosts management",
            "hosts": "all",
            "become": True,
            "gather_facts": False,
            "tasks": tasks,
        }
    ]

    playbook_yaml = yaml.dump(playbook, default_flow_style=False, sort_keys=False)
    inventory_json = generate_inventory(host_ip, ssh_port, ssh_key_path, ssh_user)

    return playbook_yaml, inventory_json
