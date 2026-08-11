"""AI verification step using the claude CLI.

A FAIL from here reverts a VM. :func:`app.tasks.action_host` treats a
failed verification as a failed run, and a failed run with a snapshot and
auto-rollback enabled restores that snapshot — so how this function reads
a verdict decides whether a healthy host gets rolled back.

**The verdict is read from the first line only.** The previous version
searched the whole reply for the substrings ``PASS`` and ``FAIL``, which
made an ordinary English pass into a rollback: given

    Everything looks fine; nothing failed.

``find("PASS")`` returns -1 while ``find("FAIL")`` matches inside
*failed*, so the reply parsed as a FAIL. That is not hypothetical
phrasing — it is how a model answers "is this host healthy?" when the
answer is yes.

It also is not dead code, which is the part that made it worth fixing
before the phase 5 rewrite. The callers pass ``verification_prompt=None``,
but :func:`app.workflows.steps.verify.run_verification` runs AI
verification whenever there is a prompt **or** any journal error in the
last ten minutes, and the container ships a ``claude`` binary.

Ambiguity still passes rather than failing. That is the existing
behaviour and the conservative direction here: a verdict nobody can read
is not evidence that anything is wrong, and rolling a host back on it
would be inventing a failure. Whether "unsure" should be able to fail
closed is a policy question with a per-manifest answer, and it belongs to
the phase 5 rework along with an explicit INCONCLUSIVE verdict.
"""

import logging
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

#: The verdict, anchored to the start of the first line. ``\b`` keeps
#: "FAILED to find any problems" from matching FAIL.
_VERDICT = re.compile(r"^(PASS|FAIL)\b")

#: What a reading that could not be taken looks like in the prompt.
#: Spelled out rather than left as 0 or blank: the collector used to
#: default an unreadable load average to 0.00 and an unreadable disk to
#: 0%, which describes a host whose checks all failed as the healthiest
#: possible host.
UNAVAILABLE = "UNAVAILABLE — this check could not be run, do not assume a value"

_PROMPT_TEMPLATE = """\
You are verifying a Linux host after a system update. Analyze the following
data and reply with PASS or FAIL as the very first word of your reply,
followed by a brief reason. Nothing may precede the verdict.

Some readings below may be marked UNAVAILABLE, which means the check
failed rather than that the value was fine. Judge only what you can see,
and say which readings you were missing.

Host: {hostname} ({ip})
Services checked: {service_results}
Packages checked: {package_results}
System load: {loadavg}
Disk usage: {disk_pct}
Recent error logs:
{journal_errors}

Additional verification instructions:
{verification_prompt}"""


def _reading(value: Any) -> Any:
    """Render one collected reading, or say plainly that it is missing."""
    return UNAVAILABLE if value is None else value


def parse_verdict(output: str) -> bool | None:
    """``True`` for PASS, ``False`` for FAIL, ``None`` when unreadable.

    Deliberately strict. The prompt asks for the verdict as the first
    word, so anything else is a reply that did not follow the contract —
    and guessing at one is how a pass became a rollback.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _VERDICT.match(stripped.upper())
        return match.group(1) == "PASS" if match else None
    return None


def run_ai_verification(system_state: dict[str, Any], verification_prompt: str) -> dict[str, Any]:
    """Invoke the claude CLI to assess post-update system state.

    Builds a structured prompt from ``system_state``, runs ``claude -p``, and
    reads a PASS/FAIL verdict from the first line of the response. Failures
    to locate or run the ``claude`` binary are treated as a non-fatal pass so
    that the overall workflow is not blocked when the CLI is unavailable.

    Args:
        system_state: Dict produced by :func:`run_verification` containing
            ``host_hostname``, ``host_ip``, ``hard_checks``, and related keys.
        verification_prompt: Free-text instructions supplied by the operator
            describing what should be confirmed after the update.

    Returns:
        A dict with keys:

        - ``passed`` (bool): ``True`` when the verdict is PASS, and also when
          the verdict could not be read, the CLI is missing, or it timed out.
        - ``output`` (str): The CLI's reply, or a description of why there
          isn't one. When the verdict was unreadable this is prefixed with a
          note saying so, because the run detail is where an operator would
          otherwise see a pass with no explanation of how it was reached.
    """
    hard = system_state.get("hard_checks", {})

    prompt = _PROMPT_TEMPLATE.format(
        hostname=system_state.get("host_hostname", "unknown"),
        ip=system_state.get("host_ip", "unknown"),
        service_results=hard.get("services", []),
        package_results=hard.get("packages", []),
        loadavg=_reading(hard.get("load")),
        disk_pct=_reading(hard.get("disk_pct")),
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

        verdict = parse_verdict(output)
        if verdict is None:
            # Surfaced in the returned output, not only in the log: this
            # produces a pass, and a pass whose reasoning nobody can read
            # should say so where the operator is looking.
            detail = output or (result.stderr or "").strip() or "(no output)"
            logger.warning(
                "ai_verify: no PASS/FAIL verdict on the first line, treating as PASS: %s",
                detail[:200],
            )
            return {
                "passed": True,
                "output": (
                    "AI verification returned no readable PASS/FAIL verdict and was "
                    f"treated as a pass. Reply was:\n{detail}"
                ),
            }

        return {"passed": verdict, "output": output}

    except FileNotFoundError:
        logger.info("ai_verify: claude CLI not installed, skipping AI verification")
        return {"passed": True, "output": "claude CLI not available, skipping AI verification"}

    except subprocess.TimeoutExpired:
        logger.warning("ai_verify: claude CLI timed out after 120 s, treating as pass")
        return {"passed": True, "output": "AI verification timed out, treating as pass"}
