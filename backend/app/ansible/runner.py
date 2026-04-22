import json
import os
from pathlib import Path
from typing import Any

ANSIBLE_ROLES_PATH = Path(__file__).parent / "roles"


def generate_multi_host_inventory(
    hosts: list[dict],
) -> str:
    """Generate Ansible inventory JSON for multiple hosts.

    Each host gets a unique key derived from its 'name' value, or 'host_{ip}'
    if the name key is absent. All hosts must supply their own ssh_key_path
    (caller is responsible for writing the key to tmpfs beforehand).

    Args:
        hosts: List of dicts, each containing:
            - ip: str — target IP address
            - port: int — SSH port
            - ssh_user: str — SSH login user
            - ssh_key_path: str — path to the private key file on disk
            - name: str (optional) — unique label for this host entry

    Returns:
        JSON string with all hosts nested under ``all.hosts``.
    """
    all_hosts: dict[str, Any] = {}
    for host in hosts:
        key = host.get("name") or f"host_{host['ip']}"
        all_hosts[key] = {
            "ansible_host": host["ip"],
            "ansible_port": host["port"],
            "ansible_user": host["ssh_user"],
            "ansible_ssh_private_key_file": host["ssh_key_path"],
            "ansible_ssh_common_args": "-o StrictHostKeyChecking=accept-new",
        }
    inventory = {"all": {"hosts": all_hosts}}
    return json.dumps(inventory, indent=2)


def run_ansible(
    *,
    playbook_yaml: str | None = None,
    playbook_path: Path | None = None,
    inventory_json: str,
    private_data_dir: str,
    extra_vars: dict | None = None,
    timeout: int | None = None,
    envvars: dict | None = None,
) -> Any:
    """Write playbook + inventory to *private_data_dir* and invoke ansible_runner.

    Exactly one of *playbook_yaml* or *playbook_path* must be provided.
    When *playbook_path* is given its content is read from disk.

    The caller is responsible for creating *private_data_dir* and for all
    tmpfs / cleanup lifecycle management — this function only writes the
    ``project/playbook.yml`` and ``inventory/hosts`` files, then delegates
    to ``ansible_runner.run()``.

    Args:
        playbook_yaml: Playbook content as a string.
        playbook_path: Path to an existing playbook file whose content will
            be read and written to the runner project directory.
        inventory_json: Ansible inventory serialised as a JSON string.
        private_data_dir: Path to the ansible-runner working directory.
        extra_vars: Optional dict of extra variables forwarded to
            ``ansible_runner.run()`` as ``extravars``.
        timeout: Optional timeout in seconds forwarded to
            ``ansible_runner.run()``.
        envvars: Optional dict of environment variables merged with the
            default ``ANSIBLE_ROLES_PATH`` env var and forwarded to
            ``ansible_runner.run()``.

    Returns:
        The ``ansible_runner.Runner`` object returned by ``ansible_runner.run()``.

    Raises:
        ValueError: If neither or both of *playbook_yaml* / *playbook_path*
            are provided.
    """
    import ansible_runner

    if playbook_yaml is not None and playbook_path is not None:
        raise ValueError("Provide exactly one of playbook_yaml or playbook_path, not both.")
    if playbook_yaml is None and playbook_path is None:
        raise ValueError("One of playbook_yaml or playbook_path must be provided.")

    if playbook_path is not None:
        playbook_yaml = Path(playbook_path).read_text()

    os.makedirs(f"{private_data_dir}/project", exist_ok=True)
    os.makedirs(f"{private_data_dir}/inventory", exist_ok=True)

    with open(f"{private_data_dir}/project/playbook.yml", "w") as f:
        f.write(playbook_yaml)  # type: ignore[arg-type]
    with open(f"{private_data_dir}/inventory/hosts", "w") as f:
        f.write(inventory_json)

    # Ansible output is captured into the DB and rendered in HTML, not a
    # terminal. ansible-runner attaches a pty by default, which makes Ansible
    # emit ANSI SGR escape codes that show up as literal garbage in the UI.
    # ANSIBLE_NOCOLOR=1 tells Ansible to skip colouring regardless of tty.
    merged_env = {
        "ANSIBLE_ROLES_PATH": str(ANSIBLE_ROLES_PATH),
        "ANSIBLE_NOCOLOR": "1",
    }
    if envvars:
        merged_env.update(envvars)

    runner = ansible_runner.run(
        private_data_dir=private_data_dir,
        playbook="playbook.yml",
        extravars=extra_vars or {},
        timeout=timeout,
        envvars=merged_env,
    )
    return runner
