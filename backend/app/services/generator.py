import yaml

from app.ansible.inventory import generate_inventory


def generate_cleanup_tasks(desired_services: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """
    Generate Ansible tasks to remove orphaned LabDog-managed unit files and overrides.

    Runs before any deploy tasks so stale files are removed first.

    Args:
        desired_services: List of effective service dicts (same shape as accepted by
            ``generate_service_playbook``).

    Returns:
        A three-tuple of:
        - tasks: list of Ansible task dicts to prepend to the playbook
        - allowed_unit_paths: paths that should be preserved (full unit files)
        - allowed_override_paths: paths that should be preserved (override drop-ins)
    """
    allowed_unit_paths: list[str] = []
    allowed_override_paths: list[str] = []

    for svc in desired_services:
        unit_content = svc.get("unit_content")
        deploy_mode = svc.get("deploy_mode", "override")
        service_name = svc["service_name"]

        if unit_content is not None:
            if deploy_mode == "full":
                allowed_unit_paths.append(f"/etc/systemd/system/{service_name}.service")
            elif deploy_mode == "override":
                allowed_override_paths.append(
                    f"/etc/systemd/system/{service_name}.service.d/labdog.conf"
                )

    tasks: list[dict] = [
        # --- Override drop-in cleanup ---
        {
            "name": "Find LabDog override files",
            "ansible.builtin.find": {
                "paths": "/etc/systemd/system",
                "patterns": "labdog.conf",
                "recurse": True,
            },
            "register": "labdog_overrides",
        },
        {
            "name": "Remove orphaned LabDog overrides",
            "ansible.builtin.file": {
                "path": "{{ item.path }}",
                "state": "absent",
            },
            "loop": "{{ labdog_overrides.files }}",
            "when": "item.path not in allowed_override_paths",
        },
        {
            "name": "Remove empty override directories",
            "ansible.builtin.file": {
                "path": "{{ item.path | dirname }}",
                "state": "absent",
            },
            "loop": "{{ labdog_overrides.files }}",
            "when": "item.path not in allowed_override_paths",
        },
        # --- Full unit file cleanup ---
        {
            "name": "Find LabDog-managed unit files",
            "ansible.builtin.shell": {
                "cmd": (
                    "grep -rl '# Managed by LabDog'"
                    " /etc/systemd/system/*.service 2>/dev/null || true"
                ),
            },
            "register": "labdog_units",
            "changed_when": False,
        },
        {
            "name": "Remove orphaned LabDog unit files",
            "ansible.builtin.file": {
                "path": "{{ item }}",
                "state": "absent",
            },
            "loop": "{{ labdog_units.stdout_lines }}",
            "when": "item not in allowed_unit_paths",
        },
        # --- Daemon reload after cleanup ---
        {
            "name": "Reload systemd after cleanup",
            "ansible.builtin.systemd": {
                "daemon_reload": True,
            },
            "when": (
                "(labdog_overrides.files | length > 0)"
                " or (labdog_units.stdout_lines | default([]) | length > 0)"
            ),
        },
    ]

    return tasks, allowed_unit_paths, allowed_override_paths


def generate_service_playbook(
    host_ip: str,
    ssh_port: int,
    services: list[dict],  # list of EffectiveServiceResponse fields
    ssh_key_path: str,
    ssh_user: str = "root",
) -> tuple[str, str]:
    """
    Generate Ansible playbook and inventory for service management.

    Returns (playbook_yaml, inventory_json) tuple.

    Each entry in *services* may contain:
      - service_name (str)
      - state (str | enum): "running" | "stopped"
      - enabled (bool)
      - unit_content (str | None): raw unit file / override content
      - deploy_mode (str): "full" | "override"

    Task order per service:
      1. Deploy unit file (copy to /etc/systemd/system/<name>.service) — when
         unit_content is set and deploy_mode == "full".
      2. Create override directory + deploy drop-in — when unit_content is set
         and deploy_mode == "override".
      3. Daemon reload — when a file was deployed in step 1 or 2.
      4. Manage service state/enabled (always).

    State mapping:
      "running" -> "started"
      "stopped" -> "stopped"

    Never use state: restarted or reloaded (those are transient).
    """
    STATE_MAP = {
        "running": "started",
        "stopped": "stopped",
    }

    cleanup_tasks, allowed_unit_paths, allowed_override_paths = generate_cleanup_tasks(services)

    gather_task = {
        "name": "Gather systemd service facts",
        "ansible.builtin.service_facts": {},
    }

    deploy_tasks: list[dict] = []
    for svc in services:
        service_name = svc["service_name"]
        unit_content = svc.get("unit_content")
        deploy_mode = svc.get("deploy_mode", "override")
        file_deployed = False
        is_override = deploy_mode == "override"
        when_clause = f"'{service_name}.service' in ansible_facts.services" if is_override else None

        def _with_when(task: dict) -> dict:
            if when_clause:
                task["when"] = when_clause
            return task

        if unit_content is not None:
            if deploy_mode == "full":
                deploy_tasks.append(
                    {
                        "name": f"Deploy unit file for {service_name}",
                        "ansible.builtin.copy": {
                            "dest": f"/etc/systemd/system/{service_name}.service",
                            "content": f"# Managed by LabDog\n{unit_content}",
                            "owner": "root",
                            "group": "root",
                            "mode": "0644",
                        },
                    }
                )
                file_deployed = True

            elif deploy_mode == "override":
                deploy_tasks.append(
                    _with_when(
                        {
                            "name": f"Create override directory for {service_name}",
                            "ansible.builtin.file": {
                                "path": f"/etc/systemd/system/{service_name}.service.d",
                                "state": "directory",
                                "owner": "root",
                                "group": "root",
                                "mode": "0755",
                            },
                        }
                    )
                )
                deploy_tasks.append(
                    _with_when(
                        {
                            "name": f"Deploy override file for {service_name}",
                            "ansible.builtin.copy": {
                                "dest": (
                                    f"/etc/systemd/system/{service_name}.service.d/labdog.conf"
                                ),
                                "content": unit_content,
                                "owner": "root",
                                "group": "root",
                                "mode": "0644",
                            },
                        }
                    )
                )
                file_deployed = True

        if file_deployed:
            deploy_tasks.append(
                _with_when(
                    {
                        "name": f"Reload systemd after deploying {service_name}",
                        "ansible.builtin.systemd": {
                            "daemon_reload": True,
                        },
                    }
                )
            )

        state_str = svc["state"].value if hasattr(svc["state"], "value") else str(svc["state"])
        deploy_tasks.append(
            _with_when(
                {
                    "name": f"Manage service {service_name}",
                    "ansible.builtin.service": {
                        "name": service_name,
                        "state": STATE_MAP.get(state_str, "started"),
                        "enabled": svc["enabled"],
                    },
                }
            )
        )

    playbook = [
        {
            "name": "LabDog service management",
            "hosts": "all",
            "become": True,
            "gather_facts": False,
            "vars": {
                "allowed_unit_paths": allowed_unit_paths,
                "allowed_override_paths": allowed_override_paths,
            },
            "tasks": [gather_task] + cleanup_tasks + deploy_tasks,
        }
    ]

    playbook_yaml = yaml.dump(playbook, default_flow_style=False, sort_keys=False)
    inventory_json = generate_inventory(host_ip, ssh_port, ssh_key_path, ssh_user)

    return playbook_yaml, inventory_json
