"""Run a shell command on a managed host.

This is the tool the whole safety model exists for. Three gates apply
before anything reaches a host, in order:

1. **Scope** — the host must be in the session's allowlist. The model
   cannot widen its own blast radius by naming another host.
2. **Classification** — :func:`app.ai.safety.classify_command` decides
   what the command actually does, default-denying anything unrecognised.
3. **Autonomy** — the session's level decides whether a mutating command
   runs, is refused, or (from phase 3) becomes an approval request.

Output is truncated and redacted before it is returned, so neither an
enormous log dump nor a leaked credential reaches the provider.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncssh
from sqlalchemy import select

from app.ai.redaction import redact
from app.ai.safety import classify_command, is_allowed
from app.ai.tools.base import ToolContext, ToolResult, tool
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
from app.models.host import Host
from app.models.ssh_key import SSHKey
from app.ssh_utils import HostKeyMismatchError, ssh_connect_host

logger = logging.getLogger(__name__)

# Enough for a long `journalctl` page without letting one command flood the
# context window (and the token bill) with a multi-megabyte log.
MAX_OUTPUT_CHARS = 12_000

# Per-command wall clock. The session-level cap in the loop bounds the run
# as a whole; this stops one hung command eating all of it.
COMMAND_TIMEOUT_SECONDS = 120


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    omitted = len(text) - (half * 2)
    return (
        f"{text[:half]}\n\n"
        f"... [{omitted} characters omitted by LabDog; narrow the command "
        f"with grep, head, or --since to see the rest] ...\n\n"
        f"{text[-half:]}"
    )


@tool(
    name="run_ssh_command",
    description=(
        "Run a shell command on a managed host over SSH and return its output. "
        "Read-only commands (status, logs, package queries, network state) run "
        "immediately. Commands that would modify the host are refused unless "
        "this session's autonomy level permits them, and a small set of "
        "destructive commands is always blocked. Prefer specific, bounded "
        "commands: use --since and -n on journalctl, and grep or head to "
        "narrow large output."
    ),
    parameters={
        "type": "object",
        "properties": {
            "host_id": {
                "type": "integer",
                "description": "Host id from list_hosts.",
            },
            "command": {
                "type": "string",
                "description": "The shell command to run.",
            },
            "purpose": {
                "type": "string",
                "description": (
                    "One short sentence on why you are running this, shown to "
                    "the operator in the audit trail and any approval prompt."
                ),
            },
        },
        "required": ["host_id", "command"],
        "additionalProperties": False,
    },
    # The ceiling: an individual call is re-classified from its arguments.
    classification="mutating",
)
async def _run_ssh_command(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    host_id = args.get("host_id")
    command = (args.get("command") or "").strip()

    if not isinstance(host_id, int):
        return ToolResult("host_id must be an integer.", ok=False, classification="read_only")
    if not command:
        return ToolResult("command must not be empty.", ok=False, classification="read_only")

    # Gate 1: scope.
    if ctx.target_host_ids and host_id not in ctx.target_host_ids:
        return ToolResult(
            f"Host {host_id} is not in scope for this session. You may only run "
            f"commands on hosts returned by list_hosts.",
            ok=False,
            classification="denied",
            summary="out of scope",
        )
    if not ctx.target_host_ids:
        return ToolResult(
            "This session has no target hosts, so no command can be run.",
            ok=False,
            classification="denied",
            summary="no target hosts",
        )

    # Gate 2: what does this command actually do?
    verdict = classify_command(command)

    # Gate 3: is that permitted here?
    allowed, reason = is_allowed(verdict, ctx.autonomy_level)
    if not allowed:
        logger.info(
            "ai session %s: refused command on host %s (%s)",
            ctx.session_id,
            host_id,
            verdict.classification,
        )
        return ToolResult(
            f"{reason}\n\nThe command was not run. If you need this change, "
            f"explain what it would do and why, and stop — the operator will "
            f"decide.",
            ok=False,
            target_host_id=host_id,
            classification=verdict.classification,
            summary=reason[:200],
        )

    host = (await ctx.db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if host is None:
        return ToolResult(f"No host with id {host_id}.", ok=False, classification="read_only")
    if not host.ssh_key_id:
        return ToolResult(
            f"Host {host.hostname} has no SSH key assigned in LabDog.",
            ok=False,
            target_host_id=host_id,
            classification="read_only",
        )

    ssh_key = (
        await ctx.db.execute(select(SSHKey).where(SSHKey.id == host.ssh_key_id))
    ).scalar_one_or_none()
    if ssh_key is None:
        return ToolResult(
            f"The SSH key assigned to {host.hostname} no longer exists.",
            ok=False,
            target_host_id=host_id,
            classification="read_only",
        )

    try:
        private_pem = decrypt_ssh_key(ssh_key.encrypted_private_key, get_master_key())
        imported_key = asyncssh.import_private_key(private_pem)
    except Exception as exc:
        logger.warning("ai session %s: SSH key unusable: %s", ctx.session_id, exc)
        return ToolResult(
            f"Could not load the SSH key for {host.hostname}.",
            ok=False,
            target_host_id=host_id,
            classification="read_only",
        )

    try:
        async with ssh_connect_host(host, ctx.db, client_keys=[imported_key]) as conn:
            result = await asyncio.wait_for(
                conn.run(command, check=False), timeout=COMMAND_TIMEOUT_SECONDS
            )
    except HostKeyMismatchError as exc:
        return ToolResult(
            f"SSH host key mismatch for {host.hostname} — refusing to connect. "
            f"This may indicate a man-in-the-middle, or the host was rebuilt. "
            f"An operator must resolve it. ({exc})",
            ok=False,
            target_host_id=host_id,
            classification=verdict.classification,
            summary="host key mismatch",
        )
    except TimeoutError:
        return ToolResult(
            f"The command timed out after {COMMAND_TIMEOUT_SECONDS}s on "
            f"{host.hostname}. Try a bounded variant.",
            ok=False,
            target_host_id=host_id,
            classification=verdict.classification,
            summary="timed out",
        )
    except (OSError, asyncssh.Error) as exc:
        return ToolResult(
            f"Could not run the command on {host.hostname}: {exc}",
            ok=False,
            target_host_id=host_id,
            classification=verdict.classification,
            summary="ssh error",
        )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    exit_status = result.exit_status

    parts = [f"exit status: {exit_status}"]
    if stdout:
        parts.append(f"stdout:\n{stdout}")
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    if not stdout and not stderr:
        parts.append("(no output)")

    # Redact before truncating so a secret cannot survive by sitting in the
    # part that gets cut and re-joined.
    body = redact("\n\n".join(parts))
    return ToolResult(
        _truncate(body),
        ok=exit_status == 0,
        target_host_id=host_id,
        classification=verdict.classification,
        summary=f"{command[:120]} (exit {exit_status})",
    )


RUN_SSH_COMMAND = _run_ssh_command
