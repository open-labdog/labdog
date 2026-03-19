from app.ansible.inventory import generate_inventory


def generate_resolver_playbook(
    host_ip: str,
    resolver_type: str,
    rendered_content: str,
    ssh_key_path: str,
    ssh_port: int = 22,
    ssh_user: str = "root",
) -> dict:
    """
    Generate an Ansible playbook dict for DNS resolver configuration.

    Supports three resolver backends:
    - resolv_conf: writes /etc/resolv.conf directly
    - systemd_resolved: writes /etc/systemd/resolved.conf + restarts service
    - networkmanager: writes NM dns config + restarts service

    Returns a dict with 'playbook' and 'inventory' keys for ansible-runner.
    """
    tasks: list[dict] = _build_tasks(resolver_type, rendered_content)

    playbook = [
        {
            "name": "Barricade DNS resolver sync",
            "hosts": "all",
            "become": True,
            "gather_facts": False,
            "tasks": tasks,
        }
    ]

    inventory = generate_inventory(host_ip, ssh_port, ssh_key_path, ssh_user)

    return {"playbook": playbook, "inventory": inventory}


def _build_tasks(resolver_type: str, rendered_content: str) -> list[dict]:

    if resolver_type == "resolv_conf":
        return _tasks_resolv_conf(rendered_content)
    elif resolver_type == "systemd_resolved":
        return _tasks_systemd_resolved(rendered_content)
    elif resolver_type == "networkmanager":
        return _tasks_networkmanager(rendered_content)
    else:
        raise ValueError(f"Unsupported resolver type: {resolver_type}")


def _tasks_resolv_conf(content: str) -> list[dict]:
    return [
        {
            "name": "Write /etc/resolv.conf",
            "ansible.builtin.copy": {
                "content": content,
                "dest": "/etc/resolv.conf",
                "mode": "0644",
            },
        },
    ]


def _tasks_systemd_resolved(content: str) -> list[dict]:
    return [
        {
            "name": "Write /etc/systemd/resolved.conf",
            "ansible.builtin.copy": {
                "content": content,
                "dest": "/etc/systemd/resolved.conf",
                "mode": "0644",
            },
        },
        {
            "name": "Restart systemd-resolved",
            "ansible.builtin.systemd": {
                "name": "systemd-resolved",
                "state": "restarted",
            },
        },
    ]


def _tasks_networkmanager(content: str) -> list[dict]:
    return [
        {
            "name": "Write NetworkManager DNS config",
            "ansible.builtin.copy": {
                "content": content,
                "dest": "/etc/NetworkManager/conf.d/90-barricade-dns.conf",
                "mode": "0644",
            },
        },
        {
            "name": "Restart NetworkManager",
            "ansible.builtin.systemd": {
                "name": "NetworkManager",
                "state": "restarted",
            },
        },
    ]
