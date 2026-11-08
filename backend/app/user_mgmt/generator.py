from typing import Any

from app.ansible_runtime.playbook_yaml import Template


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _group_tasks(groups: list) -> list[dict]:
    tasks = []
    for g in groups:
        groupname = _get(g, "groupname")
        state = _get(g, "state", "present")
        gid = _get(g, "gid")

        task: dict[str, Any] = {
            "name": f"Manage group {groupname}",
            "ansible.builtin.group": {
                "name": groupname,
                "state": state,
            },
        }
        if gid is not None:
            task["ansible.builtin.group"]["gid"] = gid

        tasks.append(task)
    return tasks


def _user_tasks(users: list) -> list[dict]:
    tasks = []
    for u in users:
        username = _get(u, "username")
        state = _get(u, "state", "present")
        uid = _get(u, "uid")
        shell = _get(u, "shell", "/bin/bash")
        home_dir = _get(u, "home_dir")
        comment = _get(u, "comment")
        supplementary_groups = _get(u, "supplementary_groups", [])

        user_params: dict[str, Any] = {
            "name": username,
            "shell": shell,
            "state": state,
        }

        if uid is not None:
            user_params["uid"] = uid
        if home_dir is not None:
            user_params["home"] = home_dir
        if comment is not None:
            user_params["comment"] = comment
        if supplementary_groups:
            user_params["groups"] = supplementary_groups
            user_params["append"] = True

        if state == "absent":
            user_params["remove"] = True

        tasks.append(
            {
                "name": f"Manage user {username}",
                "ansible.builtin.user": user_params,
            }
        )
    return tasks


def _authorized_key_tasks(users: list) -> list[dict]:
    """Write each present user's ``~/.ssh/authorized_keys`` (exclusive).

    Uses ``ansible.builtin`` only (BUG-128): the image ships ansible-core,
    which has no ``ansible.posix.authorized_key``, so emitting that module
    made every users sync fail at parse time. The file is written whole,
    which is what ``exclusive: true`` did. A user without keys is left
    alone, as before (``authorized_key`` with an empty key and
    ``state: absent`` removed nothing).

    The home directory comes from ``getent`` on the target, the same source
    the collector reads it from. In check mode a user created earlier in the
    same play does not exist yet, so the lookup is allowed to miss and the
    key tasks are skipped for that user.
    """
    tasks = []
    for u in users:
        username = _get(u, "username")
        state = _get(u, "state", "present")
        if state != "present":
            continue

        keys = _get(u, "authorized_keys", []) or []
        if not keys:
            continue

        # Operator data stays out of the Jinja expressions: the username
        # reaches them only as a task variable.
        task_vars = {"labdog_key_user": username}
        passwd = "ansible_facts.getent_passwd[labdog_key_user]"  # nosec B105 - a fact path
        home = Template("{{ " + passwd + "[4] }}")
        exists = f"{passwd} is not none"
        tasks.extend(
            [
                {
                    "name": f"Look up home directory of {username}",
                    "ansible.builtin.getent": {
                        "database": "passwd",
                        "key": username,
                        "fail_key": False,
                    },
                },
                {
                    "name": f"SSH directory for {username}",
                    "ansible.builtin.file": {
                        "path": Template(home + "/.ssh"),
                        "state": "directory",
                        "owner": username,
                        "mode": "0700",
                    },
                    "vars": task_vars,
                    "when": exists,
                },
                {
                    "name": f"Authorized keys for {username}",
                    "ansible.builtin.copy": {
                        "content": "".join(f"{key}\n" for key in keys),
                        "dest": Template(home + "/.ssh/authorized_keys"),
                        "owner": username,
                        "mode": "0600",
                    },
                    "vars": task_vars,
                    "when": exists,
                },
            ]
        )
    return tasks


def _sudoers_tasks(users: list) -> list[dict]:
    from app.user_mgmt.constants import SUDO_FORBIDDEN_PATTERN

    tasks = []
    for u in users:
        username = _get(u, "username")
        state = _get(u, "state", "present")
        sudo_rule = _get(u, "sudo_rule")

        if state == "present" and sudo_rule is not None:
            # Second line of defence. The schema rejects newlines
            # (SEC-25), but a rule can also reach here from the GitOps
            # YAML importer and from rows written before that validator
            # existed. This is the point where a newline becomes a real
            # sudoers line, so it is the right place to be certain.
            if SUDO_FORBIDDEN_PATTERN.search(sudo_rule):
                raise ValueError(
                    f"sudo_rule for {username!r} contains forbidden characters; "
                    "refusing to write /etc/sudoers.d"
                )
            tasks.append(
                {
                    "name": f"Sudo rule for {username}",
                    "ansible.builtin.copy": {
                        "content": f"{username} {sudo_rule}\n",
                        "dest": f"/etc/sudoers.d/{username}",
                        "mode": "0440",
                        "validate": "visudo -cf %s",
                    },
                }
            )
        else:
            tasks.append(
                {
                    "name": f"Remove sudo rule for {username}",
                    "ansible.builtin.file": {
                        "path": f"/etc/sudoers.d/{username}",
                        "state": "absent",
                    },
                }
            )
    return tasks


def generate_user_playbook(
    host_ip: str,
    users: list,
    groups: list,
    ssh_key_path: str,
) -> dict:
    """
    Generate an Ansible playbook dict for Linux user/group management.

    Task order: groups -> users -> authorized_keys -> sudoers.
    Returns a single play dict (caller wraps in a list for ansible-runner).
    """
    # Split groups into present/absent — absent groups must come AFTER
    # absent users, since a user's primary group cannot be removed while
    # the user still exists.
    groups_present = [g for g in groups if _get(g, "state", "present") != "absent"]
    groups_absent = [g for g in groups if _get(g, "state", "present") == "absent"]

    tasks: list[dict] = []
    tasks.extend(_group_tasks(groups_present))
    tasks.extend(_user_tasks(users))
    tasks.extend(_authorized_key_tasks(users))
    tasks.extend(_sudoers_tasks(users))
    tasks.extend(_group_tasks(groups_absent))

    return {
        "name": "LabDog Linux User Management",
        "hosts": host_ip,
        "become": True,
        "gather_facts": False,
        "tasks": tasks,
    }
