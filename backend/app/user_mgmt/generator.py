from typing import Any


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
    tasks = []
    for u in users:
        username = _get(u, "username")
        state = _get(u, "state", "present")
        if state != "present":
            continue

        keys = _get(u, "authorized_keys", [])

        if keys:
            tasks.append(
                {
                    "name": f"Authorized keys for {username}",
                    "ansible.posix.authorized_key": {
                        "user": username,
                        "key": "\n".join(keys),
                        "exclusive": True,
                        "state": "present",
                    },
                }
            )
        else:
            tasks.append(
                {
                    "name": f"Authorized keys for {username}",
                    "ansible.posix.authorized_key": {
                        "user": username,
                        "key": "",
                        "exclusive": True,
                        "state": "absent",
                    },
                }
            )
    return tasks


def _sudoers_tasks(users: list) -> list[dict]:
    tasks = []
    for u in users:
        username = _get(u, "username")
        state = _get(u, "state", "present")
        sudo_rule = _get(u, "sudo_rule")

        if state == "present" and sudo_rule is not None:
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
        "name": "Barricade Linux User Management",
        "hosts": host_ip,
        "become": True,
        "gather_facts": False,
        "tasks": tasks,
    }
