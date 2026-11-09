"""Collect current package states from remote hosts via SSH (dpkg/rpm)."""

import asyncio
import re
import shlex
from typing import TYPE_CHECKING

import asyncssh

from app.ssh_utils import ssh_connect_host

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.host import Host


async def collect_package_states(
    host: "Host",
    db: "AsyncSession",
    private_key_pem: str,
    package_names: list[str],
) -> list[dict]:
    """Return [{"name", "state": "present"|"absent", "version", "hold": bool}].

    Three remote commands whatever the number of packages (BUG-158): which
    package manager there is, one query for every package, and the held
    list. It used to run one ``dpkg -l`` pipeline per package, a round trip
    each: 241 ms against 10 ms for 20 packages over one connection, before
    any network latency.
    """
    if not package_names:
        return []

    try:
        private_key = asyncssh.import_private_key(private_key_pem)

        async def _run() -> list[dict]:
            async with ssh_connect_host(host, db, client_keys=[private_key]) as conn:
                manager = await _package_manager(conn)
                installed = await _query_installed(conn, manager, package_names)
                held = await _collect_held(conn, package_names, manager == "dpkg", manager == "rpm")
            return [
                {
                    "name": name,
                    "state": "present" if name in installed else "absent",
                    "version": installed.get(name),
                    "hold": name in held,
                }
                for name in package_names
            ]

        return await asyncio.wait_for(_run(), timeout=30.0)

    except Exception:
        return [
            {"name": name, "state": "absent", "version": None, "hold": False}
            for name in package_names
        ]


async def _package_manager(conn) -> str | None:
    """``"dpkg"``, ``"rpm"`` or ``None``; dpkg wins where both are installed."""
    result = await conn.run(
        "if command -v dpkg-query >/dev/null 2>&1; then echo dpkg; "
        "elif command -v rpm >/dev/null 2>&1; then echo rpm; fi",
        check=False,
    )
    manager = (result.stdout or "").strip()
    return manager if manager in ("dpkg", "rpm") else None


#: One line per installed package: name, architecture, version, status.
_DPKG_FORMAT = "${Package}\\t${Architecture}\\t${Version}\\t${db:Status-Abbrev}\\n"
_RPM_FORMAT = "%{NAME}\\t%{ARCH}\\t%{VERSION}-%{RELEASE}\\n"


async def _query_installed(conn, manager: str | None, package_names: list[str]) -> dict[str, str]:
    """Map each installed package in ``package_names`` to its version.

    One query for every name. A name the package database does not know is
    missing from the output (``dpkg-query`` and ``rpm`` both say so on
    stderr and exit non-zero, which is not a failure here). Output lines
    are matched back to the requested names by ``name``, ``name:arch``
    (dpkg) and ``name.arch`` (rpm), so an architecture-qualified request
    finds its package.
    """
    if manager is None:
        return {}
    names = " ".join(shlex.quote(n) for n in package_names)
    if manager == "dpkg":
        command = f"dpkg-query -W -f={shlex.quote(_DPKG_FORMAT)} {names} 2>/dev/null"
    else:
        command = f"rpm -q --qf {shlex.quote(_RPM_FORMAT)} {names} 2>/dev/null"
    result = await conn.run(command, check=False)
    parse = _parse_dpkg_query if manager == "dpkg" else _parse_rpm_query
    found = parse(result.stdout or "")
    return {name: found[name] for name in package_names if name in found}


def _parse_dpkg_query(output: str) -> dict[str, str]:
    """``dpkg-query -W`` lines → ``{name: version}`` for installed packages.

    Installed means the status abbreviation's second letter is ``i``:
    ``ii`` and also ``hi``, a package held with ``apt-mark hold``. The
    ``dpkg -l | grep '^ii'`` this replaces read a held package as absent.
    """
    installed: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, arch, version, status = parts[0], parts[1], parts[2], parts[3]
        if len(status) < 2 or status[1] != "i":
            continue
        for key in (name, f"{name}:{arch}"):
            installed.setdefault(key, version)
    return installed


def _parse_rpm_query(output: str) -> dict[str, str]:
    """``rpm -q --qf`` lines → ``{name: version-release}``.

    ``rpm`` prints ``package <name> is not installed`` in place of a missing
    package, which has no tabs and is skipped.
    """
    installed: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, arch, version = parts[0], parts[1], parts[2]
        for key in (name, f"{name}.{arch}"):
            installed.setdefault(key, version)
    return installed


async def _collect_held(conn, package_names: list[str], use_dpkg: bool, use_rpm: bool) -> set[str]:
    """Return the subset of package_names whose install is held/version-locked.

    apt: ``apt-mark showhold`` lists held package names verbatim.
    dnf: ``dnf versionlock list`` lists ``[epoch:]name-version...`` entries; a
    package is locked when an entry's name component matches exactly (the
    ``name-<digit>`` boundary avoids matching e.g. ``foo`` against ``foo-libs``).
    On any error the held set is empty (no hold drift asserted).
    """
    try:
        if use_dpkg:
            r = await conn.run("apt-mark showhold 2>/dev/null", check=False)
            if r.exit_status != 0:
                return set()
            held_all = {ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()}
            return {n for n in package_names if n in held_all}
        if use_rpm:
            r = await conn.run("dnf versionlock list 2>/dev/null", check=False)
            if r.exit_status != 0:
                return set()
            lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
            return {
                n
                for n in package_names
                if any(re.match(rf"^(?:\d+:)?{re.escape(n)}-\d", ln) for ln in lines)
            }
    except Exception:
        return set()
    return set()


async def collect_repo_sources(
    host: "Host",
    db: "AsyncSession",
    private_key_pem: str,
) -> list[dict]:
    """Collect configured package repository sources from a remote host.

    Returns list of {"name": str, "type": "apt"|"yum", "url": str,
    "distribution": str|None, "components": str|None, "enabled": bool}.
    """
    try:
        private_key = asyncssh.import_private_key(private_key_pem)

        async def _run() -> list[dict]:
            async with ssh_connect_host(host, db, client_keys=[private_key]) as conn:
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
        components = " ".join(parts[idx + 2 :]) if idx + 2 < len(parts) else None
        repos.append(
            {
                "name": distribution or url.split("/")[-1] or url,
                "type": "apt",
                "url": url,
                "distribution": distribution,
                "components": components,
                "enabled": True,
            }
        )
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
