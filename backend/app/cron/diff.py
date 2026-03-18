from dataclasses import dataclass, field


@dataclass
class CronDiff:
    jobs_to_add: list[str] = field(default_factory=list)
    jobs_to_remove: list[str] = field(default_factory=list)
    jobs_to_update: list[str] = field(default_factory=list)
    jobs_in_sync: list[str] = field(default_factory=list)


def diff_cron_jobs(desired: list[dict], actual: list[dict]) -> CronDiff:
    result = CronDiff()

    desired_by_key = {_key(d): d for d in desired}
    actual_by_key = {_key(a): a for a in actual}

    for key, desired_entry in desired_by_key.items():
        if key not in actual_by_key:
            result.jobs_to_add.append(_format_key(key))
            continue

        actual_entry = actual_by_key[key]
        if _needs_update(desired_entry, actual_entry):
            result.jobs_to_update.append(_format_key(key))
        else:
            result.jobs_in_sync.append(_format_key(key))

    for key in actual_by_key:
        if key not in desired_by_key:
            result.jobs_to_remove.append(_format_key(key))

    return result


def _key(entry: dict) -> tuple[str, str]:
    return (entry["name"], entry["user"])


def _format_key(key: tuple[str, str]) -> str:
    return f"{key[0]}|{key[1]}"


def _needs_update(desired: dict, actual: dict) -> bool:
    if desired.get("schedule") != actual.get("schedule"):
        return True
    if desired.get("command") != actual.get("command"):
        return True
    return False
