"""Claude Code CLI backend.

Runs the locally installed ``claude`` binary. Useful when the operator
already has the CLI authenticated on the LabDog host and would rather not
manage a second API key.

**LabDog owns every tool.** The CLI is invoked with its own tools
disabled, so it can reason and answer but cannot touch the host itself —
all host access goes through LabDog's classified, allowlisted,
audit-logged tool layer. That is the whole point of the safety model, and
handing a second unaudited execution path to the same model would defeat
it.

Two modes, chosen by probing the installed CLI once:

- **tool mode** — ``--output-format stream-json --input-format stream-json``
  lets LabDog feed tool results back over stdin across turns.
- **single-shot** — one prompt in, text out. Used when the installed CLI
  is too old to round-trip tool results. The factory marks the provider
  ``supports_tools = False`` and the loop restricts it to reports and
  verify verdicts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator

from app.ai.providers.base import (
    LLMProviderError,
    NormalizedMessage,
    StreamEvent,
    TextDelta,
    ToolSpec,
    TurnEnd,
    Usage,
)

logger = logging.getLogger(__name__)

CLI_BINARY = "claude"
DEFAULT_TIMEOUT_SECONDS = 300


class ClaudeCLIProvider:
    """Single-shot Claude Code CLI backend.

    ``supports_tools`` is False: the loop uses this for report generation
    and verify verdicts, where one prompt in and one answer out is the
    whole interaction.
    """

    provider_type = "claude_cli"
    supports_tools = False

    def __init__(
        self,
        model: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds

    def _argv(self, prompt: str) -> list[str]:
        argv = [CLI_BINARY, "-p", prompt]
        if self.model:
            argv += ["--model", self.model]
        # Deny the CLI its own tools — host access belongs to LabDog's
        # classified tool layer, not to a second unaudited path.
        argv += ["--disallowedTools", "*"]
        return argv

    @staticmethod
    def _flatten(messages: list[NormalizedMessage]) -> str:
        """Render the conversation as one prompt.

        The CLI takes a single prompt string, so multi-turn context is
        folded into a labelled transcript.
        """
        parts: list[str] = []
        for msg in messages:
            if not msg.content:
                continue
            if msg.role == "system":
                parts.append(msg.content)
            elif msg.role == "user":
                parts.append(f"User: {msg.content}")
            elif msg.role == "assistant":
                parts.append(f"Assistant: {msg.content}")
            else:
                parts.append(f"Tool result: {msg.content}")
        return "\n\n".join(parts)

    async def stream_turn(
        self,
        messages: list[NormalizedMessage],
        tools: list[ToolSpec],
        *,
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[StreamEvent]:
        if tools:
            raise LLMProviderError(
                "The Claude CLI backend cannot execute tool calls. Configure an "
                "OpenAI-compatible or Anthropic provider for agentic sessions."
            )

        prompt = self._flatten(messages)
        try:
            # exec, never shell: the prompt is model- and operator-supplied
            # text and must never reach a shell interpreter.
            proc = await asyncio.create_subprocess_exec(
                *self._argv(prompt),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise LLMProviderError(
                f"The {CLI_BINARY!r} CLI is not installed on the LabDog host"
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise LLMProviderError(
                f"The {CLI_BINARY} CLI timed out after {self.timeout_seconds}s"
            ) from None

        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip()[:500]
            raise LLMProviderError(f"{CLI_BINARY} exited {proc.returncode}: {detail}")

        text = stdout.decode(errors="replace").strip()
        if text:
            yield TextDelta(text)
        # The CLI reports no token usage in this mode, so cost is a floor.
        yield Usage(prompt_tokens=0, completion_tokens=0, unknown=True)
        yield TurnEnd(stop_reason="end_turn")

    async def test_connection(self) -> str:
        path = shutil.which(CLI_BINARY)
        if not path:
            raise LLMProviderError(
                f"The {CLI_BINARY!r} CLI is not on PATH for the LabDog service user"
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                CLI_BINARY,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except (FileNotFoundError, TimeoutError) as exc:
            raise LLMProviderError(f"Could not run {CLI_BINARY} --version") from exc
        if proc.returncode != 0:
            raise LLMProviderError(stderr.decode(errors="replace").strip()[:300] or "CLI error")
        version = stdout.decode(errors="replace").strip()
        return f"Found {path} ({version}); single-shot mode, no tool calls"


async def cli_supports_stream_json() -> bool:
    """Probe whether the installed CLI can round-trip tool results.

    Used by the factory to decide between tool mode and single-shot. The
    check is deliberately cheap and fail-safe: anything unexpected means
    single-shot, which always works.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            CLI_BINARY,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (FileNotFoundError, TimeoutError, OSError):
        return False
    if proc.returncode != 0:
        return False
    help_text = stdout.decode(errors="replace")
    return "--input-format" in help_text and "stream-json" in help_text


def parse_stream_json_line(line: str) -> dict | None:
    """Parse one stream-json envelope, returning None for noise.

    Kept separate from the provider so the tool-mode transport can be
    built and tested against recorded CLI output.
    """
    line = line.strip()
    if not line:
        return None
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
