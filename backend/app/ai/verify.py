"""The verify session: evidence in, one verdict out.

A verify step is not an investigation. It is handed an evidence pack
assembled by the caller (see :mod:`app.ai.evidence`) and asked one
question about it. That is why it can run on a single-shot backend —
:data:`app.ai.loop.INVESTIGATIVE_MODES` deliberately excludes ``verify``
— and it is why the session gets no tools at all: there is nothing left
to look up, and a session whose verdict decides whether a snapshot gets
restored should not be opening SSH connections while it decides.

It still runs as a real :class:`~app.ai.models.AISession`. That is the
whole reason for the rewrite. The old step shelled out to a ``claude``
binary directly, so a verify verdict was the one AI call in LabDog with
no kill switch, no budget, no cost accounting, no transcript, and no
trace in the UI — invisible until it rolled a host back.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service
from app.ai import verdict as verdicts
from app.ai.evidence import EvidenceItem, render
from app.ai.models import AISession
from app.ai.service import AIDisabledError, BudgetExceededError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are LabDog's verification step. An automated change has just been \
made to one Linux host, and you decide whether it left the host healthy.

Your answer has consequences: a FAIL can restore the host from a \
pre-change snapshot, discarding the change and anything else that \
happened since.

The first line of your reply must be exactly one of these words, with \
nothing before it:

PASS — the evidence shows the host is healthy.
FAIL — the evidence shows something is wrong.
INCONCLUSIVE — the evidence does not let you tell.

After that word, give a brief reason in plain sentences.

How to judge:
- Everything you are given is below. You cannot run commands, open \
connections, or look anything up, so judge only what is in front of you.
- A reading marked UNAVAILABLE means that check did not run. It is not \
a healthy reading and it is not a zero — it is a gap in what you know.
- A reading marked TRUNCATED was cut short. Say so if it affects your \
answer.
- INCONCLUSIVE is a real answer, not a failure to decide. Use it when \
the readings you were given do not cover the question asked. Do not \
guess, and do not report a problem you cannot point at in the evidence.
- Do not infer a fault from the fact that you were asked. Most \
verifications pass.
"""

USER_TEMPLATE = """\
Host: {hostname} ({ip})

## What to confirm
{instructions}

## Evidence
{evidence}
"""

#: What the model is asked to confirm when the action's manifest does
#: not say. Reached when verification runs because journal errors turned
#: up rather than because someone configured a prompt, so the question
#: is the one those errors raise.
DEFAULT_INSTRUCTIONS = (
    "No specific checks were configured for this action. Decide whether "
    "anything in the evidence below indicates a real problem with this "
    "host — in particular whether any recent errors are serious rather "
    "than routine noise."
)


def build_prompt(*, hostname: str, ip: str, instructions: str, evidence: list[EvidenceItem]) -> str:
    """The single user turn a verify session sends."""
    return USER_TEMPLATE.format(
        hostname=hostname or "unknown",
        ip=ip or "unknown",
        instructions=(instructions or "").strip() or DEFAULT_INSTRUCTIONS,
        evidence=render(evidence),
    )


async def create_verify_session(
    db: AsyncSession,
    *,
    prompt: str,
    title: str,
    host_id: int | None = None,
    action_run_id: int | None = None,
    provider_id: int | None = None,
) -> AISession:
    """Persist the session and its two turns.

    ``allowed_tools=[]`` is recorded for the transcript's sake; it is not
    what withholds the tools. An empty allowlist still yields
    ``list_hosts`` and ``get_host_facts`` through ``ALWAYS_ALLOWED`` —
    the mode is what makes the session toolless, in
    :func:`app.ai.tools.tools_for_session`.
    """
    session = AISession(
        provider_id=provider_id,
        mode="verify",
        title=title[:200],
        mission=prompt,
        # A verify session cannot change anything even in principle: it
        # has no tools. Recording the lowest level keeps that true if it
        # ever grows one by accident.
        autonomy_level="read_only",
        status="queued",
        target_host_ids=[host_id] if host_id is not None else [],
        allowed_tools=[],
        action_run_id=action_run_id,
    )
    db.add(session)
    await db.flush()
    await service.append_message(db, session.id, role="system", content=SYSTEM_PROMPT)
    await service.append_message(db, session.id, role="user", content=prompt)
    await db.commit()
    return session


async def run_verify_session(
    db: AsyncSession,
    *,
    hostname: str,
    ip: str,
    instructions: str,
    evidence: list[EvidenceItem],
    fail_closed: bool,
    host_id: int | None = None,
    action_run_id: int | None = None,
    provider_id: int | None = None,
) -> verdicts.VerifyOutcome:
    """Collect a verdict on one host, or explain why there isn't one.

    Never raises. Every failure resolves to INCONCLUSIVE and is decided
    by ``fail_closed``, because none of them — no provider, AI disabled,
    budget spent, backend error — say anything about the host. The old
    step reached ``passed: True`` from five separate places, each
    deciding that policy for itself and none of them telling the
    operator which one had fired.
    """
    from app.tasks.ai_task import build_runner

    prompt = build_prompt(hostname=hostname, ip=ip, instructions=instructions, evidence=evidence)

    # Resolve the provider before writing anything: a session row for a
    # run that could never start is noise in the operator's session list.
    try:
        provider_row = await service.resolve_provider(db, provider_id)
        await service.assert_within_budget(db, provider_row)
    except (AIDisabledError, BudgetExceededError) as exc:
        logger.info("ai_verify: no verdict for %s: %s", hostname or ip, exc)
        return verdicts.inconclusive(f"AI verification did not run: {exc}", fail_closed=fail_closed)

    session = await create_verify_session(
        db,
        prompt=prompt,
        title=f"Verify {hostname or ip}",
        host_id=host_id,
        action_run_id=action_run_id,
        provider_id=provider_row.id,
    )

    from app.ai.loop import LoopCaps

    caps = await LoopCaps.from_settings(db)
    try:
        runner = build_runner(db, session, provider_row, caps, None, prompt=prompt)
        outcome = await runner.run()
        await db.commit()
    except Exception as exc:
        logger.exception("ai_verify: session %s failed for %s", session.id, hostname or ip)
        await service.finish_session(db, session, status="failed", error=str(exc))
        await db.commit()
        return verdicts.inconclusive(
            f"AI verification errored: {exc}", fail_closed=fail_closed, session_id=session.id
        )

    if outcome.status != "succeeded":
        return verdicts.inconclusive(
            f"AI verification did not complete ({outcome.stopped_by or outcome.status}).",
            fail_closed=fail_closed,
            session_id=session.id,
        )

    return verdicts.outcome_from_reply(
        outcome.report, fail_closed=fail_closed, session_id=session.id
    )
