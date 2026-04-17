from typing import Any

from app.cron.validators import validate_cron_expression


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _serialize_state(state: Any) -> str:
    if hasattr(state, "value"):
        return state.value
    return str(state)


def _add_absent_cron_task(tasks: list, name: str, user: str) -> None:
    tasks.append(
        {
            "name": f"Remove cron job {name} for {user}",
            "ansible.builtin.cron": {
                "name": name,
                "user": user,
                "state": "absent",
            },
        }
    )


def _add_environment_tasks(tasks: list, name: str, user: str, environment: dict) -> None:
    for env_key, env_val in environment.items():
        tasks.append(
            {
                "name": f"Set env {env_key} for cron job {name}",
                "ansible.builtin.cron": {
                    "name": env_key,
                    "user": user,
                    "env": True,
                    "value": env_val,
                },
            }
        )


def _add_present_cron_task(
    tasks: list,
    name: str,
    user: str,
    schedule: str,
    command: str,
) -> None:
    minute, hour, dom, month, dow = validate_cron_expression(schedule)
    tasks.append(
        {
            "name": f"Manage cron job {name} for {user}",
            "ansible.builtin.cron": {
                "name": name,
                "user": user,
                "minute": minute,
                "hour": hour,
                "day": dom,
                "month": month,
                "weekday": dow,
                "job": command,
                "state": "present",
            },
        }
    )


def generate_cron_playbook(host_ip: str, cron_jobs: list, ssh_key_path: str) -> dict:
    tasks = []

    for job in cron_jobs:
        name = _get(job, "name")
        user = _get(job, "user")
        schedule = _get(job, "schedule")
        command = _get(job, "command")
        state = _serialize_state(_get(job, "state"))
        environment = _get(job, "environment", {})

        if state == "absent":
            _add_absent_cron_task(tasks, name, user)
        else:
            _add_environment_tasks(tasks, name, user, environment)
            _add_present_cron_task(tasks, name, user, schedule, command)

    return {
        "name": "Barricade Cron Job Management",
        "hosts": host_ip,
        "become": True,
        "gather_facts": False,
        "tasks": tasks,
    }
