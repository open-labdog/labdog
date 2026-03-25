"""Collect current package states from remote hosts via SSH (dpkg/rpm)."""

import asyncio
from typing import Optional

import asyncssh

from app.ssh_utils import ssh_connect


async def collect_package_states(
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    package_names: list[str],
    ssh_user: str = "root",
) -> list[dict]:
    """Return [{"name": str, "state": "present"|"absent", "version": str|None}]."""
    if not package_names:
        return []

    results: list[dict] = []

    try:
        private_key = asyncssh.import_private_key(private_key_pem)

        async def _run() -> list[dict]:
            async with ssh_connect(
                host_ip,
                port=ssh_port,
                username=ssh_user,
                client_keys=[private_key],
            ) as conn:
                dpkg_check = await conn.run("which dpkg 2>/dev/null", check=False)
                rpm_check = await conn.run("which rpm 2>/dev/null", check=False)

                use_dpkg = dpkg_check.exit_status == 0
                use_rpm = rpm_check.exit_status == 0

                for pkg_name in package_names:
                    try:
                        if use_dpkg:
                            result = await conn.run(
                                f"dpkg -l {pkg_name} 2>/dev/null | grep -E '^ii|^rc|^un' | head -1",
                                check=False,
                            )
                            state, version = _parse_dpkg_output(
                                result.stdout.strip(), pkg_name
                            )
                        elif use_rpm:
                            result = await conn.run(
                                f"rpm -q {pkg_name} 2>/dev/null",
                                check=False,
                            )
                            state, version = _parse_rpm_output(
                                result.stdout.strip(), result.exit_status, pkg_name
                            )
                        else:
                            state, version = "absent", None

                        results.append(
                            {"name": pkg_name, "state": state, "version": version}
                        )
                    except Exception:
                        results.append(
                            {"name": pkg_name, "state": "absent", "version": None}
                        )

            return results

        return await asyncio.wait_for(_run(), timeout=30.0)

    except Exception:
        for pkg_name in package_names:
            results.append({"name": pkg_name, "state": "absent", "version": None})
        return results


def _parse_dpkg_output(output: str, pkg_name: str) -> tuple[str, Optional[str]]:
    if not output:
        return "absent", None
    parts = output.split()
    if len(parts) < 3:
        return "absent", None
    # dpkg status: "ii" = installed, "rc" = removed-config-remains, "un" = unknown
    status_char = parts[0]
    if status_char == "ii":
        version = parts[2] if len(parts) > 2 else None
        return "present", version
    return "absent", None


def _parse_rpm_output(
    output: str, exit_status: int, pkg_name: str
) -> tuple[str, Optional[str]]:
    if exit_status != 0 or not output or "not installed" in output:
        return "absent", None
    try:
        # rpm -q format: "name-version-release.arch" e.g. "nginx-1.24.0-1.el9.x86_64"
        prefix = f"{pkg_name}-"
        if output.startswith(prefix):
            version_part = output[len(prefix):]
            return "present", version_part.rsplit(".", 1)[0]
        return "present", output.strip()
    except Exception:
        return "present", None


async def collect_repo_sources(
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    ssh_user: str = "root",
) -> list[dict]:
    """Collect configured package repository sources from a remote host.

    Returns list of {"name": str, "type": "apt"|"yum", "url": str,
    "distribution": str|None, "components": str|None, "enabled": bool}.
    """
    try:
        private_key = asyncssh.import_private_key(private_key_pem)

        async def _run() -> list[dict]:
            async with ssh_connect(
                host_ip,
                port=ssh_port,
                username=ssh_user,
                client_keys=[private_key],
            ) as conn:
                repos: list[dict] = []

                # Try APT
                apt_result = await conn.run(
                    "cat /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null || true",
                    check=False,
                )
                if apt_result.stdout.strip():
                    repos.extend(_parse_apt_sources(apt_result.stdout))

                # Try YUM/DNF
                if not repos:
                    yum_result = await conn.run(
                        "cat /etc/yum.repos.d/*.repo 2>/dev/null || true",
                        check=False,
                    )
                    if yum_result.stdout.strip():
                        repos.extend(_parse_yum_repos(yum_result.stdout))

                return repos

        return await asyncio.wait_for(_run(), timeout=15.0)
    except Exception:
        return []


def _parse_apt_sources(output: str) -> list[dict]:
    """Parse APT sources.list format into repo dicts."""
    repos = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: deb [options] URL distribution [component ...]
        # or:    deb-src [options] URL distribution [component ...]
        parts = line.split()
        if not parts or parts[0] not in ("deb", "deb-src"):
            continue
        idx = 1
        # Skip [options] like [arch=amd64 signed-by=...]
        if idx < len(parts) and parts[idx].startswith("["):
            while idx < len(parts) and "]" not in parts[idx]:
                idx += 1
            idx += 1
        if idx >= len(parts):
            continue
        url = parts[idx]
        distribution = parts[idx + 1] if idx + 1 < len(parts) else None
        components = " ".join(parts[idx + 2:]) if idx + 2 < len(parts) else None
        repos.append({
            "name": distribution or url.split("/")[-1] or url,
            "type": "apt",
            "url": url,
            "distribution": distribution,
            "components": components,
            "enabled": True,
        })
    return repos


def _parse_yum_repos(output: str) -> list[dict]:
    """Parse YUM .repo INI format into repo dicts."""
    repos = []
    current: dict | None = None
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            if current and current.get("url"):
                repos.append(current)
            current = {
                "name": line[1:-1],
                "type": "yum",
                "url": "",
                "distribution": None,
                "components": None,
                "enabled": True,
            }
        elif current and "=" in line:
            key, _, value = line.partition("=")
            key = key.strip().lower()
            value = value.strip()
            if key == "baseurl":
                current["url"] = value
            elif key == "name" and current["name"] == current.get("_id", current["name"]):
                current["name"] = value
            elif key == "enabled":
                current["enabled"] = value != "0"
    if current and current.get("url"):
        repos.append(current)
    return repos
