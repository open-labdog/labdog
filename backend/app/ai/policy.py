"""The command policy: which commands an AI session may run as reads.

:mod:`app.ai.safety` decides what a command line does. It splits the line,
refuses what can never be read (substitution, the denylist, a redirect to a
file) and hands each command to :meth:`CommandPolicy.judge`, which decides
from data in ``command_policy.yaml`` whether that command only reads.

The data used to be Python: a set of read-only command names and, for some
of them, the subcommands known to write. That was a denylist with an
allowlist's name. ``docker`` was a read unless one of 23 subcommands
appeared, so ``docker pause`` and ``docker rename`` were reads; ``qm`` was a
read unless one of 13 appeared, so ``qm guest exec`` ran any command in any
VM; ``ip`` was a read unless a whole word was ``add`` or ``set``, so
``ip l s eth0 down`` took an interface down (SEC-38). Here a command is
read-only only in a form the policy lists, and every word it cannot place
makes it a change. The file's own header describes the format.

The policy is plain data on purpose: an operator will be able to extend it
without a release, and a reviewer can read it as a list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

POLICY_FILE = Path(__file__).with_name("command_policy.yaml")

MODES = ("any", "operands", "exact")

#: Commands that run other commands or code, so nothing about them can be
#: read from the command line. The policy refuses to call any of them
#: read-only, whatever a policy file says: a rule for ``awk`` or ``xargs``
#: would make every command a read.
NEVER_READ_ONLY = frozenset(
    {
        "sh", "bash", "zsh", "dash", "ksh", "ash", "fish", "csh", "tcsh", "busybox",
        "python", "python2", "python3", "perl", "ruby", "php", "node", "nodejs", "lua", "tclsh",
        "awk", "gawk", "mawk", "nawk", "sed", "xargs", "parallel", "watch", "eval", "exec",
        "source", ".", "su", "runuser", "chroot", "nsenter", "unshare", "systemd-run",
        "script", "expect", "tmux", "screen", "ssh", "scp", "sftp", "rsync", "socat",
        "nc", "ncat", "telnet",
    }
)  # fmt: skip

#: Where a command may be named by its full path. Anything else with a
#: ``/`` in it, ``./cat`` or ``/tmp/cat``, is not the system's ``cat``.
SYSTEM_DIRS = ("/usr/bin/", "/bin/", "/usr/sbin/", "/sbin/", "/usr/local/bin/", "/usr/local/sbin/")

_TOP_LEVEL_KEYS = {"version", "read_only", "options", "gates", "wrappers", "environment"}
_OPTION_KEYS = ("flags", "values", "optional", "clusters")

_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.S)
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*\*?")
_SHORT_LETTER = re.compile(r"[A-Za-z0-9#]")

#: How many read-only forms of a command a refusal lists, so the model can
#: pick one instead of guessing.
_HINT_LIMIT = 12


class PolicyError(ValueError):
    """The policy file says something the policy cannot mean."""


def command_name(word: str) -> str | None:
    """The command a word names, or ``None`` if it names a file instead.

    ``/usr/bin/docker`` is ``docker``. ``./docker`` and ``/tmp/docker`` are
    whatever someone put there, so they name nothing the policy knows.
    """
    if "/" not in word:
        return word
    if ".." in word.split("/") or not word.startswith(SYSTEM_DIRS):
        return None
    name = word.rsplit("/", 1)[-1]
    return name or None


@dataclass(frozen=True)
class Options:
    """How to read the options of one command."""

    flags: frozenset[str] = frozenset()
    values: frozenset[str] = frozenset()
    #: Options whose value, if any, is attached: ``-Lalways``, ``--color=auto``.
    #: The next word is never theirs.
    optional: frozenset[str] = frozenset()
    #: Whether ``-abc`` means ``-a -b -c``, as getopt reads it.
    clusters: bool = False

    def merged(self, other: Options) -> Options:
        return Options(
            self.flags | other.flags,
            self.values | other.values,
            self.optional | other.optional,
            self.clusters or other.clusters,
        )

    @property
    def names(self) -> frozenset[str]:
        return self.flags | self.values | self.optional

    def takes_value(self, letter: str) -> bool:
        return f"-{letter}" in self.values

    def attaches_value(self, letter: str) -> bool:
        return f"-{letter}" in self.optional

    def consume(self, args: list[str], i: int) -> int:
        """How many words the option at ``args[i]`` takes up, or 0 if unknown."""
        word = args[i]
        has_next = i + 1 < len(args)
        name, eq, _ = word.partition("=")
        if word.startswith("--"):
            if name in self.values:
                return 1 if eq or not has_next else 2
            return 1 if name in self.flags or name in self.optional else 0
        if word in self.flags or word in self.optional:
            return 1
        if word in self.values:
            return 2 if has_next else 1
        # `-color=always`, the single-dash long form some tools take.
        if eq and name in self.names:
            return 1
        if self.clusters and len(word) > 2:
            for k, letter in enumerate(word[1:], start=1):
                if f"-{letter}" in self.flags:
                    continue
                if self.takes_value(letter):
                    # The rest of the word is its value, or the next word is.
                    return 1 if k + 1 < len(word) or not has_next else 2
                if self.attaches_value(letter):
                    return 1
                return 0
            return 1
        return 0


@dataclass(frozen=True)
class Gate:
    """An argument that makes an otherwise read-only command a change."""

    reason: str
    short: str = ""
    long: tuple[str, ...] = ()
    words: frozenset[str] = frozenset()
    operands: int | None = None
    pattern: re.Pattern[str] | None = None
    if_value: re.Pattern[str] | None = None
    unless_value: re.Pattern[str] | None = None

    def fires(self, args: list[str], options: Options) -> bool:
        if self.words and any(arg in self.words for arg in args):
            return True
        if self.pattern is not None and self.pattern.search(" ".join(args)):
            return True
        if self.operands is not None and _count_operands(args, options) > self.operands:
            return True
        if self.short or self.long:
            for value in self._occurrences(args, options):
                if self._value_fires(value):
                    return True
        return False

    def _value_fires(self, value: str | None) -> bool:
        if self.if_value is not None:
            return value is not None and self.if_value.fullmatch(value) is not None
        if self.unless_value is not None:
            return value is None or self.unless_value.fullmatch(value) is None
        return True

    def _needs_value(self) -> bool:
        return self.if_value is not None or self.unless_value is not None

    def _occurrences(self, args: list[str], options: Options):
        """Yield the value (or ``None``) of each use of this gate's options.

        Reads every word, including those after ``--``: a word that looks
        like an option but is not one can only make the gate fire when it
        need not have, never the other way round.
        """
        i = 0
        while i < len(args):
            word = args[i]
            nxt = args[i + 1] if i + 1 < len(args) else None
            if word.startswith("--") and len(word) > 2:
                name, eq, attached = word[2:].partition("=")
                if self._matches_long(name, options):
                    if eq:
                        yield attached
                    elif self._needs_value():
                        yield nxt
                        i += 1
                    else:
                        yield None
                elif not eq and f"--{name}" in options.values:
                    i += 1
            elif word.startswith("-") and len(word) > 1:
                for k, letter in enumerate(word[1:], start=1):
                    rest = word[k + 1 :]
                    if letter in self.short:
                        if self._needs_value() or options.takes_value(letter):
                            value = rest if rest else nxt
                            yield value
                            if not rest:
                                i += 1
                        else:
                            yield None
                        break
                    if options.takes_value(letter):
                        if not rest:
                            i += 1
                        break
                    if options.attaches_value(letter):
                        break
            i += 1

    def _matches_long(self, name: str, options: Options) -> bool:
        if not name:
            return False
        for gated in self.long:
            if name == gated:
                return True
            # GNU getopt and curl take any unambiguous prefix, so `--out`
            # is `--output`. An exact name the command declares is its own
            # option, not an abbreviation: `--cursor`, not `--cursor-file`.
            if gated.startswith(name) and f"--{name}" not in options.names:
                return True
        return False


def _count_operands(args: list[str], options: Options) -> int:
    """Plain words, after the values of the options that take one.

    An option the command does not declare is read as a flag, so a value it
    takes is counted as an operand: that can only make a gate fire.
    """
    count = 0
    i = 0
    while i < len(args):
        word = args[i]
        if word == "--":
            return count + len(args) - i - 1
        if word.startswith("-") and word != "-":
            i += options.consume(args, i) or 1
            continue
        count += 1
        i += 1
    return count


@dataclass
class Node:
    """One word of a command path, and what may follow it."""

    path: tuple[str, ...]
    mode: str | None = None
    children: dict[str, Node] = field(default_factory=dict)
    options: Options = field(default_factory=Options)
    gates: list[Gate] = field(default_factory=list)

    def child_for(self, word: str) -> Node | None:
        if word in self.children:
            return self.children[word]
        for key, child in self.children.items():
            if len(key) > 1 and key.endswith("*") and word.startswith(key[:-1]):
                return child
        if not word.startswith("-") and "*" in self.children:
            return self.children["*"]
        return None

    def read_only_forms(self) -> list[str]:
        forms: list[str] = []
        stack = [self]
        while stack and len(forms) < _HINT_LIMIT:
            node = stack.pop(0)
            if node.mode is not None and node is not self:
                forms.append(" ".join(node.path))
            stack.extend(node.children.values())
        return forms


@dataclass(frozen=True)
class Wrapper:
    """A command that runs the rest of the line, such as ``sudo``."""

    options: Options
    #: Plain words it takes before the command: ``timeout``'s duration.
    operands: int = 0
    #: Whether ``NAME=value`` words may come before the command, as with
    #: ``env`` and ``sudo``.
    assignments: bool = False


@dataclass(frozen=True)
class Judgement:
    read_only: bool
    reason: str


class CommandPolicy:
    """The policy, loaded and checked, ready to judge commands."""

    def __init__(
        self,
        root: dict[str, Node],
        wrappers: dict[str, Wrapper],
        environment: tuple[str, ...],
    ) -> None:
        self._root = root
        self._wrappers = wrappers
        self._environment = environment

    # -- loading ------------------------------------------------------------

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> CommandPolicy:
        """Build a policy from parsed YAML, or raise :class:`PolicyError`."""
        if not isinstance(data, dict):
            raise PolicyError("the policy is not a mapping")
        _only_keys(data, _TOP_LEVEL_KEYS, "")
        if data.get("version") != 1:
            raise PolicyError("version must be 1")

        wrappers = {
            str(name): _wrapper(str(name), spec or {})
            for name, spec in (data.get("wrappers") or {}).items()
        }

        root: dict[str, Node] = {}
        for raw_path, mode in (data.get("read_only") or {}).items():
            words = _path(raw_path)
            if mode not in MODES:
                raise PolicyError(f"{raw_path}: the mode must be one of {', '.join(MODES)}")
            head = words[0]
            if head in NEVER_READ_ONLY or head in wrappers:
                raise PolicyError(
                    f"{raw_path}: {head} runs other commands, so it is never read-only"
                )
            node = _node_at(root, words, create=True)
            if node.mode is not None:
                raise PolicyError(f"{raw_path}: listed twice")
            node.mode = mode

        for raw_path, spec in (data.get("options") or {}).items():
            node = _node_at(root, _path(raw_path), create=False)
            if node is None:
                raise PolicyError(f"options for {raw_path}: no read-only command starts with it")
            node.options = _options(spec or {}, f"options for {raw_path}")

        for raw_path, specs in (data.get("gates") or {}).items():
            node = _node_at(root, _path(raw_path), create=False)
            if node is None:
                raise PolicyError(f"gates for {raw_path}: no read-only command starts with it")
            if not isinstance(specs, list):
                raise PolicyError(f"gates for {raw_path}: must be a list")
            node.gates = [_gate(spec, f"gates for {raw_path}") for spec in specs]

        for node, effective in _walk(root):
            if node.mode == "any" and node.children:
                raise PolicyError(
                    f"{' '.join(node.path)}: anything may follow it, so its subcommands "
                    f"{', '.join(sorted(node.children))} mean nothing"
                )
            _check_gate_values(node, effective)

        environment = tuple(str(name) for name in data.get("environment") or [])
        for name in environment:
            if not _ENV_NAME.fullmatch(name):
                raise PolicyError(f"environment: {name!r} is not a variable name")
        return cls(root, wrappers, environment)

    @classmethod
    def load(cls, path: Path = POLICY_FILE) -> CommandPolicy:
        return cls.from_data(load_data(path))

    # -- judging ------------------------------------------------------------

    def unwrap(self, tokens: list[str]) -> tuple[list[str], str | None]:
        """Strip assignments and wrappers, returning the command they run.

        The second value is a refusal: a wrapper option or an environment
        variable this policy does not know, either of which can change what
        actually runs.
        """
        i = 0
        refusal = self._assignments(tokens, i)
        if isinstance(refusal, str):
            return [], refusal
        i = refusal
        while i < len(tokens):
            name = command_name(tokens[i])
            wrapper = self._wrappers.get(name) if name else None
            if wrapper is None:
                break
            label = tokens[i]
            i += 1
            while i < len(tokens) and tokens[i].startswith("-") and tokens[i] != "-":
                if tokens[i] == "--":
                    i += 1
                    break
                used = wrapper.options.consume(tokens, i)
                if not used:
                    return [], (
                        f"{label} {tokens[i]}: an option of {label} this policy does not know, "
                        f"so what it runs cannot be read and it is treated as a write"
                    )
                i += used
            if wrapper.assignments:
                result = self._assignments(tokens, i)
                if isinstance(result, str):
                    return [], result
                i = result
            i = min(i + wrapper.operands, len(tokens))
        return tokens[i:], None

    def _assignments(self, tokens: list[str], i: int) -> int | str:
        while i < len(tokens) and (match := _ASSIGNMENT.fullmatch(tokens[i])):
            name = match.group(1)
            if not self.env_allowed(name):
                return (
                    f"{name} can change what the command actually runs, so a command given it "
                    f"is treated as a write"
                )
            i += 1
        return i

    def env_allowed(self, name: str) -> bool:
        for allowed in self._environment:
            if allowed.endswith("*"):
                if name.startswith(allowed[:-1]):
                    return True
            elif name == allowed:
                return True
        return False

    def judge(self, tokens: list[str]) -> Judgement:
        """Whether the command in ``tokens`` only reads, and why."""
        name = command_name(tokens[0])
        if name is None:
            return _refuse(
                f"{tokens[0]!r} is a path, not a command on the system's own path, "
                f"so it is treated as a write"
            )
        node = self._root.get(name)
        if node is None:
            return _refuse(
                f"{name!r} is not a known read-only command, so it is treated as a write"
            )

        args = tokens[1:]
        options = node.options
        gates = list(node.gates)
        seen = [name]
        i = 0
        while True:
            if node.mode == "any":
                return _gated(gates, args[i:], options, seen)
            start = i
            while i < len(args):
                word = args[i]
                if node.child_for(word) is not None:
                    break
                if word == "--":
                    i += 1
                    if node.mode == "operands":
                        return _gated(gates, args[start:], options, seen)
                    break
                if not word.startswith("-") or word == "-":
                    break
                used = options.consume(args, i)
                if not used:
                    return _refuse(_unknown_option(seen, word))
                i += used
            if i == len(args):
                if node.mode in ("exact", "operands"):
                    return _gated(gates, args[start:], options, seen)
                return _refuse(_not_known(node, seen, None))
            word = args[i]
            child = node.child_for(word)
            if child is not None:
                node = child
                options = options.merged(child.options)
                gates.extend(child.gates)
                seen.append(word)
                i += 1
                continue
            if node.mode == "operands":
                bad = _first_unknown_option(args[i:], options)
                if bad is not None:
                    return _refuse(_unknown_option(seen, bad))
                return _gated(gates, args[start:], options, seen)
            if node.mode == "exact":
                return _refuse(
                    f"{' '.join(seen)!r} is read-only only on its own, and {word!r} "
                    f"makes it something else, so it is treated as a write"
                )
            return _refuse(_not_known(node, seen, word))


# -- helpers ----------------------------------------------------------------


def load_data(path: Path = POLICY_FILE) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _refuse(reason: str) -> Judgement:
    return Judgement(False, reason)


def _unknown_option(seen: list[str], option: str) -> str:
    return (
        f"{' '.join((*seen, option))!r}: {option} is an option this policy does not know there, "
        f"so the command cannot be read and is treated as a write"
    )


def _not_known(node: Node, seen: list[str], word: str | None) -> str:
    if word is None:
        reason = (
            f"{' '.join(seen)!r} on its own is not a known read-only command, "
            f"so it is treated as a write"
        )
    else:
        reason = (
            f"{' '.join((*seen, word))!r} is not a known read-only command, "
            f"so it is treated as a write"
        )
    forms = node.read_only_forms()
    if forms:
        more = ", …" if len(forms) >= _HINT_LIMIT else ""
        reason += f" (read-only forms include {', '.join(forms)}{more})"
    return reason


def _gated(gates: list[Gate], args: list[str], options: Options, seen: list[str]) -> Judgement:
    for gate in gates:
        if gate.fires(args, options):
            return _refuse(gate.reason)
    return Judgement(True, f"{' '.join(seen)} only reports state")


def _first_unknown_option(args: list[str], options: Options) -> str | None:
    i = 0
    while i < len(args):
        word = args[i]
        if word == "--":
            return None
        if word.startswith("-") and word != "-":
            used = options.consume(args, i)
            if not used:
                return word
            i += used
            continue
        i += 1
    return None


def _path(raw: Any) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        raise PolicyError(f"{raw!r}: a command path is a non-empty string")
    words = raw.split()
    head = words[0]
    if "*" in head or "/" in head:
        raise PolicyError(f"{raw}: the command itself must be named, without * or a path")
    for word in words[1:]:
        if "*" in word[:-1]:
            raise PolicyError(f"{raw}: * may only end a word")
    return words


def _node_at(root: dict[str, Node], words: list[str], *, create: bool) -> Node | None:
    head = words[0]
    node = root.get(head)
    if node is None:
        if not create:
            return None
        node = root[head] = Node((head,))
    for word in words[1:]:
        child = node.children.get(word)
        if child is None:
            if not create:
                return None
            child = node.children[word] = Node((*node.path, word))
        node = child
    return node


def _walk(root: dict[str, Node]):
    """Every node, with the options in force at it."""
    stack = [(node, node.options) for node in root.values()]
    while stack:
        node, effective = stack.pop()
        yield node, effective
        stack.extend((child, effective.merged(child.options)) for child in node.children.values())


def _only_keys(spec: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(spec) - allowed
    if unknown:
        prefix = f"{where}: " if where else ""
        raise PolicyError(f"{prefix}unknown key {', '.join(sorted(map(str, unknown)))}")


def _option_list(spec: dict[str, Any], key: str, where: str) -> frozenset[str]:
    values = spec.get(key) or []
    if not isinstance(values, list):
        raise PolicyError(f"{where}: {key} must be a list")
    for value in values:
        if not isinstance(value, str) or not value.startswith("-") or value in ("-", "--"):
            raise PolicyError(f"{where}: {value!r} in {key} is not an option")
    return frozenset(values)


def _options(spec: dict[str, Any], where: str) -> Options:
    if not isinstance(spec, dict):
        raise PolicyError(f"{where}: must be a mapping")
    _only_keys(spec, set(_OPTION_KEYS), where)
    options = Options(
        _option_list(spec, "flags", where),
        _option_list(spec, "values", where),
        _option_list(spec, "optional", where),
        bool(spec.get("clusters", False)),
    )
    overlap = (options.flags & options.values) | (options.flags & options.optional)
    overlap |= options.values & options.optional
    if overlap:
        raise PolicyError(f"{where}: {', '.join(sorted(overlap))} declared twice")
    return options


def _wrapper(name: str, spec: dict[str, Any]) -> Wrapper:
    where = f"wrapper {name}"
    if not isinstance(spec, dict):
        raise PolicyError(f"{where}: must be a mapping")
    _only_keys(spec, {*_OPTION_KEYS, "operands", "assignments"}, where)
    option_spec = {key: value for key, value in spec.items() if key in _OPTION_KEYS}
    operands = spec.get("operands", 0)
    if not isinstance(operands, int) or operands < 0:
        raise PolicyError(f"{where}: operands must be a whole number")
    return Wrapper(_options(option_spec, where), operands, bool(spec.get("assignments", False)))


def _regex(value: Any, where: str) -> re.Pattern[str] | None:
    if value is None:
        return None
    try:
        return re.compile(str(value))
    except re.error as exc:
        raise PolicyError(f"{where}: {value!r} is not a regular expression ({exc})") from exc


def _gate(spec: Any, where: str) -> Gate:
    if not isinstance(spec, dict):
        raise PolicyError(f"{where}: each gate is a mapping")
    _only_keys(
        spec,
        {"reason", "short", "long", "words", "operands", "pattern", "if_value", "unless_value"},
        where,
    )
    reason = spec.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise PolicyError(f"{where}: every gate needs a reason, which is what the model is told")
    short = str(spec.get("short") or "")
    if any(not _SHORT_LETTER.fullmatch(letter) for letter in short):
        raise PolicyError(f"{where}: short is a string of option letters")
    long_names = tuple(str(name) for name in spec.get("long") or [])
    if any(not name or name.startswith("-") for name in long_names):
        raise PolicyError(f"{where}: long names are written without their dashes")
    operands = spec.get("operands")
    if operands is not None and (not isinstance(operands, int) or operands < 0):
        raise PolicyError(f"{where}: operands must be a whole number")
    gate = Gate(
        reason=reason.strip(),
        short=short,
        long=long_names,
        words=frozenset(str(word) for word in spec.get("words") or []),
        operands=operands,
        pattern=_regex(spec.get("pattern"), where),
        if_value=_regex(spec.get("if_value"), where),
        unless_value=_regex(spec.get("unless_value"), where),
    )
    if not (gate.short or gate.long or gate.words or gate.pattern or gate.operands is not None):
        raise PolicyError(f"{where}: a gate must say what it fires on")
    if (gate.if_value or gate.unless_value) and not (gate.short or gate.long):
        raise PolicyError(f"{where}: if_value and unless_value apply to an option's value")
    if gate.if_value and gate.unless_value:
        raise PolicyError(f"{where}: give if_value or unless_value, not both")
    return gate


def _check_gate_values(node: Node, options: Options) -> None:
    """A gate that reads a short option's value needs to know it takes one.

    Otherwise the cluster ``-sXPOST`` would be read with ``P``, ``O``, ``S``
    and ``T`` as further options rather than as the method.
    """
    for gate in node.gates:
        if gate.if_value or gate.unless_value:
            for letter in gate.short:
                if not options.takes_value(letter):
                    raise PolicyError(
                        f"gates for {' '.join(node.path)}: -{letter} is read for its value, "
                        f"so it must be declared under values"
                    )


#: The policy LabDog ships, loaded once at import.
DEFAULT = CommandPolicy.load()
