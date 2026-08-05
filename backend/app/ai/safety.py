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

# Splits a command line into pipeline segments. Deliberately naive about
# quoting: a segment boundary inside a quoted string yields a *more*
# conservative parse, never a less conservative one.
_SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|&\n]")

# Redirection makes an otherwise read-only command a writer.
_REDIRECT = re.compile(r"(?<![0-9<>])>{1,2}(?!&)|>\s*&\s*\d|\btee\b")


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


def _segments(command: str) -> list[str]:
    return [seg.strip() for seg in _SEGMENT_SPLIT.split(command) if seg.strip()]


def _tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        # Unbalanced quotes — fall back to whitespace so an unparseable
        # command still gets a head, and therefore still gets classified.
        return segment.split()


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """Drop sudo/env-style prefixes so the real command head is classified."""
    wrappers = {
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
        "eval",
        "exec",
    }
    idx = 0
    while idx < len(tokens) and tokens[idx] in wrappers:
        idx += 1
        # Skip the wrapper's own flags and any KEY=VALUE assignments.
        while idx < len(tokens) and (
            tokens[idx].startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[idx])
        ):
            idx += 1
    return tokens[idx:]


def _classify_segment(segment: str) -> Verdict:
    lowered = segment.lower()
    for pattern, reason in DENYLIST_PATTERNS:
        if pattern.search(lowered):
            return Verdict("denied", f"Blocked: {reason}", segment)

    tokens = _strip_wrappers(_tokenize(segment))
    if not tokens:
        return Verdict("unknown", "Could not determine what this command runs", segment)

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
            return Verdict(
                inner.classification,
                f"Inline shell script: {inner.reason}",
                inner.segment or segment,
            )
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

    if _REDIRECT.search(segment):
        return Verdict("mutating", "Output is redirected to a file", segment)

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

    severity: dict[Classification, int] = {
        "read_only": 0,
        "unknown": 1,
        "mutating": 2,
        "denied": 3,
    }
    worst = Verdict("read_only", "No command segments found", "")
    for segment in _segments(command):
        verdict = _classify_segment(segment)
        if severity[verdict.classification] > severity[worst.classification]:
            worst = verdict
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
