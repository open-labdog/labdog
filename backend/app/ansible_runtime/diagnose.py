"""Translate an ansible-runner failure into a human-friendly message.

The default `f"Ansible runner status: {runner.status}, rc: {runner.rc}"`
is opaque to operators reading the UI — every failure looks the same.
This module walks the runner's structured events for the actual
`runner_on_unreachable` / `runner_on_failed` payloads, then matches the
extracted `msg` against a small set of well-known causes (host out of
disk, SSH refused, auth denied, etc.) so the UI can show something
actionable.
"""

from __future__ import annotations

from typing import Any

# (substring, friendly message) pairs. Order matters — the first match
# wins, so put more specific patterns above more generic ones.
_KNOWN_CAUSES: tuple[tuple[str, str], ...] = (
    (
        "No space left on device",
        "Target host is out of disk space — Ansible could not create its temp directory.",
    ),
    (
        "Disk quota exceeded",
        "Target host disk quota exceeded for the SSH user.",
    ),
    (
        "Connection refused",
        "SSH connection refused — sshd is not listening on the configured port.",
    ),
    (
        "Connection timed out",
        "SSH connection timed out — host is not reachable on the network.",
    ),
    (
        "No route to host",
        "No route to target host — check network/firewall between LabDog and the host.",
    ),
    (
        "Host key verification failed",
        "SSH host key verification failed — the target's host key changed unexpectedly.",
    ),
    (
        "Permission denied",
        "SSH authentication failed — key was rejected by the target.",
    ),
    (
        "Name or service not known",
        "DNS resolution failed for the target hostname.",
    ),
    (
        "Could not resolve hostname",
        "DNS resolution failed for the target hostname.",
    ),
    (
        "sudo: a password is required",
        "Sudo on the target requires a password — configure passwordless sudo for the SSH user.",
    ),
)


def _match_known_cause(msg: str) -> str | None:
    for needle, friendly in _KNOWN_CAUSES:
        if needle in msg:
            return friendly
    return None


def _extract_failure_msg(runner: Any) -> str | None:
    """Pull the first failure / unreachable message out of runner.events.

    Returns None if no structured failure event is found, in which case
    the caller should fall back to a generic summary.
    """
    events_iter = getattr(runner, "events", None)
    if events_iter is None:
        return None
    try:
        for event in events_iter:
            if not isinstance(event, dict):
                continue
            if event.get("event") not in ("runner_on_failed", "runner_on_unreachable"):
                continue
            data = event.get("event_data") or {}
            res = data.get("res") or {}
            msg = res.get("msg") or res.get("module_stderr") or res.get("stderr")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
    except Exception:
        # Event-stream iteration is best-effort; any IO error here just
        # falls back to the generic summary below.
        return None
    return None


def interpret_runner_failure(runner: Any) -> str:
    """Build a UI-facing error message for a failed ansible-runner result.

    Always returns a non-empty string. Prefers a friendly known-cause
    message; falls back to the raw Ansible `msg` truncated; final
    fallback is the generic status/rc summary.
    """
    raw = _extract_failure_msg(runner)
    if raw:
        friendly = _match_known_cause(raw)
        if friendly:
            return friendly
        # Truncate raw msg so it stays readable in a UI cell.
        return raw if len(raw) <= 240 else raw[:237] + "..."

    status = getattr(runner, "status", "unknown")
    rc = getattr(runner, "rc", "?")
    return f"Sync failed (ansible-runner status: {status}, rc: {rc})."
