"""Collect current systemd service states from remote hosts via SSH."""

import asyncssh
from dataclasses import dataclass


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
        async with asyncssh.connect(
            host_ip,
            port=ssh_port,
            username="root",
            client_keys=[private_key],
            known_hosts=None,
        ) as conn:
            for name in service_names:
                try:
                    # Check active state
                    active_result = await conn.run(
                        f"systemctl is-active {name}", check=False
                    )
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

                    active_state = (
                        "running" if active_stdout == "active" else "stopped"
                    )

                    # Check enabled state
                    enabled_result = await conn.run(
                        f"systemctl is-enabled {name}", check=False
                    )
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
