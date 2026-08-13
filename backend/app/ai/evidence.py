"""What a verify step is given to judge.

An evidence pack is a list of readings, each one either a value or an
explicit statement that it could not be taken, with a note of where it
came from. It is rendered into the prompt and the model answers a
question about it. Nothing is looked up at run time.

**Why that is the safe shape.** It is tempting to describe this as "the
model gets no tools", but that is not the property that makes it safe —
a verify step still runs commands, it just runs the ones an operator
chose when configuring the action rather than ones the model invented
while a snapshot was open and a rollback was pending. Fixing the
arguments at configuration time is the guarantee; having no tools at run
time is a consequence of it.

That makes ``list[EvidenceItem]`` the seam. One producer exists today —
the SSH collector in :mod:`app.workflows.steps.verify`, adapted in
:mod:`app.workflows.steps.ai_verify` — and an operator-declared pack of
commands would plug in at exactly the same place, with no change to the
verdict path.

**Absence is a value.** A reading that failed is rendered as a sentence
saying so, never as a blank or a zero. The collector this replaces
defaulted an unreadable load average to 0.00 and an unreadable disk to
0%, which describes a host whose checks all failed as the healthiest
possible host. Silence reads the same way to a model: asked to judge a
host and shown an empty "Recent errors" section, it has no way to tell
"nothing was wrong" from "we never looked".

**Truncation is a value too.** An oversized reading is cut with a line
saying how much was dropped, for the same reason: a verdict reached on
the first 4 KB of a 2 MB journal is a different verdict from one reached
on all of it, and the model should be able to say so.
"""

from __future__ import annotations

from dataclasses import dataclass

#: How a reading that could not be taken is written into the prompt.
#: Spelled out rather than left blank, so the model cannot read a gap in
#: the evidence as an absence of problems.
UNAVAILABLE = "UNAVAILABLE — this check did not run, so its value is unknown."

#: Per-reading ceiling. Journal output is the one that runs away; four
#: thousand characters is roughly forty lines, enough to characterise a
#: fault without paying for a whole boot's logs on every action.
MAX_ITEM_CHARS = 4000

#: Whole-pack ceiling, applied after the per-item cut. A verify prompt
#: that outgrows this is one where the readings should be narrowed, not
#: one where a bigger context window is the answer.
MAX_PACK_CHARS = 24_000


@dataclass(frozen=True)
class EvidenceItem:
    """One reading handed to a verify step.

    ``value`` is the reading. ``None`` means it could not be taken, and
    ``unavailable_reason`` says why — the two are separate fields
    because "we tried and the SSH command failed" and "there was nothing
    to report" are different facts, and collapsing them is the bug this
    module exists to prevent.

    ``source`` is provenance: the command, query, or check that produced
    the value. It goes into the prompt so the model can weigh a reading
    it does not trust, and into the transcript so an operator reading
    the verdict later can see what it was reached from.
    """

    label: str
    value: str | None
    source: str = ""
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.value is not None

    @classmethod
    def reading(cls, label: str, value: object, source: str = "") -> EvidenceItem:
        """A reading that was taken.

        ``value`` is stringified here rather than by every caller, so a
        genuine ``0`` or ``0.0`` survives as "0" instead of being caught
        by a falsy check somewhere and turned into a missing reading.
        """
        return cls(label=label, value=str(value), source=source)

    @classmethod
    def missing(cls, label: str, reason: str, source: str = "") -> EvidenceItem:
        """A reading that could not be taken, and why."""
        return cls(label=label, value=None, source=source, unavailable_reason=reason)

    @classmethod
    def optional(
        cls, label: str, value: object | None, source: str = "", *, reason: str = ""
    ) -> EvidenceItem:
        """A reading that may or may not have been taken.

        The adapter for collectors that already signal failure with
        ``None``, so they do not each have to branch.
        """
        if value is None:
            return cls.missing(label, reason or "the check did not complete", source)
        return cls.reading(label, value, source)


def _render_item(item: EvidenceItem) -> str:
    head = f"### {item.label}"
    if item.source:
        head += f"\nSource: {item.source}"

    if not item.available:
        reason = item.unavailable_reason.strip()
        body = f"{UNAVAILABLE} Reason: {reason}" if reason else UNAVAILABLE
        return f"{head}\n{body}"

    body = (item.value or "").strip() or "(empty — the check ran and returned nothing)"
    if len(body) > MAX_ITEM_CHARS:
        dropped = len(body) - MAX_ITEM_CHARS
        body = (
            f"{body[:MAX_ITEM_CHARS]}\n"
            f"[TRUNCATED — {dropped} more characters were not included. Judge only "
            f"what is shown, and say that this reading was cut short.]"
        )
    return f"{head}\n{body}"


def render(items: list[EvidenceItem], *, max_chars: int = MAX_PACK_CHARS) -> str:
    """Render a pack for a prompt.

    Whole items are dropped rather than cut mid-reading when the pack
    ceiling is hit, and the drop is announced. A pack that silently
    ended early would be indistinguishable from one where the remaining
    checks all came back clean.
    """
    if not items:
        return "(No evidence was collected. You cannot judge this host — answer INCONCLUSIVE.)"

    rendered: list[str] = []
    used = 0
    for index, item in enumerate(items):
        block = _render_item(item)
        if rendered and used + len(block) > max_chars:
            remaining = len(items) - index
            rendered.append(
                f"### Not included\n[{remaining} further reading(s) were omitted "
                f"because the evidence exceeded {max_chars} characters. Their "
                f"values are unknown to you — do not assume they were clean.]"
            )
            break
        rendered.append(block)
        used += len(block)
    return "\n\n".join(rendered)


def summarise(items: list[EvidenceItem]) -> str:
    """One line of provenance for the run log.

    Names what was collected and what was missing, so an operator
    scanning a run can see the shape of the evidence without opening the
    session transcript.
    """
    if not items:
        return "no evidence collected"
    missing = [item.label for item in items if not item.available]
    if not missing:
        return f"{len(items)} reading(s), all collected"
    return f"{len(items)} reading(s), {len(missing)} unavailable: {', '.join(missing)}"
