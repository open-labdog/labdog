"""AI verification step: turn collected readings into a decided verdict.

This step's answer has teeth. :mod:`app.tasks.action_host` treats a
failed verification as a failed run, and a failed run with a snapshot and
auto-rollback restores that snapshot — discarding the change and anything
else that happened since. So the two things that matter here are that the
verdict is read honestly, and that the evidence behind it is described
honestly.

What this replaced shelled out to a ``claude`` binary directly. That made
it the one AI call in LabDog with no kill switch, no budget, no cost
accounting, no transcript, and no trace in the UI — a verdict that could
roll a host back and leave nothing behind explaining why. It now runs as
an ordinary :class:`~app.ai.models.AISession` in ``verify`` mode, through
whichever provider the operator configured, and the session is linked
from the action run.

The step's own job is narrow: adapt what
:func:`app.workflows.steps.verify.run_verification` collected into an
evidence pack (:mod:`app.ai.evidence`), hand it over, and render the
result. The evidence pack is the seam — a future operator-declared list
of commands produces the same ``list[EvidenceItem]`` and nothing
downstream changes.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai import verdict as verdicts
from app.ai.evidence import EvidenceItem, summarise

logger = logging.getLogger(__name__)


def _service_lines(results: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- {r.get('name')}: expected {r.get('expected')}, actual {r.get('actual')}"
        f" ({'ok' if r.get('ok') else 'NOT OK'})"
        for r in results
    )


def _package_lines(results: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- {r.get('name')}: expected {r.get('expected')},"
        f" {'installed' if r.get('installed') else 'NOT INSTALLED'}"
        for r in results
    )


def evidence_from_state(system_state: dict[str, Any]) -> list[EvidenceItem]:
    """Adapt one ``run_verification`` result into an evidence pack.

    ``load`` and ``disk_pct`` arrive as ``None`` when the reading could
    not be taken, and that distinction is the reason this adapter exists
    rather than a format string: they used to default to 0.00 and 0%,
    describing a host whose checks had all failed as the healthiest
    possible host. :meth:`EvidenceItem.optional` carries the difference
    through to the prompt.

    An empty service or package list is a reading, not a gap — it means
    the host has nothing of that kind under management — so it is
    reported as such rather than as UNAVAILABLE.
    """
    hard = system_state.get("hard_checks") or {}
    services = hard.get("services") or []
    packages = hard.get("packages") or []
    journal = (hard.get("journal_errors") or "").strip()

    return [
        EvidenceItem.reading(
            "Managed services",
            _service_lines(services) if services else "(no services are managed on this host)",
            source="ssh: systemctl is-active <unit>",
        ),
        EvidenceItem.reading(
            "Managed packages",
            _package_lines(packages) if packages else "(no packages are managed on this host)",
            source="ssh: dpkg-query / rpm -q",
        ),
        EvidenceItem.optional(
            "System load (1 minute)",
            hard.get("load"),
            source="ssh: cat /proc/loadavg",
            reason="the load average could not be read over SSH",
        ),
        EvidenceItem.optional(
            "Root filesystem usage (%)",
            hard.get("disk_pct"),
            source="ssh: df --output=pcent /",
            reason="disk usage could not be read over SSH",
        ),
        EvidenceItem.reading(
            "Errors logged in the last 10 minutes",
            journal or "(none — the journal was read and had no error-priority entries)",
            source="ssh: journalctl --since '10 minutes ago' -p err",
        ),
    ]


async def run_ai_verification(
    system_state: dict[str, Any],
    verification_prompt: str,
    *,
    fail_closed: bool = False,
    host_id: int | None = None,
    action_run_id: int | None = None,
) -> dict[str, Any]:
    """Ask the configured provider whether this host came through healthy.

    Opens its own database session. The callers cannot lend one:
    :mod:`app.tasks.action_group` verifies its hosts concurrently and
    passes ``db=None`` today, and one async SQLAlchemy session cannot
    serve parallel work.

    ``fail_closed`` decides only what an INCONCLUSIVE verdict resolves
    to — see :func:`app.ai.verdict.resolve`. It defaults to open, which
    is the behaviour every existing manifest was written against: a
    verdict nobody can read is not evidence that anything is wrong, and
    rolling a host back on it would be inventing a failure.

    Returns a dict with:

    - ``passed`` (bool): what the caller acts on.
    - ``verdict`` (str): ``pass`` | ``fail`` | ``inconclusive``, so a
      pass that was really "we could not tell" is not displayed as a
      clean pass.
    - ``output`` (str): the model's reasoning, or why there isn't any.
    - ``session_id`` (int | None): the AI session, for the transcript.
    """
    from app.ai.verify import run_verify_session
    from app.db import task_session

    evidence = evidence_from_state(system_state)
    hostname = str(system_state.get("host_hostname") or "")
    ip = str(system_state.get("host_ip") or "")

    async with task_session() as db:
        outcome = await run_verify_session(
            db,
            hostname=hostname,
            ip=ip,
            instructions=verification_prompt,
            evidence=evidence,
            fail_closed=fail_closed,
            host_id=host_id,
            action_run_id=action_run_id,
        )

    logger.info(
        "ai_verify: %s — verdict=%s passed=%s fail_closed=%s evidence=%s",
        hostname or ip,
        outcome.verdict,
        outcome.passed,
        fail_closed,
        summarise(evidence),
    )
    return {
        "passed": outcome.passed,
        "verdict": outcome.verdict,
        "output": _render_output(outcome, fail_closed),
        "session_id": outcome.session_id,
    }


def _render_output(outcome: verdicts.VerifyOutcome, fail_closed: bool) -> str:
    """What the operator reads in the run log.

    An inconclusive verdict says which way the policy resolved it. That
    line is the one an operator needs when a rollback happens and the
    reply looks like it was arguing for a pass, or when a suspicious host
    was let through on a reply nobody could read.
    """
    if outcome.conclusive:
        return outcome.detail
    resolution = "failing this verification" if fail_closed else "treated as a pass"
    return (
        f"{outcome.detail}\n\n"
        f"This action's fail_closed policy is {fail_closed}, so an inconclusive "
        f"verdict is {resolution}."
    )
