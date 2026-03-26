"""System update step for Barricade update workflows.

Runs apt upgrade / dnf update on a remote host via ansible-runner.
"""

import os
import shutil
import tempfile

import yaml


def run_system_update(
    host_ip: str,
    ssh_port: int,
    ssh_user: str,
    ssh_key_path: str,
    timeout: int = 600,
) -> dict:
    """Run a full system package upgrade on a remote host.

    Uses ansible-runner (synchronous) to execute apt upgrade on Debian-family
    hosts and dnf update on RedHat-family hosts.  The caller is responsible for
    writing the decrypted SSH key to ``ssh_key_path`` before calling this
    function and for cleaning it up afterwards.

    Args:
        host_ip: IP address of the target host.
        ssh_port: SSH port on the target host.
        ssh_user: SSH login username.
        ssh_key_path: Absolute path to the (already-decrypted) SSH private key.
        timeout: Maximum number of seconds ansible-runner is allowed to run
            before being terminated.  Defaults to 600.

    Returns:
        A dict with the following keys:

        - ``success`` (bool): ``True`` when ansible-runner reports
          ``"successful"``.
        - ``stdout`` (str): Combined stdout captured by ansible-runner, or an
          empty string when no output is available.
        - ``rc`` (int | None): Return code reported by ansible-runner.
    """
    import ansible_runner

    from app.ansible.inventory import generate_inventory

    temp_dir = tempfile.mkdtemp(prefix="barricade-update-")

    try:
        playbook = [
            {
                "name": "Barricade System Update",
                "hosts": "all",
                "become": True,
                "gather_facts": True,
                "tasks": [
                    {
                        "name": "Update apt cache",
                        "ansible.builtin.apt": {
                            "update_cache": True,
                            "cache_valid_time": 0,
                        },
                        "when": "ansible_os_family == 'Debian'",
                    },
                    {
                        "name": "Upgrade packages",
                        "ansible.builtin.apt": {
                            "upgrade": "yes",
                        },
                        "when": "ansible_os_family == 'Debian'",
                    },
                    {
                        "name": "Autoremove unused packages",
                        "ansible.builtin.apt": {
                            "autoremove": True,
                            "purge": False,
                        },
                        "when": "ansible_os_family == 'Debian'",
                    },
                    {
                        "name": "Update all packages (RHEL)",
                        "ansible.builtin.dnf": {
                            "name": "*",
                            "state": "latest",
                        },
                        "when": "ansible_os_family == 'RedHat'",
                    },
                ],
            }
        ]

        inventory_content = generate_inventory(
            host_ip, ssh_port, ssh_key_path, ssh_user
        )

        project_dir = os.path.join(temp_dir, "project")
        inventory_dir = os.path.join(temp_dir, "inventory")
        os.makedirs(project_dir, exist_ok=True)
        os.makedirs(inventory_dir, exist_ok=True)

        playbook_path = os.path.join(project_dir, "playbook.yml")
        with open(playbook_path, "w") as f:
            yaml.dump(playbook, f, default_flow_style=False)

        inventory_path = os.path.join(inventory_dir, "hosts")
        with open(inventory_path, "w") as f:
            f.write(inventory_content)

        runner = ansible_runner.run(
            private_data_dir=temp_dir,
            playbook="playbook.yml",
            timeout=timeout,
        )

        if hasattr(runner.stdout, "read"):
            stdout = runner.stdout.read()
        else:
            stdout = str(runner.stdout) if runner.stdout else ""

        return {
            "success": runner.status == "successful",
            "stdout": stdout,
            "rc": runner.rc,
        }

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
