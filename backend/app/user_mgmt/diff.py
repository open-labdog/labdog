from dataclasses import dataclass, field


@dataclass
class UserDiff:
    users_to_add: list[str] = field(default_factory=list)
    users_to_remove: list[str] = field(default_factory=list)
    users_to_update: list[str] = field(default_factory=list)
    users_in_sync: list[str] = field(default_factory=list)


@dataclass
class GroupDiff:
    groups_to_add: list[str] = field(default_factory=list)
    groups_to_remove: list[str] = field(default_factory=list)
    groups_to_update: list[str] = field(default_factory=list)
    groups_in_sync: list[str] = field(default_factory=list)


def diff_users(desired: list[dict], actual: list[dict]) -> UserDiff:
    result = UserDiff()

    desired_by_name = {d["username"]: d for d in desired}
    actual_by_name = {a["username"]: a for a in actual}

    for username, desired_entry in desired_by_name.items():
        if username not in actual_by_name:
            result.users_to_add.append(username)
            continue

        actual_entry = actual_by_name[username]

        if _user_needs_update(desired_entry, actual_entry):
            result.users_to_update.append(username)
        else:
            result.users_in_sync.append(username)

    for username in actual_by_name:
        if username not in desired_by_name:
            result.users_to_remove.append(username)

    return result


def _user_needs_update(desired: dict, actual: dict) -> bool:
    if actual["state"] == "absent":
        return desired.get("state", "present") == "present"

    if desired.get("state", "present") == "absent":
        return actual["state"] == "present"

    if desired.get("shell") is not None and desired["shell"] != actual.get("shell"):
        return True

    desired_keys = sorted(desired.get("authorized_keys", []))
    actual_keys = sorted(actual.get("authorized_keys", []))
    if desired_keys != actual_keys:
        return True

    if desired.get("sudo_rule") != actual.get("sudo_rule"):
        return True

    desired_groups = sorted(desired.get("supplementary_groups", []))
    actual_groups = sorted(actual.get("supplementary_groups", []))
    if desired_groups != actual_groups:
        return True

    return False


def diff_groups(desired: list[dict], actual: list[dict]) -> GroupDiff:
    result = GroupDiff()

    desired_by_name = {d["groupname"]: d for d in desired}
    actual_by_name = {a["groupname"]: a for a in actual}

    for groupname, desired_entry in desired_by_name.items():
        if groupname not in actual_by_name:
            result.groups_to_add.append(groupname)
            continue

        actual_entry = actual_by_name[groupname]

        if _group_needs_update(desired_entry, actual_entry):
            result.groups_to_update.append(groupname)
        else:
            result.groups_in_sync.append(groupname)

    for groupname in actual_by_name:
        if groupname not in desired_by_name:
            result.groups_to_remove.append(groupname)

    return result


def _group_needs_update(desired: dict, actual: dict) -> bool:
    if actual["state"] != desired.get("state", "present"):
        return True

    if desired.get("gid") is not None and desired["gid"] != actual.get("gid"):
        return True

    return False
