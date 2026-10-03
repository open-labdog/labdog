"""The classifier against the shell it guards.

``safety.py`` decides what a line will do by reading it. Bash is the
authority on what it does, so this runs the lines the classifier calls
read-only and checks the claim: nothing runs but the one command that was
allowed, and no file is created.

Three commands stand in for a host. ``a`` is the only head on the
allowlist; ``b`` and ``c`` are commands the classifier must never let
through, and each is a shell function that logs its own name, so a line
that gets one of them run says so. A read-only line that ran ``b``, or
made a file, is a line whose quoting the classifier read differently from
bash — the failure the quote-aware reading of BUG-106 could introduce,
because it decides which operators are syntax and which are data.

The lines cover quoting: letters, the operators, quotes, escapes, ``#``
and ``$'``. A fixed corpus first, then seeded random lines built the same
way, half of them a well-formed line with a character or two changed, since
a bug in this reading shows up where the quoting nearly balances. Set
``LABDOG_SAFETY_FUZZ_CASES`` to run more of them.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from app.ai import safety
from app.ai.safety import classify_command

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

CASES = int(os.environ.get("LABDOG_SAFETY_FUZZ_CASES", "3000"))

# `a f` fails, so that `a f || b` runs `b`. `trap wait EXIT` so a line ending
# in `&` is not judged before its background command has logged.
PRELUDE = (
    'a(){ echo a >>"$LOG"; [ "$1" != f ]; }; b(){ echo b >>"$LOG"; }; c(){ echo c >>"$LOG"; }; '
    "trap wait EXIT\n"
)


@pytest.fixture(autouse=True)
def only_a_is_read_only(monkeypatch):
    monkeypatch.setattr(safety, "READ_ONLY_HEADS", frozenset({"a"}))


class Shell:
    """Runs one line in bash, in an empty directory, and reports the damage."""

    def __init__(self, root: Path) -> None:
        self.work = root / "work"
        self.work.mkdir()
        self.log = root / "log"

    def run(self, line: str) -> tuple[list[str], list[str]]:
        self.log.write_text("")
        subprocess.run(
            [BASH, "--norc", "--noprofile", "-c", PRELUDE + line],
            cwd=self.work,
            env={"LOG": str(self.log), "PATH": os.defpath, "LC_ALL": "C"},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
        )
        created = sorted(entry.name for entry in self.work.iterdir())
        for entry in self.work.iterdir():
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        return self.log.read_text().split(), created


@pytest.fixture
def shell(tmp_path):
    return Shell(tmp_path)


def _damage(ran: list[str], created: list[str]) -> str:
    extra = sorted(set(ran) - {"a"})
    return f"ran={extra} created={created}" if extra or created else ""


# ---------------------------------------------------------------------------
# A fixed corpus
# ---------------------------------------------------------------------------

# Each of these runs `b` in bash. They are the ways a reader that decided
# something was quoted could be wrong, so none may be called read-only.
ATTACKS = [
    "a 'x'; b",
    'a "x"; b',
    "a 'x'|b",
    "a 'x'&b",
    "a 'x' && b",
    "a 'f' || b",
    "a 'x'\nb",
    "a 'x|y' | b",
    'a "x;y" ; b',
    # Escaped quotes quote nothing.
    "a \\'; b; a \\'",
    'a \\"; b; a \\"',
    # `"\\"` is a closed string; so is `'\'`.
    'a "\\\\"; b; a "\\\\"',
    "a '\\'; b; a 'x'",
    # `\'` inside ANSI-C quoting does not close it. Bash reads `$'x\'y'` as
    # one string and runs `b` on the next line; a reader that closed the
    # quote at the escaped one is still inside it, and takes `b` for data.
    "a $'x\\'y'\nb\n'",
    "a $'\\''\nb\n'",
    # A comment is not a quote's business: the quote in it opens nothing.
    "a #'\nb\n'",
    "a x #'\nb\n'",
    'a x # "\nb\n"',
    # Line continuation joins lines; it does not hide an operator.
    "a x \\\n; b",
]

# And these are plain reads. The classifier has to see that they are.
BENIGN = [
    "a 'x|b'",
    "a 'x;b'",
    "a 'x&b'",
    "a 'x&&b'",
    'a "x|b"',
    'a "x ; b"',
    "a x\\|b",
    "a x\\;b",
    "a 'x\nb'",
    "a '#'",
    "a x | a 'y|b' | a",
    "a 'x>b'",
    'a "x<b"',
    "a x\\>b",
    "a '\\' | a",
    'a "\\"" | a',
]


@pytest.mark.parametrize("line", ATTACKS)
def test_an_attack_is_not_read_only_and_really_runs_something(shell, line):
    ran, _ = shell.run(line)
    assert set(ran) - {"a"}, "not an attack: bash runs nothing but `a`"
    assert classify_command(line).classification != "read_only"


@pytest.mark.parametrize("line", BENIGN)
def test_a_plain_read_is_read_only_and_bash_agrees(shell, line):
    ran, created = shell.run(line)
    assert not _damage(ran, created), "not a plain read"
    verdict = classify_command(line)
    assert verdict.classification == "read_only", verdict.reason


# ---------------------------------------------------------------------------
# Seeded random lines
# ---------------------------------------------------------------------------

_DATA = ["a", "b", "c", "x", "|", ";", "&", "&&", "||", ">", "<", "#", "$", " ", "\n", "\\", "->"]
_EDITS = ["'", '"', "\\", "#", "$", "$'", "\n", ";", "&", "|", ">", "<", " ", "a", "b", "\\\n"]
_OPS = [" | ", " | ", " | ", " ; ", " && ", " || ", " & ", "\n"]


def _quoted(rng: random.Random) -> str:
    quote = rng.choice("'\"")
    parts = []
    for _ in range(rng.randint(0, 5)):
        piece = rng.choice(_DATA)
        if quote == '"' and piece == "\\":
            piece = rng.choice(["\\\\", '\\"', "\\|", "\\n"])
        parts.append(piece)
    return f"{quote}{''.join(parts)}{quote}"


def _ansi(rng: random.Random) -> str:
    """`$'...'` and `$"..."`, with the escapes that make the first tricky."""
    if rng.random() < 0.8:
        pool = ["a", "b", "c", "\\'", "\\'", "\\\\", "|", ";", "&", " ", ">", "\\n", "x"]
        return "$'" + "".join(rng.choice(pool) for _ in range(rng.randint(0, 6))) + "'"
    pool = ["a", "b", "|", ";", "&", " ", '\\"', "x"]
    return '$"' + "".join(rng.choice(pool) for _ in range(rng.randint(0, 6))) + '"'


def _comment(rng: random.Random) -> str:
    pool = ["a", "b", "c", "'", '"', "|", ";", "&", " ", "\n", "x", "\\"]
    return " #" + "".join(rng.choice(pool) for _ in range(rng.randint(0, 7)))


def _well_formed(rng: random.Random) -> str:
    commands = []
    for _ in range(rng.randint(1, 4)):
        words = [rng.choice(["a", "a", "a", "b", "c"])]
        for _ in range(rng.randint(0, 3)):
            kind = rng.random()
            if kind < 0.3:
                words.append(rng.choice(["-x", "word", "a", "b", "--flag=1"]))
            elif kind < 0.8:
                words.append(_quoted(rng))
            elif kind < 0.88:
                words.append("\\" + rng.choice(["|", ";", "&", ">", "<", " ", "'", '"', "#"]))
            elif kind < 0.95:
                words.append(_ansi(rng))
            else:
                words.append(_comment(rng))
        commands.append(" ".join(words))
    joined = commands[0]
    for command in commands[1:]:
        joined += rng.choice(_OPS) + command
    return joined


def _changed(rng: random.Random, line: str) -> str:
    chars = list(line)
    for _ in range(rng.randint(1, 3)):
        at = rng.randrange(len(chars) + 1)
        roll = rng.random()
        if roll < 0.5:
            chars.insert(at, rng.choice(_EDITS))
        elif roll < 0.75 and at < len(chars):
            del chars[at]
        elif at < len(chars):
            chars[at] = rng.choice(_EDITS)
    return "".join(chars)


def _line(rng: random.Random) -> str:
    base = _well_formed(rng)
    return base if rng.random() < 0.4 else _changed(rng, base)


def test_a_line_the_classifier_calls_read_only_runs_only_what_it_allowed(shell):
    rng = random.Random(106)
    read_only = 0
    bad: list[str] = []
    for _ in range(CASES):
        line = _line(rng)
        if classify_command(line).classification != "read_only":
            continue
        read_only += 1
        if damage := _damage(*shell.run(line)):
            bad.append(f"{line!r} -> {damage}")

    assert not bad, "\n".join(bad[:10])
    # The generator has to reach the lines the property is about.
    assert read_only >= CASES // 20, f"only {read_only} of {CASES} lines were read-only"
