"""Verification step: hard SSH checks + optional AI review after a system update."""

import logging
from typing import Any

from app.ssh_utils import ssh_connect_host

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
    *,
    ai_fail_closed: bool = False,
    action_run_id: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Verify system health after a host update via SSH hard checks.

    Connects once over SSH and runs all checks sequentially over the same
    connection.  Hard checks cover every service with ``desired_state="running"``
    and every package with ``desired_state`` in ``("present", "latest")``.
    Basic system health (load average and disk usage) is always collected.
    Recent journal errors are gathered as additional context.

    When all hard checks pass, and either ``verification_prompt`` was
    supplied *or* the journal had errors to explain, the function delegates
    to :func:`~app.workflows.steps.ai_verify.run_ai_verification` for an
    AI-assisted assessment of everything collected above.

    Args:
        host: Host ORM object exposing ``hostname``, ``ip_address``,
            ``ssh_port``, ``ssh_user``, and ``ssh_host_key_entry``.
        ssh_key_path: Absolute path to the decrypted SSH private key on tmpfs.
        effective_services: List of effective service rule objects, each with
            ``service_name`` and ``desired_state`` attributes.
        effective_packages: List of effective package rule objects, each with
            ``package_name`` and ``desired_state`` attributes.
        verification_prompt: Optional free-text instructions for AI
            verification, supplied by the action's manifest
            (``ai_verify_prompt``).  ``None`` still allows AI verification
            to run when journal errors were found — see below.
        db: Active async SQLAlchemy session used for TOFU key persistence.
            The AI step does not use it; it opens its own, because
            ``action_group`` verifies hosts concurrently and has none to
            lend.
        ai_fail_closed: What an INCONCLUSIVE AI verdict resolves to.
            Defaults to open, which is the behaviour every existing
            manifest was written against.
        action_run_id: Links the AI session back to the run that caused
            it, so a verdict is traceable from the run detail page.
        dry_run: Suppresses AI verification. The SSH checks still run —
            they are free and they are what a preview is for — but a
            check-mode run changed nothing, so an AI verdict on it would
            describe the host as it already was while costing real money
            and reading, in the run detail, exactly like a verdict on a
            change that happened.

    Returns:
        A dict of the form::

            {
                "passed": bool,
                "hard_checks": {
                    "services": [{"name": str, "expected": str, "actual": str, "ok": bool}, ...],
                    "packages": [
                        {"name": str, "expected": str, "installed": bool, "ok": bool},
                        ...,
                    ],
                    "load": float | None,
                    "disk_pct": int | None,
                    "journal_errors": str,
                },
                "ai_result": {
                    "passed": bool,
                    "verdict": "pass" | "fail" | "inconclusive",
                    "output": str,
                    "session_id": int | None,
                } | None,
            }

        ``load`` and ``disk_pct`` are ``None`` when the reading could not be
        taken, which callers must not conflate with 0.

        ``passed`` is ``True`` only when every hard check succeeds AND (if AI
        verification was requested) the AI verdict resolves to a pass. The
        load and disk readings do not affect it — they are context for the
        AI verdict and for the operator, not thresholds.
    """
    service_results: list[dict[str, Any]] = []
    package_results: list[dict[str, Any]] = []
    # None means "not collected", which is not the same as zero. These
    # used to default to 0.0 and 0 with their read errors swallowed below,
    # so a host whose load and disk checks had both failed was described
    # to the AI verifier as load 0.00 and disk 0% — the healthiest
    # possible host. The journal read already got this right, substituting
    # an explicit error string; these two now match it.
    load_avg: float | None = None
    disk_pct: int | None = None
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
            s for s in effective_services if getattr(s, "state", None) == "running"
        ]

    packages_to_check = [
        p for p in effective_packages if getattr(p, "desired_state", None) in ("present", "latest")
    ]
    if not packages_to_check:
        packages_to_check = [
            p for p in effective_packages if getattr(p, "state", None) in ("present", "latest")
        ]

    # ------------------------------------------------------------------
    # Open a single SSH connection for all checks
    # ------------------------------------------------------------------
    try:
        conn_ctx = ssh_connect_host(
            host,
            db,
            client_keys=[ssh_key_path],
            connect_timeout=30,
        )
        conn = await conn_ctx.__aenter__()
    except Exception as exc:
        logger.error("verify: SSH connection failed to %s: %s", host.ip_address, exc)
        # Cannot run any checks — return a failed result immediately
        return {
            "passed": False,
            "hard_checks": {
                "services": [],
                "packages": [],
                # Nothing was collected, so nothing is reported. Zeros here
                # would describe an unreachable host as an idle healthy one.
                "load": None,
                "disk_pct": None,
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
                result = await conn.run(f"systemctl is-active {svc_name}", check=False)
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
                    rpm_result = await conn.run(f"rpm -q {pkg_name}", check=False)
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
                logger.warning("verify: high load average %.2f on %s", load_avg, host.ip_address)
        except Exception as exc:
            # load_avg stays None, and is reported as unavailable rather
            # than as a number nobody measured.
            logger.warning("verify: load average check failed on %s: %s", host.ip_address, exc)

        # ------------------------------------------------------------------
        # System health: disk usage
        # ------------------------------------------------------------------
        try:
            disk_result = await conn.run("df --output=pcent / | tail -1", check=False)
            disk_pct = int(disk_result.stdout.strip().rstrip("%"))
            if disk_pct > _DISK_WARN_THRESHOLD:
                logger.warning("verify: disk usage %d%% on %s", disk_pct, host.ip_address)
        except Exception as exc:
            # disk_pct stays None. See the load average note above.
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
            managed_names = {s.service_name for s in effective_services}
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
            await conn_ctx.__aexit__(None, None, None)
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
    # The journal-error trigger is not a leftover: it is how a host that
    # logged something ugly gets looked at even when nobody configured a
    # prompt for this action. It stays gated behind ai.enabled (off by
    # default) and a configured provider, so it cannot bill anyone who
    # has not opted in.
    # ``not dry_run`` is load-bearing rather than tidy. Neither the
    # snapshot nor the verify gate in ``action_host`` consults dry_run, so
    # a preview of a destructive action on a VM-mapped host already
    # reaches here — and without this it would open a billed AI session to
    # judge a host that check mode deliberately left untouched.
    should_run_ai = hard_passed and not dry_run and (verification_prompt or journal_errors)
    if should_run_ai:
        from app.workflows.steps.ai_verify import run_ai_verification

        system_state: dict[str, Any] = {
            "host_hostname": getattr(host, "hostname", host.ip_address),
            "host_ip": host.ip_address,
            "hard_checks": hard_checks,
        }
        try:
            ai_result = await run_ai_verification(
                system_state,
                verification_prompt or "",
                fail_closed=ai_fail_closed,
                host_id=getattr(host, "id", None),
                action_run_id=action_run_id,
            )
        except Exception as exc:
            # run_ai_verification does not raise — every failure inside it
            # is already an inconclusive verdict decided by the policy. If
            # something got past it anyway, the same rule applies here
            # rather than a hardcoded pass, so a fail-closed action cannot
            # be let through by a bug in the verifier.
            logger.warning("verify: AI verification raised an exception: %s", exc)
            ai_result = {
                "passed": not ai_fail_closed,
                "verdict": "inconclusive",
                "output": f"AI verification errored before reaching a verdict: {exc}",
                "session_id": None,
            }

    overall_passed = hard_passed and (ai_result is None or ai_result.get("passed", True))

    logger.info(
        "verify: host %s — hard=%s ai=%s overall=%s",
        host.ip_address,
        hard_passed,
        ai_result.get("verdict") if ai_result else "n/a",
        overall_passed,
    )

    return {
        "passed": overall_passed,
        "hard_checks": hard_checks,
        "ai_result": ai_result,
    }
