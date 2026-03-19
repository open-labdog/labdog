"""Collect current package states from remote hosts via SSH (dpkg/rpm)."""

import asyncio
from typing import Optional

import asyncssh


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
            async with asyncssh.connect(
                host_ip,
                port=ssh_port,
                username=ssh_user,
                client_keys=[private_key],
                known_hosts=None,
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
