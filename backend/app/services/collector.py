"""Collect current systemd service states from remote hosts via SSH."""

import asyncio
import shlex

import asyncssh
from dataclasses import dataclass

from app.ssh_utils import ssh_connect


@dataclass
class ServiceCurrentState:
    service_name: str
    active_state: str  # "running", "stopped", "error"
    enabled: bool


async def collect_service_states(
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    service_names: list[str],
    ssh_user: str = "root",
) -> list[ServiceCurrentState]:
    """
    SSH into host and check service states via systemctl.

    For each service:
      - `systemctl is-active {name}` → "active" = running, everything else = stopped
      - `systemctl is-enabled {name}` → "enabled" = True, "disabled" = False
      - Exit code 4 = service not found → mark as error
    """
    results = []

    try:
        private_key = asyncssh.import_private_key(private_key_pem)
        async with ssh_connect(
            host_ip,
            port=ssh_port,
            username=ssh_user,
            client_keys=[private_key],
        ) as conn:
            for name in service_names:
                try:
                    # Check active state
                    active_result = await conn.run(f"systemctl is-active {shlex.quote(name)}", check=False)
                    active_stdout = active_result.stdout.strip()
                    exit_code = active_result.exit_status

                    if exit_code == 4:
                        # Service unit not found
                        results.append(
                            ServiceCurrentState(
                                service_name=name,
                                active_state="error",
                                enabled=False,
                            )
                        )
                        continue

                    active_state = "running" if active_stdout == "active" else "stopped"

                    # Check enabled state
                    enabled_result = await conn.run(f"systemctl is-enabled {shlex.quote(name)}", check=False)
                    enabled_stdout = enabled_result.stdout.strip()
                    enabled = enabled_stdout == "enabled"

                    results.append(
                        ServiceCurrentState(
                            service_name=name,
                            active_state=active_state,
                            enabled=enabled,
                        )
                    )
                except Exception:
                    results.append(
                        ServiceCurrentState(
                            service_name=name,
                            active_state="error",
                            enabled=False,
                        )
                    )
    except Exception:
        # Connection failure — all services in error state
        for name in service_names:
            results.append(
                ServiceCurrentState(
                    service_name=name,
                    active_state="error",
                    enabled=False,
                )
            )

    return results


async def list_all_services(
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    ssh_user: str = "root",
) -> list[dict]:
    """
    SSH into host and list all systemd services via systemctl.

    Returns a list of dicts with keys: unit, load_state, active_state, sub_state, description.
    Returns empty list on any error (connection failure, timeout, parse error).
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
                result = await conn.run(
                    "systemctl list-units --type=service --all --no-pager --plain",
                    check=False,
                )
                services = []
                for line in (result.stdout or "").splitlines():
                    # Skip empty lines
                    if not line.strip():
                        continue
                    # Skip header line and summary lines
                    if line.startswith("UNIT") or "loaded units listed" in line:
                        continue
                    # Skip bullet/dot-style lines (e.g. "● service.service ...")
                    stripped = line.strip()
                    if stripped.startswith("●"):
                        continue
                    parts = stripped.split(maxsplit=4)
                    if len(parts) < 4:
                        continue
                    if not parts[0].endswith(".service"):
                        continue
                    unit = parts[0].removesuffix(".service")
                    load_state = parts[1]
                    active_state = parts[2]
                    sub_state = parts[3]
                    description = parts[4] if len(parts) > 4 else ""
                    services.append(
                        {
                            "unit": unit,
                            "load_state": load_state,
                            "active_state": active_state,
                            "sub_state": sub_state,
                            "description": description,
                        }
                    )
                return services

        return await asyncio.wait_for(_run(), timeout=30.0)
    except Exception:
        return []


async def execute_service_command(
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    service_name: str,
    action: str,
    ssh_user: str = "root",
) -> dict:
    """
    SSH into host and execute a systemctl action on a service.

    action must be one of: "start", "stop", "restart".
    Returns a dict with keys: success, exit_code, stdout, stderr.
    """
    if action not in ("start", "stop", "restart"):
        raise ValueError(f"Invalid action {action!r}. Must be one of: start, stop, restart")

    cmd = f"systemctl {action} {shlex.quote(service_name)}"

    try:
        private_key = asyncssh.import_private_key(private_key_pem)

        async def _run() -> dict:
            async with ssh_connect(
                host_ip,
                port=ssh_port,
                username=ssh_user,
                client_keys=[private_key],
            ) as conn:
                result = await conn.run(cmd, check=False)
                return {
                    "success": result.exit_status == 0,
                    "exit_code": result.exit_status,
                    "stdout": result.stdout or "",
                    "stderr": result.stderr or "",
                }

        return await asyncio.wait_for(_run(), timeout=30.0)
    except asyncio.TimeoutError:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "Command timed out after 30s",
        }
    except Exception as e:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
        }


async def collect_unit_file_content(
    host_ip: str,
    ssh_port: int,
    private_key_pem: str,
    service_name: str,
    deploy_mode: str,
    ssh_user: str = "root",
) -> str | None:
    quoted = shlex.quote(service_name)
    if deploy_mode == "override":
        cmd = f"cat /etc/systemd/system/{quoted}.service.d/barricade.conf"
    else:
        cmd = f"cat /etc/systemd/system/{quoted}.service"

    try:
        private_key = asyncssh.import_private_key(private_key_pem)

        async def _run() -> str | None:
            async with ssh_connect(
                host_ip,
                port=ssh_port,
                username=ssh_user,
                client_keys=[private_key],
            ) as conn:
                result = await conn.run(cmd, check=False)
                if result.exit_status != 0:
                    return None
                return result.stdout or ""

        return await asyncio.wait_for(_run(), timeout=30.0)
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None
