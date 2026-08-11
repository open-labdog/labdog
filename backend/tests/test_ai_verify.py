"""Reading a PASS/FAIL verdict, and reporting what could not be measured.

A FAIL here reverts a VM: ``action_host`` treats a failed verification as
a failed run, and a failed run with a snapshot and auto-rollback enabled
restores it. So the parser is a safety control, and it had two ways to
get the answer wrong that nothing tested.

Neither of these is a hypothetical. The first case below is how a model
answers "is this host healthy?" when the answer is yes.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from app.workflows.steps.ai_verify import UNAVAILABLE, parse_verdict, run_ai_verification


class TestReadingTheVerdict:
    @pytest.mark.parametrize(
        "reply",
        [
            "PASS",
            "PASS - all services came back up.",
            "pass: nothing to report",
            "PASS\n\nDetail follows.",
            "\n  PASS — leading blank line and whitespace",
        ],
    )
    def test_a_pass_is_read_as_a_pass(self, reply: str) -> None:
        assert parse_verdict(reply) is True

    @pytest.mark.parametrize(
        "reply",
        [
            "FAIL",
            "FAIL: nginx did not restart.",
            "fail — disk is full",
        ],
    )
    def test_a_fail_is_read_as_a_fail(self, reply: str) -> None:
        assert parse_verdict(reply) is False

    def test_an_english_pass_is_not_read_as_a_failure(self) -> None:
        """The bug this file exists for. The old parser searched the whole
        reply for substrings: "PASS" was absent, "FAIL" matched inside
        *failed*, and a healthy host was rolled back."""
        assert parse_verdict("Everything looks fine; nothing failed.") is None

    @pytest.mark.parametrize(
        "reply",
        [
            "FAILED to find any problems — the host is healthy.",
            "No checks failed.",
            "The upgrade did not FAIL.",
        ],
    )
    def test_the_word_fail_in_prose_is_not_a_verdict(self, reply: str) -> None:
        assert parse_verdict(reply) is None

    def test_a_verdict_buried_below_the_first_line_is_not_read(self) -> None:
        """Strict by design. The prompt asks for the verdict first, so a
        reply that buries it did not follow the contract — and guessing at
        one is exactly how a pass became a rollback."""
        assert parse_verdict("Here is my analysis.\nPASS") is None

    def test_an_empty_reply_has_no_verdict(self) -> None:
        assert parse_verdict("") is None
        assert parse_verdict("   \n\n  ") is None


class TestWhatAnUnreadableVerdictDoes:
    """It passes — and says so. Rolling a host back because nobody could
    read the answer would be inventing a failure."""

    def _run(self, stdout: str, stderr: str = ""):
        completed = subprocess.CompletedProcess(
            args=["claude"], returncode=0, stdout=stdout, stderr=stderr
        )
        with patch("subprocess.run", return_value=completed):
            return run_ai_verification({"hard_checks": {}}, "check it")

    def test_it_passes(self) -> None:
        assert self._run("Everything looks fine; nothing failed.")["passed"] is True

    def test_the_output_says_the_verdict_was_unreadable(self) -> None:
        """Otherwise the run detail shows a pass with no indication that
        nothing was actually decided."""
        result = self._run("Everything looks fine; nothing failed.")
        assert "no readable PASS/FAIL verdict" in result["output"]
        assert "nothing failed" in result["output"], "the reply itself should survive"

    def test_stderr_is_shown_when_there_is_no_stdout(self) -> None:
        """A CLI that failed leaves its reason on stderr. Without this the
        operator sees a pass and an empty explanation."""
        result = self._run("", stderr="Error: credit balance too low")
        assert "credit balance too low" in result["output"]

    def test_a_real_fail_still_fails(self) -> None:
        assert self._run("FAIL: nginx is not running")["passed"] is False


class TestUnavailableReadings:
    def _prompt_for(self, hard_checks: dict) -> str:
        completed = subprocess.CompletedProcess(
            args=["claude"], returncode=0, stdout="PASS", stderr=""
        )
        with patch("subprocess.run", return_value=completed) as run:
            run_ai_verification({"hard_checks": hard_checks}, "check it")
        return run.call_args.args[0][2]

    def test_a_missing_reading_is_marked_not_zeroed(self) -> None:
        """The collector reports None when a read failed. Rendering that as
        0 would describe a host whose checks all failed as the healthiest
        possible host."""
        prompt = self._prompt_for({"load": None, "disk_pct": None})
        assert prompt.count(UNAVAILABLE) == 2
        assert "System load: 0" not in prompt
        assert "Disk usage: 0" not in prompt

    def test_a_real_zero_is_still_reported_as_zero(self) -> None:
        """An idle host really does have a load of 0.0, and that has to
        stay distinguishable from a failed read."""
        prompt = self._prompt_for({"load": 0.0, "disk_pct": 12})
        assert "System load: 0.0" in prompt
        assert UNAVAILABLE not in prompt

    def test_the_prompt_warns_that_unavailable_is_not_healthy(self) -> None:
        prompt = self._prompt_for({"load": None, "disk_pct": 40})
        assert "means the check" in prompt


class TestTheFailOpenPaths:
    def test_a_missing_cli_passes(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = run_ai_verification({"hard_checks": {}}, "check it")
        assert result["passed"] is True
        assert "not available" in result["output"]

    def test_a_timeout_passes(self) -> None:
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=120)
        ):
            result = run_ai_verification({"hard_checks": {}}, "check it")
        assert result["passed"] is True
        assert "timed out" in result["output"]
