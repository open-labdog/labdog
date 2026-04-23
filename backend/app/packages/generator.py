from app.ansible.inventory import generate_inventory


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
    for pkg in packages:
        state = pkg.get("state", "present")
        name = pkg["package_name"]
        version = pkg.get("version")

        if state == "absent":
            tasks.append(
                {
                    "name": f"Remove package: {name}",
                    "ansible.builtin.package": {
                        "name": name,
                        "state": "absent",
                    },
                }
            )
        elif state == "latest":
            tasks.append(
                {
                    "name": f"Ensure latest: {name}",
                    "ansible.builtin.package": {
                        "name": name,
                        "state": "latest",
                    },
                }
            )
        else:
            if version:
                pkg_name_versioned = _format_package_name_with_version(
                    name,
                    version,
                    pkg.get("package_manager", "auto"),
                )
                tasks.append(
                    {
                        "name": f"Install package: {name} version {version}",
                        "ansible.builtin.package": {
                            "name": pkg_name_versioned,
                            "state": "present",
                        },
                    }
                )
            else:
                tasks.append(
                    {
                        "name": f"Ensure present: {name}",
                        "ansible.builtin.package": {
                            "name": name,
                            "state": "present",
                        },
                    }
                )

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
            "gather_facts": True,
            "tasks": tasks,
        }
    ]

    inventory = generate_inventory(host_ip, ssh_port, ssh_key_path, ssh_user)

    return {"playbook": playbook, "inventory": inventory}


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
