"""The classifier against the shell it guards.

``safety.py`` decides what a line will do by reading it. Bash is the
authority on what it does, so this runs the lines the classifier calls
read-only and checks the claim: nothing runs but the one command that was
allowed, and no file is created.

Three commands stand in for a host. ``a`` is the only head on the
allowlist; ``b`` and ``c`` are commands the classifier must never let
through, and each is a shell function that logs its own name, so a line
that gets one of them run says so. A read-only line that ran ``b``, or
made a file, is a line the classifier read differently from bash. Two
things have caused that: the quote-aware reading of BUG-106, which decides
which operators are syntax and which are data, and the redirect rules,
which let a file be written behind a digit (SEC-36) or an inline shell
(SEC-37).

The lines cover quoting (letters, the operators, quotes, escapes, ``#`` and
``$'``) and redirection (every spelling of one, with and without a number in
front, and inline shells). A fixed corpus first, then seeded random lines
built the same way, most of them a well-formed line with a character or two
changed, since a bug in either shows up where the line nearly balances. Set
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

# `a f` fails, so that `a f || b` runs `b`. The functions are exported so that
# `bash -c a` finds them. `trap wait EXIT` so a line ending in `&` is not
# judged before its background command has logged.
PRELUDE = (
    'a(){ echo a >>"$LOG"; [ "$1" != f ]; }; b(){ echo b >>"$LOG"; }; c(){ echo c >>"$LOG"; }; '
    "export -f a b c; trap wait EXIT\n"
)


#: Stands in for sudo, first on the PATH the lines run with. The lines
#: include `sudo sh -c ...`, and a developer machine or a CI runner may well
#: have passwordless sudo: run as the real thing, a line that writes a file
#: would write it as root. This one drops its options and runs the command
#: as whoever is running the tests.
FAKE_SUDO = """#!/bin/sh
while [ "$#" -gt 0 ]; do
  case "$1" in
    -u|-g|-p|-C|-h|-r|-t|-T|-U|-D|-R) shift 2 ;;
    --) shift; break ;;
    -*) shift ;;
    *) break ;;
  esac
done
exec "$@"
"""


@pytest.fixture(autouse=True)
def only_a_is_read_only(monkeypatch):
    monkeypatch.setattr(safety, "READ_ONLY_HEADS", frozenset({"a"}))


class Shell:
    """Runs one line in bash, in an empty directory, and reports the damage."""

    def __init__(self, root: Path) -> None:
        self.work = root / "work"
        self.work.mkdir()
        self.log = root / "log"
        bin_dir = root / "bin"
        bin_dir.mkdir()
        sudo = bin_dir / "sudo"
        sudo.write_text(FAKE_SUDO)
        sudo.chmod(0o755)
        self.path = f"{bin_dir}:{os.defpath}"

    def run(self, line: str) -> tuple[list[str], list[str]]:
        self.log.write_text("")
        subprocess.run(
            [BASH, "--norc", "--noprofile", "-c", PRELUDE + line],
            cwd=self.work,
            env={"LOG": str(self.log), "PATH": self.path, "LC_ALL": "C"},
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
    # A redirect to a file writes it, whatever is in front of the `>`.
    "a >x",
    "a 1>x",
    "a 2>x",
    "a 0>x",
    "a 2>>x",
    "a x9>x",
    "a 3<>x",
    "a '2'>x",
    "a \\2>x",
    # An inline shell's own redirects count, and so do its payload's.
    "bash -c a > x",
    "sh -c 'a' >> x",
    "env bash -c a 2>x",
    "sudo sh -c a > x",
    "bash -c 'a 2>x'",
    'sh -c "a >x"',
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
    # Redirects that write nothing.
    "a 2>/dev/null",
    "a >/dev/null",
    "a > /dev/null",
    "a 2>>/dev/null",
    "a >/dev/null 2>&1",
    "a 2>&1",
    "a >&2",
    "a 1>&2",
    "bash -c a 2>/dev/null",
]


@pytest.mark.parametrize("line", ATTACKS)
def test_an_attack_is_not_read_only_and_really_does_damage(shell, line):
    assert _damage(*shell.run(line)), "not an attack: bash runs only `a` and makes no file"
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
_EDITS += ["2", "1", "0", "x", "f"]
_OPS = [" | ", " | ", " | ", " ; ", " && ", " || ", " & ", "\n"]
_REDIRS = [
    *[">f", "> f", "1>f", "2>f", "2> f", "0>f", "2>>f", "x9>f", "9>f", "3<>f", "<>f", ">>f", ">|f"],
    *["&>f", ">&f", ">& f", "1>& f", "2>& f", "0<f", "<f", "3<f", "<<<f", "<<f"],
    *[">/dev/null", "2>/dev/null", "> /dev/null", "2>>/dev/null", ">/dev/null2", ">/dev/null/x"],
    *[">&2", "2>&1", "1>&2", ">&1", "2>&-"],
]


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


def _shell(rng: random.Random) -> str:
    """An inline shell, sometimes with a redirect of its own."""
    wrapper = rng.choice(["bash -c", "sh -c", "sudo sh -c", "env bash -c"])
    inner = rng.choice(["a", "a x", "a | a", "a; b", "a && c", "b", "a 'x|y'", "a >f", "a 2>f"])
    quote = rng.choice(["'", '"', ""])
    if not quote and any(ch in inner for ch in " ;|&>"):
        quote = "'"
    tail = rng.choice(["", "", " " + rng.choice(_REDIRS)])
    return f"{wrapper} {quote}{inner}{quote}{tail}"


def _well_formed(rng: random.Random) -> str:
    commands = []
    for _ in range(rng.randint(1, 4)):
        if rng.random() < 0.12:
            commands.append(_shell(rng))
            continue
        words = [rng.choice(["a", "a", "a", "b", "c"])]
        for _ in range(rng.randint(0, 3)):
            kind = rng.random()
            if kind < 0.3:
                words.append(rng.choice(["-x", "word", "a", "b", "--flag=1"]))
            elif kind < 0.7:
                words.append(_quoted(rng))
            elif kind < 0.78:
                words.append("\\" + rng.choice(["|", ";", "&", ">", "<", " ", "'", '"', "#"]))
            elif kind < 0.85:
                words.append(_ansi(rng))
            elif kind < 0.88:
                words.append(_comment(rng))
            else:
                words.append(rng.choice(_REDIRS))
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
