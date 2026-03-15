import json


def generate_inventory(host_ip: str, ssh_port: int, ssh_key_path: str, ssh_user: str = "root") -> str:
    """Generate Ansible inventory JSON for a single host."""
    inventory = {
        "all": {
            "hosts": {
                "target": {
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
