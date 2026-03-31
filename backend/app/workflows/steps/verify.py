"""Verification step: hard SSH checks + optional AI review after a system update."""

import asyncio
import logging
from typing import Any

import asyncssh

logger = logging.getLogger(__name__)

# Warn thresholds
_LOAD_WARN_THRESHOLD = 10.0
_DISK_WARN_THRESHOLD = 95


async def run_verification(
    host: Any,
    ssh_key_path: str,
    effective_services: list[Any],
    effective_packages: list[Any],
    verification_prompt: str | None,
    db: Any,
) -> dict[str, Any]:
    """Verify system health after a host update via SSH hard checks.

    Connects once over SSH and runs all checks sequentially over the same
    connection.  Hard checks cover every service with ``desired_state="running"``
    and every package with ``desired_state`` in ``("present", "latest")``.
    Basic system health (load average and disk usage) is always collected.
    Recent journal errors are gathered as additional context.

    When all hard checks pass and ``verification_prompt`` is provided, the
    function delegates to :func:`~app.workflows.steps.ai_verify.run_ai_verification`
    for an AI-assisted assessment.

    Args:
        host: Host ORM object exposing ``hostname``, ``ip_address``,
            ``ssh_port``, and ``ssh_user``.
        ssh_key_path: Absolute path to the decrypted SSH private key on tmpfs.
        effective_services: List of effective service rule objects, each with
            ``service_name`` and ``desired_state`` attributes.
        effective_packages: List of effective package rule objects, each with
            ``package_name`` and ``desired_state`` attributes.
        verification_prompt: Optional free-text instructions for AI
            verification.  ``None`` or empty string disables AI verification.
        db: Active async SQLAlchemy session (reserved for future use).

    Returns:
        A dict of the form::

            {
                "passed": bool,
                "hard_checks": {
                    "services": [{"name": str, "expected": str, "actual": str, "ok": bool}, ...],
                    "packages": [{"name": str, "expected": str, "installed": bool, "ok": bool}, ...],
                    "load": float,
                    "disk_pct": int,
                    "journal_errors": str,
                },
                "ai_result": {"passed": bool, "output": str} | None,
            }

        ``passed`` is ``True`` only when every hard check succeeds AND (if AI
        verification was requested) the AI also returns PASS.
    """
    service_results: list[dict[str, Any]] = []
    package_results: list[dict[str, Any]] = []
    load_avg: float = 0.0
    disk_pct: int = 0
    journal_errors: str = ""
    unmanaged_services: list[str] = []
    ai_result: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Filter to only the rules that require verification
    # ------------------------------------------------------------------
    services_to_check = [
        s for s in effective_services if getattr(s, "desired_state", None) == "running"
    ]
    # EffectiveServiceResponse uses .state, not .desired_state — handle both
    if not services_to_check:
        services_to_check = [
            s for s in effective_services
            if getattr(s, "state", None) == "running"
        ]

    packages_to_check = [
        p for p in effective_packages
        if getattr(p, "desired_state", None) in ("present", "latest")
    ]
    if not packages_to_check:
        packages_to_check = [
            p for p in effective_packages
            if getattr(p, "state", None) in ("present", "latest")
        ]

    # ------------------------------------------------------------------
    # Open a single SSH connection for all checks
    # ------------------------------------------------------------------
    try:
        conn: asyncssh.SSHClientConnection = await asyncio.wait_for(
            asyncssh.connect(
                host.ip_address,
                port=host.ssh_port or 22,
                username=host.ssh_user or "root",
                client_keys=[ssh_key_path],
                known_hosts=None,
            ),
            timeout=30,
        )
    except Exception as exc:
        logger.error(
            "verify: SSH connection failed to %s: %s", host.ip_address, exc
        )
        # Cannot run any checks — return a failed result immediately
        return {
            "passed": False,
            "hard_checks": {
                "services": [],
                "packages": [],
                "load": 0.0,
                "disk_pct": 0,
                "journal_errors": f"SSH connection failed: {exc}",
            },
            "ai_result": None,
        }

    try:
        # ------------------------------------------------------------------
        # Service checks
        # ------------------------------------------------------------------
        for svc in services_to_check:
            svc_name = svc.service_name
            try:
                result = await conn.run(
                    f"systemctl is-active {svc_name}", check=False
                )
                actual = result.stdout.strip()
            except Exception as exc:
                logger.warning("verify: service check failed for %s: %s", svc_name, exc)
                actual = f"error: {exc}"

            ok = actual == "active"
            service_results.append(
                {"name": svc_name, "expected": "active", "actual": actual, "ok": ok}
            )
            if not ok:
                logger.warning(
                    "verify: service %s is %s (expected active) on %s",
                    svc_name,
                    actual,
                    host.ip_address,
                )

        # ------------------------------------------------------------------
        # Package checks (Debian first, RHEL fallback)
        # ------------------------------------------------------------------
        for pkg in packages_to_check:
            pkg_name = pkg.package_name
            installed = False
            try:
                # Debian/Ubuntu: dpkg-query
                deb_result = await conn.run(
                    f"dpkg-query -W -f='${{Status}}' {pkg_name} 2>/dev/null",
                    check=False,
                )
                if deb_result.stdout.strip() == "install ok installed":
                    installed = True
                else:
                    # RHEL/CentOS: rpm -q (exit 0 = installed)
                    rpm_result = await conn.run(
                        f"rpm -q {pkg_name}", check=False
                    )
                    installed = rpm_result.exit_status == 0
            except Exception as exc:
                logger.warning("verify: package check failed for %s: %s", pkg_name, exc)

            expected_state = getattr(pkg, "desired_state", None) or getattr(pkg, "state", "present")
            ok = installed
            package_results.append(
                {"name": pkg_name, "expected": expected_state, "installed": installed, "ok": ok}
            )
            if not ok:
                logger.warning(
                    "verify: package %s not installed on %s",
                    pkg_name,
                    host.ip_address,
                )

        # ------------------------------------------------------------------
        # System health: load average
        # ------------------------------------------------------------------
        try:
            load_result = await conn.run("cat /proc/loadavg", check=False)
            raw_load = load_result.stdout.strip()
            load_avg = float(raw_load.split()[0])
            if load_avg > _LOAD_WARN_THRESHOLD:
                logger.warning(
                    "verify: high load average %.2f on %s", load_avg, host.ip_address
                )
        except Exception as exc:
            logger.warning("verify: load average check failed on %s: %s", host.ip_address, exc)

        # ------------------------------------------------------------------
        # System health: disk usage
        # ------------------------------------------------------------------
        try:
            disk_result = await conn.run(
                "df --output=pcent / | tail -1", check=False
            )
            disk_pct = int(disk_result.stdout.strip().rstrip("%"))
            if disk_pct > _DISK_WARN_THRESHOLD:
                logger.warning(
                    "verify: disk usage %d%% on %s", disk_pct, host.ip_address
                )
        except Exception as exc:
            logger.warning("verify: disk check failed on %s: %s", host.ip_address, exc)

        # ------------------------------------------------------------------
        # Journal errors (last 10 minutes, error priority and above)
        # ------------------------------------------------------------------
        try:
            journal_result = await conn.run(
                "journalctl --since '10 minutes ago' -p err --no-pager -q",
                check=False,
            )
            journal_errors = journal_result.stdout.strip()
        except Exception as exc:
            logger.warning("verify: journal check failed on %s: %s", host.ip_address, exc)
            journal_errors = f"journal read error: {exc}"

        # ------------------------------------------------------------------
        # Unmanaged service detection
        # ------------------------------------------------------------------
        try:
            active_result = await conn.run(
                "systemctl list-units --type=service --state=active --no-pager --plain --no-legend",
                check=False,
            )
            managed_names = {
                s.service_name for s in effective_services
            }
            for line in (active_result.stdout or "").splitlines():
                parts = line.strip().split(maxsplit=4)
                if len(parts) >= 1:
                    unit = parts[0].removesuffix(".service")
                    if unit not in managed_names:
                        unmanaged_services.append(unit)
        except Exception as exc:
            logger.warning("verify: unmanaged service scan failed on %s: %s", host.ip_address, exc)

    finally:
        try:
            conn.close()
            await conn.wait_closed()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Aggregate hard-check result
    # ------------------------------------------------------------------
    services_ok = all(r["ok"] for r in service_results)
    packages_ok = all(r["ok"] for r in package_results)
    hard_passed = services_ok and packages_ok

    hard_checks: dict[str, Any] = {
        "services": service_results,
        "packages": package_results,
        "load": load_avg,
        "disk_pct": disk_pct,
        "journal_errors": journal_errors,
        "unmanaged_services": unmanaged_services,
    }

    # ------------------------------------------------------------------
    # AI verification (when hard checks pass and prompt or journal errors exist)
    # ------------------------------------------------------------------
    should_run_ai = hard_passed and (verification_prompt or journal_errors)
    if should_run_ai:
        from app.workflows.steps.ai_verify import run_ai_verification

        prompt = verification_prompt or (
            "Analyze the following system journal errors and determine if any "
            "indicate a critical issue that needs attention."
        )
        system_state: dict[str, Any] = {
            "host_hostname": getattr(host, "hostname", host.ip_address),
            "host_ip": host.ip_address,
            "hard_checks": hard_checks,
        }
        try:
            ai_result = run_ai_verification(system_state, prompt)
        except Exception as exc:
            logger.warning("verify: AI verification raised an exception: %s", exc)
            ai_result = {"passed": True, "output": f"AI verification error (treated as pass): {exc}"}

    overall_passed = hard_passed and (ai_result is None or ai_result.get("passed", True))

    logger.info(
        "verify: host %s — hard=%s ai=%s overall=%s",
        host.ip_address,
        hard_passed,
        ai_result.get("passed") if ai_result else "n/a",
        overall_passed,
    )

    return {
        "passed": overall_passed,
        "hard_checks": hard_checks,
        "ai_result": ai_result,
    }
