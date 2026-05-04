from app.ansible_runtime.inventory import generate_inventory

CLOUD_INIT_DISABLE_PATH = "/etc/cloud/cloud.cfg.d/99-labdog-disable-network-config.cfg"
CLOUD_INIT_DISABLE_CONTENT = (
    "# Managed by LabDog. Prevents cloud-init from re-applying network/DNS\n"
    "# config on boot, which would overwrite the LabDog resolver sync.\n"
    "network: {config: disabled}\n"
)


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
            "name": "LabDog DNS resolver sync",
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
        backend_tasks = _tasks_resolv_conf(rendered_content)
    elif resolver_type == "systemd_resolved":
        backend_tasks = _tasks_systemd_resolved(rendered_content)
    elif resolver_type == "networkmanager":
        backend_tasks = _tasks_networkmanager(rendered_content)
    else:
        raise ValueError(f"Unsupported resolver type: {resolver_type}")
    return _disable_cloud_init_network_tasks() + backend_tasks


def _disable_cloud_init_network_tasks() -> list[dict]:
    return [
        {
            "name": "Check for cloud-init",
            "ansible.builtin.stat": {"path": "/etc/cloud/cloud.cfg.d"},
            "register": "labdog_cloud_init_dir",
        },
        {
            "name": "Disable cloud-init network config (if cloud-init present)",
            "ansible.builtin.copy": {
                "content": CLOUD_INIT_DISABLE_CONTENT,
                "dest": CLOUD_INIT_DISABLE_PATH,
                "mode": "0644",
            },
            "when": "labdog_cloud_init_dir.stat.exists",
        },
    ]


def _tasks_resolv_conf(content: str) -> list[dict]:
    return [
        {
            "name": "Stat /etc/resolv.conf",
            "ansible.builtin.stat": {"path": "/etc/resolv.conf"},
            "register": "labdog_resolv_stat",
        },
        {
            "name": "Replace /etc/resolv.conf symlink",
            "ansible.builtin.file": {"path": "/etc/resolv.conf", "state": "absent"},
            "when": "labdog_resolv_stat.stat.islnk | default(false)",
        },
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
                "dest": "/etc/NetworkManager/conf.d/90-labdog-dns.conf",
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
