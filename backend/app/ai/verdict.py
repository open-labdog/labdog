"""Reading a verify verdict, and deciding what an unclear one means.

A FAIL from an AI verify step reverts a VM: ``app.tasks.action_host``
treats failed verification as a failed run, and a failed run with a
snapshot and auto-rollback restores that snapshot. So the two questions
this module answers — *what did the model say* and *what happens when we
cannot tell* — are the ones that decide whether a healthy host gets
rolled back.

**Three verdicts, not two.** The previous version had PASS and FAIL, and
an unreadable reply silently became a pass. That default is right — a
verdict nobody can read is not evidence that anything is wrong, and
rolling back on it would be inventing a failure — but it is a *policy*,
not a fact, and for a critical upgrade the opposite answer is correct.
Making the third value explicit is what lets a manifest choose:
``ai_verify_fail_closed`` decides what INCONCLUSIVE resolves to, and
nothing else in the pipeline has to know.

Everything that is not a clean verdict funnels through INCONCLUSIVE —
an unreadable reply, no configured provider, AI switched off, budget
exhausted, a provider error, a timeout. The old code reached ``passed:
True`` from five separate places, each deciding the policy for itself;
now they all reach the same one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The host is healthy after the change.
PASSED = "pass"
#: Something is wrong. This is the verdict that can roll a host back.
FAILED = "fail"
#: The evidence does not support either answer. Includes every case
#: where no verdict was obtained at all.
INCONCLUSIVE = "inconclusive"

VERDICTS = (PASSED, FAILED, INCONCLUSIVE)

#: Anchored to the start of the first non-blank line, with a word
#: boundary. The anchor is what stops an ordinary English pass —
#: "Everything looks fine; nothing failed." — from being read as a FAIL
#: because the substring appears inside *failed*. The boundary is what
#: stops "FAILED to find any problems" from matching.
_VERDICT = re.compile(r"^(PASS|FAIL|INCONCLUSIVE)\b")


@dataclass(frozen=True)
class VerifyOutcome:
    """A verdict, what it resolves to, and why.

    ``passed`` is what the caller acts on; ``verdict`` and ``detail`` are
    what the operator reads in the run log. Keeping all three means a
    pass that was really "we could not tell, and this manifest fails
    open" is never displayed as a clean pass.
    """

    verdict: str
    passed: bool
    detail: str
    #: The AI session backing this verdict, when one ran. ``None`` when
    #: the step never got as far as starting one.
    session_id: int | None = None

    @property
    def conclusive(self) -> bool:
        return self.verdict in (PASSED, FAILED)


def parse_verdict(text: str) -> str:
    """Read the verdict from the first non-blank line.

    Deliberately strict. The prompt asks for the verdict as the first
    word of the reply, so anything else is a reply that did not follow
    the contract — and guessing at one is how a pass became a rollback.
    A reply that does not follow the contract is INCONCLUSIVE, which is
    a real answer the caller's policy knows how to handle, rather than
    an assumption made here.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _VERDICT.match(stripped.upper())
        return match.group(1).lower() if match else INCONCLUSIVE
    return INCONCLUSIVE


def resolve(verdict: str, *, fail_closed: bool) -> bool:
    """Whether ``verdict`` counts as a passing verification.

    A stated verdict means what it says at either policy — ``fail_closed``
    cannot turn a stated PASS into a failure, because that would discard
    a verdict the model actually gave. It only decides the middle::

        verdict         fail_closed=False   fail_closed=True
        PASSED          pass                pass
        FAILED          fail                fail
        INCONCLUSIVE    pass                fail

    The default is open, which preserves the behaviour every existing
    manifest was written against.
    """
    if verdict == PASSED:
        return True
    if verdict == FAILED:
        return False
    return not fail_closed


def outcome_from_reply(
    reply: str, *, fail_closed: bool, session_id: int | None = None
) -> VerifyOutcome:
    """Turn a model's reply into a decided outcome.

    The unreadable case keeps the reply in ``detail`` rather than
    discarding it. This resolves to a pass under the default policy, and
    a pass whose reasoning nobody can read should say so where the
    operator is looking, not only in a log line.
    """
    verdict = parse_verdict(reply)
    if verdict == INCONCLUSIVE:
        body = reply.strip() or "(no output)"
        detail = (
            "AI verification did not return a readable PASS or FAIL verdict "
            f"on the first line, so it is inconclusive. Reply was:\n{body}"
        )
    else:
        detail = reply.strip()
    return VerifyOutcome(
        verdict=verdict,
        passed=resolve(verdict, fail_closed=fail_closed),
        detail=detail,
        session_id=session_id,
    )


def inconclusive(reason: str, *, fail_closed: bool, session_id: int | None = None) -> VerifyOutcome:
    """An outcome for the cases where no verdict was ever obtained.

    No provider configured, AI disabled, budget exhausted, the backend
    errored, the session was cancelled. None of these say anything about
    the host — which is precisely what INCONCLUSIVE means — so they get
    the same policy treatment as an unreadable reply rather than each
    quietly returning a pass.
    """
    return VerifyOutcome(
        verdict=INCONCLUSIVE,
        passed=resolve(INCONCLUSIVE, fail_closed=fail_closed),
        detail=reason,
        session_id=session_id,
    )
