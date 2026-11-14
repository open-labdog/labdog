"""Reading a run's Ansible output for the assistant.

An action run's log is the evidence an investigation of it starts from,
but it is too big to hand over whole: ``ActionHostRun.output`` holds up to
a mebibyte per host, and every byte in a prompt is read back on each later
turn. So the mission gets the parts an operator would look at first — what
unbork did around the playbook (``[preflight]``, ``[verify]``,
``[rollback]``…), each ``PLAY RECAP``, and every failed task — and the
model pages through the rest with ``get_action_run_output``.

:func:`parse` is the run log's line classifier, ported from
``frontend/lib/ansible-log.ts`` so that what the assistant is told is a
failure is what the run page paints red. Keep the two in step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.ai.redaction import redact

# Terminal control sequences, as the run page strips them: CSI (colour,
# cursor moves), OSC (titles, hyperlinks) and bare carriage returns.
_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_BARE_CR = re.compile(r"\r(?!\n)")

_STATUS = re.compile(r"^(ok|changed|skipping|failed|fatal|unreachable|rescued|ignored|included): ")
_PLAY = re.compile(r"^PLAY \[")
_TASK = re.compile(r"^(TASK|RUNNING HANDLER) \[")
_RECAP = re.compile(r"^PLAY RECAP\b")
_RECAP_ROW = re.compile(r"^\S.*\s:\s+ok=\d+")
_ERROR = re.compile(r"^\[ERROR\]:")
_WARNING = re.compile(r"^\[(DEPRECATION )?WARNING\]:")
_LABDOG = re.compile(r"^\[[a-z][a-z-]*\] ")
_LABDOG_FAILED = re.compile(r"\b(status=failed|passed=False|success=False|failed|unreachable)\b")
_SECTION = re.compile(r"^={3,} .* ={3,}$")
_OPENS_BRACKET = re.compile(r"(=> |^ *)[{\[]$")

#: The kinds that open a failure. Only the line that opens a block counts.
FAILURE_KINDS = frozenset({"error", "fatal", "failed", "unreachable", "labdog-failed"})

#: Lines that say what happened around and between the playbooks.
_OUTLINE_KINDS = frozenset({"labdog", "labdog-failed", "section", "recap-head", "recap-row"})

#: Lines of a task kept before its failure, and lines of the failure kept.
_LINES_BEFORE = 5
_LINES_OF_FAILURE = 40


@dataclass(frozen=True)
class LogLine:
    #: 1-based, as ``get_action_run_output`` numbers them.
    number: int
    text: str
    kind: str
    #: Part of the block a line above opened (a ``=> {`` result, an
    #: ``[ERROR]`` explanation).
    cont: bool = False


def readable(output: str) -> str:
    """A host run's output as the assistant sees it.

    Without terminal control sequences, and redacted as a whole rather
    than line by line, so a private key that spans lines is caught. The
    mission and ``get_action_run_output`` both number lines of this text,
    which is why both go through here: a redaction that folds a key into
    one placeholder moves every line after it.
    """
    text = _BARE_CR.sub("", _ANSI_CSI.sub("", _ANSI_OSC.sub("", output or "")))
    return redact(text)


def parse(text: str) -> list[LogLine]:
    """Classify every line of ``text``, which :func:`readable` has prepared.

    See ``frontend/lib/ansible-log.ts``.
    """
    out: list[LogLine] = []
    block: tuple[str, str] | None = None  # (kind, until)
    in_recap = False

    for number, line in enumerate(text.split("\n"), start=1):
        if block is not None and block[1] == "bracket":
            out.append(LogLine(number, line, block[0], cont=True))
            if line in ("}", "]"):
                block = None
            continue
        if block is not None:
            structural = any(
                p.match(line) for p in (_STATUS, _TASK, _PLAY, _RECAP, _LABDOG, _SECTION)
            )
            if structural or (block[1] == "blank" and not line.strip()):
                block = None
            else:
                out.append(LogLine(number, line, block[0], cont=True))
                continue

        kind = "plain"
        status = _STATUS.match(line)
        if _RECAP.match(line):
            kind = "recap-head"
            in_recap = True
        elif in_recap and _RECAP_ROW.match(line):
            kind = "recap-row"
        elif _PLAY.match(line):
            kind = "play"
        elif _TASK.match(line):
            kind = "task"
        elif status:
            kind = status.group(1)
            if _OPENS_BRACKET.search(line):
                block = (kind, "bracket")
        elif line == "...ignoring":
            kind = "ignored"
        elif _ERROR.match(line):
            kind = "error"
            block = (kind, "structure")
        elif _WARNING.match(line):
            kind = "warning"
            block = (kind, "blank")
        elif _LABDOG.match(line):
            kind = "labdog-failed" if _LABDOG_FAILED.search(line) else "labdog"
        elif _SECTION.match(line):
            kind = "section"
        if kind not in ("recap-head", "recap-row") and line.strip():
            in_recap = False
        out.append(LogLine(number, line, kind))
    return out


def outline(lines: list[LogLine]) -> list[LogLine]:
    """unbork's own step lines, the section headers, and every recap."""
    return [line for line in lines if line.kind in _OUTLINE_KINDS and not line.cont]


@dataclass
class Failure:
    """One task that failed, with enough around it to read why."""

    #: The ``TASK [...]`` header, when the failure was inside a task.
    task: str | None
    lines: list[LogLine]
    #: Ansible carried on past it (``...ignoring``).
    ignored: bool = False


def failures(lines: list[LogLine]) -> list[Failure]:
    """Every failed task, in order. Failures inside one task are one entry.

    Each keeps its task header, the few lines before the failure, and the
    failure's own block (the result, or the ``[ERROR]`` explanation). A
    unbork step that failed (``[verify] … status=failed``) is its own entry
    with no task.
    """
    found: list[Failure] = []
    task_index: int | None = None
    current: Failure | None = None
    current_task: int | None = None

    for i, line in enumerate(lines):
        if line.cont:
            continue
        if line.kind in ("task", "play", "section", "recap-head"):
            task_index = i if line.kind == "task" else None
            current = None
            continue
        if line.kind == "ignored" and current is not None:
            current.ignored = True
            continue
        if line.kind not in FAILURE_KINDS:
            continue

        block = [line]
        for follow in lines[i + 1 :]:
            if not follow.cont or len(block) >= _LINES_OF_FAILURE:
                break
            block.append(follow)

        if line.kind == "labdog-failed":
            found.append(Failure(task=None, lines=block))
            current = None
            continue
        if current is not None and current_task == task_index:
            current.lines.extend(block)
            continue

        start = i - _LINES_BEFORE
        if task_index is not None:
            start = max(start, task_index + 1)
        before = [b for b in lines[max(start, 0) : i] if b.text.strip()]
        current = Failure(
            task=lines[task_index].text.rstrip(" *") if task_index is not None else None,
            lines=before + block,
        )
        current_task = task_index
        found.append(current)
    return found


#: Where a failed task's own result starts, for :func:`failure_reason`. A
#: PostgreSQL regular expression, so the list endpoint reads about 2 kB of
#: each first-failing host's log rather than the whole mebibyte. Nested
#: because PostgreSQL refuses a repetition count over 255.
FAILURE_WINDOW_SQL = r"(?:^|\n)((?:fatal|failed): \[(?:.{0,250}){0,8})"

_MSG = re.compile(r'"msg":\s*"((?:[^"\\]|\\.)*)"')
_RESULT = re.compile(r"^(?:fatal|failed): \[[^\]]*\]: [A-Z!]+(?: => )?(.*)$")
_REASON_MAX = 240


def failure_reason(window: str | None) -> str | None:
    """One line saying why a host failed, from the start of its first
    failed task (``FAILURE_WINDOW_SQL``), for a row in a list of runs.

    The ``msg`` of the first failure Ansible did not carry on past, else
    the result line itself. ``ActionHostRun.error_message`` is no use
    here: for a playbook failure it says only that ansible-runner exited
    non-zero. Redacted like everything else read out of a run log.
    """
    if not window:
        return None
    lines = parse(readable(window))
    found = failures(lines)
    if not found:
        return None
    pick = next((f for f in found if not f.ignored), found[0])
    text = "\n".join(line.text for line in pick.lines if line.kind in FAILURE_KINDS or line.cont)
    msg = _MSG.search(text)
    if msg:
        try:
            reason = json.loads(f'"{msg.group(1)}"')
        except ValueError:
            reason = msg.group(1)
    else:
        head = next((line.text for line in pick.lines if line.kind in FAILURE_KINDS), "")
        m = _RESULT.match(head)
        reason = (m.group(1) if m else head).strip(" {")
    reason = " ".join(str(reason).split())
    if not reason:
        return None
    return reason if len(reason) <= _REASON_MAX else reason[: _REASON_MAX - 1] + "…"
