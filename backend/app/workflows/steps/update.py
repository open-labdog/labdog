"""System update step for LabDog update workflows.

Runs the action-registry playbook on a remote host via the shared run_ansible()
helper.  Rebooting is handled by the separate reboot.py step, so auto_reboot is
always forced to False here.
"""

import tempfile

from app.actions.registry import ACTION_REGISTRY
from app.ansible.inventory import generate_inventory
from app.ansible.runner import run_ansible


def run_system_update(
    host_ip: str,
    ssh_port: int,
    ssh_user: str,
    ssh_key_path: str,
    action_key: str,
    action_parameters: dict,
    timeout: int = 600,
) -> dict:
    """Run a full system package upgrade on a remote host.

    Looks up the playbook to execute from the action registry using
    *action_key*, then delegates to :func:`app.ansible.runner.run_ansible`.
    The caller is responsible for writing the decrypted SSH key to
    ``ssh_key_path`` before calling this function and for cleaning it up
    afterwards.

    Args:
        host_ip: IP address of the target host.
        ssh_port: SSH port on the target host.
        ssh_user: SSH login username.
        ssh_key_path: Absolute path to the (already-decrypted) SSH private key.
        action_key: Key used to look up the action in ``ACTION_REGISTRY``.
        action_parameters: Parameters from the workflow model forwarded as
            Ansible extra vars.  ``auto_reboot`` is always overridden to
            ``False`` and ``cleanup`` is always set to ``True``.
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
    action = ACTION_REGISTRY[action_key]

    extra_vars = {
        **action_parameters,
        "auto_reboot": False,
        "cleanup": True,
    }

    inventory_json = generate_inventory(host_ip, ssh_port, ssh_key_path, ssh_user)

    private_data_dir = tempfile.mkdtemp(prefix="labdog-update-")

    try:
        runner = run_ansible(
            playbook_path=action.playbook_path,
            inventory_json=inventory_json,
            private_data_dir=private_data_dir,
            extra_vars=extra_vars,
            timeout=timeout,
            roles_paths=list(action.roles_paths) if action.roles_paths else None,
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
        import shutil

        shutil.rmtree(private_data_dir, ignore_errors=True)
