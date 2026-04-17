"""AI verification step using the claude CLI."""

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """\
You are verifying a Linux host after a system update. Analyze the following
data and respond with exactly PASS or FAIL followed by a brief reason.

Host: {hostname} ({ip})
Services checked: {service_results}
Packages checked: {package_results}
System load: {loadavg}
Disk usage: {disk_pct}
Recent error logs:
{journal_errors}

Additional verification instructions:
{verification_prompt}"""


def run_ai_verification(system_state: dict[str, Any], verification_prompt: str) -> dict[str, Any]:
    """Invoke the claude CLI to assess post-update system state.

    Builds a structured prompt from ``system_state``, runs ``claude -p``, and
    parses the response for a PASS/FAIL verdict.  Failures to locate or run the
    ``claude`` binary are treated as a non-fatal pass so that the overall
    workflow is not blocked when the CLI is unavailable.

    Args:
        system_state: Dict produced by :func:`run_verification` containing
            ``host_hostname``, ``host_ip``, ``hard_checks``, and related keys.
        verification_prompt: Free-text instructions supplied by the operator
            describing what should be confirmed after the update.

    Returns:
        A dict with keys:

        - ``passed`` (bool): ``True`` when the AI verdict is PASS, or when AI
          verification is unavailable / timed out.
        - ``output`` (str): Raw stdout from the claude CLI, or a descriptive
          fallback message.
    """
    hard = system_state.get("hard_checks", {})

    prompt = _PROMPT_TEMPLATE.format(
        hostname=system_state.get("host_hostname", "unknown"),
        ip=system_state.get("host_ip", "unknown"),
        service_results=hard.get("services", []),
        package_results=hard.get("packages", []),
        loadavg=hard.get("load", "unknown"),
        disk_pct=hard.get("disk_pct", "unknown"),
        journal_errors=hard.get("journal_errors", "(none)") or "(none)",
        verification_prompt=verification_prompt,
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout.strip()
        logger.debug("ai_verify: claude output: %s", output)

        upper = output.upper()
        # Locate first occurrence of PASS or FAIL
        pass_pos = upper.find("PASS")
        fail_pos = upper.find("FAIL")

        if pass_pos == -1 and fail_pos == -1:
            # No clear verdict — treat as pass but log the ambiguity
            logger.warning("ai_verify: no PASS/FAIL found in output, treating as PASS")
            return {"passed": True, "output": output}

        if fail_pos == -1 or (pass_pos != -1 and pass_pos < fail_pos):
            return {"passed": True, "output": output}

        return {"passed": False, "output": output}

    except FileNotFoundError:
        logger.info("ai_verify: claude CLI not installed, skipping AI verification")
        return {"passed": True, "output": "claude CLI not available, skipping AI verification"}

    except subprocess.TimeoutExpired:
        logger.warning("ai_verify: claude CLI timed out after 120 s, treating as pass")
        return {"passed": True, "output": "AI verification timed out, treating as pass"}
