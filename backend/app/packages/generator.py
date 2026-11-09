from app.ansible_runtime.inventory import generate_inventory


def generate_package_playbook(
    host_ip: str,
    packages: list[dict],
    repos: list[dict],
    ssh_key_path: str,
    ssh_port: int = 22,
    ssh_user: str = "root",
) -> dict:
    """
    Generate an Ansible playbook dict for package and repository management.

    Ordering: repos are configured FIRST, then packages (dependency ordering).

    Returns a dict with 'playbook' and 'inventory' keys for ansible-runner.
    """
    tasks: list[dict] = []

    # STEP 1: Repository tasks (must come before package installs)
    for repo in repos:
        if repo.get("state", "present") == "absent":
            if repo.get("repo_type") == "apt":
                tasks.append(
                    {
                        "name": f"Remove apt repository: {repo['name']}",
                        "ansible.builtin.apt_repository": {
                            "repo": _build_apt_repo_string(repo),
                            "state": "absent",
                            "update_cache": True,
                        },
                        "when": "ansible_facts['os_family'] == 'Debian'",
                    }
                )
            else:
                tasks.append(
                    {
                        "name": f"Remove yum repository: {repo['name']}",
                        "ansible.builtin.yum_repository": {
                            "name": repo["name"],
                            "state": "absent",
                        },
                        "when": "ansible_facts['os_family'] == 'RedHat'",
                    }
                )
        else:
            if repo.get("repo_type") == "apt":
                tasks.append(
                    {
                        "name": f"Add apt repository: {repo['name']}",
                        "ansible.builtin.apt_repository": {
                            "repo": _build_apt_repo_string(repo),
                            "state": "present",
                            "update_cache": True,
                        },
                        "when": "ansible_facts['os_family'] == 'Debian'",
                    }
                )
            else:
                yum_repo_config: dict = {
                    "name": repo["name"],
                    "baseurl": repo["url"],
                    "state": "present",
                    "enabled": True,
                }
                if repo.get("key_url"):
                    yum_repo_config["gpgkey"] = repo["key_url"]
                    yum_repo_config["gpgcheck"] = True
                tasks.append(
                    {
                        "name": f"Add yum repository: {repo['name']}",
                        "ansible.builtin.yum_repository": yum_repo_config,
                        "when": "ansible_facts['os_family'] == 'RedHat'",
                    }
                )

    # STEP 2: Package tasks (after repos are configured)
    tasks.extend(_package_tasks(packages))

    # STEP 3: Hold/unhold tasks (after packages are installed)
    hold_pkgs = [
        p["package_name"] for p in packages if p.get("hold") and p.get("state") != "absent"
    ]
    unhold_pkgs = [
        p["package_name"] for p in packages if not p.get("hold") and p.get("state") != "absent"
    ]

    if hold_pkgs:
        tasks.append(
            {
                "name": f"Hold packages: {', '.join(hold_pkgs)}",
                "ansible.builtin.command": f"apt-mark hold {' '.join(hold_pkgs)}",
                "when": "ansible_facts['os_family'] == 'Debian'",
                "changed_when": True,
            }
        )
        tasks.append(
            {
                "name": f"Hold packages (dnf): {', '.join(hold_pkgs)}",
                "ansible.builtin.command": f"dnf versionlock add {' '.join(hold_pkgs)}",
                "when": "ansible_facts['os_family'] == 'RedHat'",
                "changed_when": True,
            }
        )

    if unhold_pkgs:
        tasks.append(
            {
                "name": f"Unhold packages: {', '.join(unhold_pkgs)}",
                "ansible.builtin.command": f"apt-mark unhold {' '.join(unhold_pkgs)}",
                "when": "ansible_facts['os_family'] == 'Debian'",
                "changed_when": False,
            }
        )
        tasks.append(
            {
                "name": f"Unhold packages (dnf): {', '.join(unhold_pkgs)}",
                "ansible.builtin.command": f"dnf versionlock delete {' '.join(unhold_pkgs)}",
                "when": "ansible_facts['os_family'] == 'RedHat'",
                "failed_when": False,
                "changed_when": False,
            }
        )

    playbook = [
        {
            "name": "LabDog Package Management",
            "hosts": "all",
            "become": True,
            # BUG-157: only what the tasks read. ``os_family`` for the
            # repository and hold conditions, and ``pkg_mgr``, which
            # ``ansible.builtin.package`` would otherwise look up with a
            # setup run of its own. Gathering everything cost about 0.35 s
            # more per sync.
            "gather_facts": True,
            "gather_subset": ["!all", "!min", "distribution", "pkg_mgr"],
            "tasks": tasks,
        }
    ]

    inventory = generate_inventory(host_ip, ssh_port, ssh_key_path, ssh_user)

    return {"playbook": playbook, "inventory": inventory}


#: Package states in the order their tasks run: removals first, so that a
#: package replacing a removed one (an MTA, say) is installed after it.
_STATE_ORDER = (
    ("absent", "Remove"),
    ("present", "Ensure present"),
    ("latest", "Ensure latest"),
)


def _package_tasks(packages: list[dict]) -> list[dict]:
    """One package-manager call per target state, not one per package.

    BUG-157: 20 packages were 20 ``ansible.builtin.package`` tasks, each a
    module run of its own that started the package manager and read its
    cache again. Packages that want the same state now go in one task with
    ``name:`` as a list, which apt and dnf resolve in one transaction.

    One bad name fails the whole list, so a list task ignores its failure
    and is followed by one task per package that runs only when it failed:
    the good packages are still installed and the bad one fails the
    module, as before. (An ignored failure does not count against the
    module, BUG-167.) A state with one package keeps the single task.
    """
    names: dict[str, list[tuple[str, str]]] = {state: [] for state, _ in _STATE_ORDER}
    for pkg in packages:
        state = pkg.get("state", "present")
        name = pkg["package_name"]
        version = pkg.get("version")
        if state not in ("absent", "latest"):
            state = "present"
        if state == "present" and version:
            spec = _format_package_name_with_version(
                name, version, pkg.get("package_manager", "auto")
            )
            label = f"{name} version {version}"
        else:
            spec, label = name, name
        names[state].append((spec, label))

    tasks: list[dict] = []
    for state, verb in _STATE_ORDER:
        group = names[state]
        if len(group) == 1:
            spec, label = group[0]
            tasks.append(_package_task(f"{verb}: {label}", spec, state))
        elif group:
            register = f"labdog_packages_{state}"
            listed = _package_task(
                f"{verb}: {', '.join(label for _, label in group)}",
                [spec for spec, _ in group],
                state,
            )
            listed["register"] = register
            listed["ignore_errors"] = True
            tasks.append(listed)
            for spec, label in group:
                one = _package_task(f"{verb} one by one: {label}", spec, state)
                one["when"] = f"{register} is failed"
                tasks.append(one)
    return tasks


def _package_task(name: str, spec: str | list[str], state: str) -> dict:
    return {"name": name, "ansible.builtin.package": {"name": spec, "state": state}}


def _build_apt_repo_string(repo: dict) -> str:
    distribution = repo.get("distribution", "")
    components = repo.get("components", "main")
    url = repo["url"]
    return f"deb {url} {distribution} {components}".strip()


def _format_package_name_with_version(
    name: str,
    version: str,
    package_manager: str,
) -> str:
    if package_manager in ("yum", "dnf"):
        return f"{name}-{version}"
    return f"{name}={version}"
