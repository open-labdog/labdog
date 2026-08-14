"""The AI verify step: what it shows the model, and what it reports back.

The step's own job is narrow — adapt what the SSH collector gathered into
an evidence pack, and render the decided verdict for the run log. The
session behaviour behind it is covered in ``tests/ai/test_verify_session``.

No database: the session is stubbed, because what is under test here is
the adaptation, not the agent.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.ai.evidence import UNAVAILABLE
from app.ai.verdict import FAILED, INCONCLUSIVE, PASSED, VerifyOutcome
from app.workflows.steps.ai_verify import evidence_from_state, run_ai_verification


def _fake_ssh():
    """Stand in for ``ssh_connect_host``, returning healthy readings.

    ``run_verification`` opens one connection and runs every check over
    it, so the checks have to succeed for the AI step to be reached at
    all — it only runs when the hard checks pass.
    """

    class _Result:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.exit_status = 0

    class _Conn:
        async def run(self, command: str, check: bool = False):  # noqa: ARG002, FBT001, FBT002
            if "loadavg" in command:
                return _Result("0.42 0.30 0.25 1/200 1234")
            if command.startswith("df"):
                return _Result("37%")
            if "journalctl" in command:
                return _Result("Aug 11 12:00:00 h kernel: something looked odd")
            return _Result("")

    @asynccontextmanager
    async def _cm(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        yield _Conn()

    return _cm


def _state(**hard) -> dict:
    """One ``run_verification`` result, with sensible readings unless
    overridden."""
    checks = {
        "services": [{"name": "nginx", "expected": "active", "actual": "active", "ok": True}],
        "packages": [{"name": "nginx", "expected": "latest", "installed": True, "ok": True}],
        "load": 0.42,
        "disk_pct": 37,
        "journal_errors": "",
    }
    checks.update(hard)
    return {"host_hostname": "jellyfin", "host_ip": "10.0.0.9", "hard_checks": checks}


def _rendered(**hard) -> str:
    from app.ai.evidence import render

    return render(evidence_from_state(_state(**hard)))


class TestWhatTheModelIsShown:
    def test_service_state_reaches_the_prompt(self) -> None:
        assert "nginx" in _rendered()

    def test_a_failed_service_is_marked_not_ok(self) -> None:
        text = _rendered(
            services=[{"name": "nginx", "expected": "active", "actual": "failed", "ok": False}]
        )
        assert "NOT OK" in text

    def test_a_missing_package_is_marked(self) -> None:
        text = _rendered(
            packages=[{"name": "nginx", "expected": "latest", "installed": False, "ok": False}]
        )
        assert "NOT INSTALLED" in text

    def test_every_reading_carries_its_source(self) -> None:
        text = _rendered()
        assert "cat /proc/loadavg" in text
        assert "journalctl" in text


class TestAGapIsNotAHealthyReading:
    def test_an_unread_load_average_is_marked_unavailable(self) -> None:
        """It used to default to 0.00, which describes a host whose
        checks all failed as the healthiest possible host."""
        text = _rendered(load=None)
        assert UNAVAILABLE in text
        assert "could not be read" in text

    def test_an_unread_disk_is_marked_unavailable(self) -> None:
        assert UNAVAILABLE in _rendered(disk_pct=None)

    def test_a_real_zero_load_is_still_a_reading(self) -> None:
        text = _rendered(load=0.0)
        assert "0.0" in text
        assert UNAVAILABLE not in text

    def test_a_real_zero_disk_is_still_a_reading(self) -> None:
        assert UNAVAILABLE not in _rendered(disk_pct=0)

    def test_an_empty_journal_says_it_was_read(self) -> None:
        """ "No errors" and "we never looked" must not render the same."""
        text = _rendered(journal_errors="")
        assert "the journal was read" in text
        assert UNAVAILABLE not in text

    def test_a_host_with_nothing_managed_says_so(self) -> None:
        text = _rendered(services=[], packages=[])
        assert "no services are managed" in text
        assert "no packages are managed" in text
        assert UNAVAILABLE not in text


def _stub_session(outcome: VerifyOutcome):
    """Patch out the session, leaving the step's own logic under test."""

    @asynccontextmanager
    async def _no_db():
        yield None

    async def _run(db, **kwargs):  # noqa: ANN001
        _run.kwargs = kwargs
        return outcome

    return (
        patch("app.db.task_session", _no_db),
        patch("app.ai.verify.run_verify_session", _run),
        _run,
    )


class TestWhatItReportsBack:
    async def test_a_pass_is_reported_plainly(self) -> None:
        outcome = VerifyOutcome(PASSED, True, "PASS — nginx is up.", session_id=7)
        cm_db, cm_run, _ = _stub_session(outcome)
        with cm_db, cm_run:
            result = await run_ai_verification(_state(), "Confirm nginx.")
        assert result == {
            "passed": True,
            "verdict": PASSED,
            "output": "PASS — nginx is up.",
            "session_id": 7,
        }

    async def test_a_fail_is_reported_as_a_failure(self) -> None:
        cm_db, cm_run, _ = _stub_session(VerifyOutcome(FAILED, False, "FAIL — nginx is down."))
        with cm_db, cm_run:
            result = await run_ai_verification(_state(), "Confirm nginx.")
        assert result["passed"] is False
        assert result["verdict"] == FAILED

    @pytest.mark.parametrize(
        ("fail_closed", "passed", "wording"),
        [(False, True, "treated as a pass"), (True, False, "failing this verification")],
    )
    async def test_an_inconclusive_verdict_says_which_way_it_resolved(
        self, fail_closed: bool, passed: bool, wording: str
    ) -> None:
        """The line an operator needs when a rollback happened on a reply
        that looked like it was arguing for a pass — or when a suspicious
        host was let through on a reply nobody could read."""
        outcome = VerifyOutcome(INCONCLUSIVE, passed, "Could not tell.")
        cm_db, cm_run, _ = _stub_session(outcome)
        with cm_db, cm_run:
            result = await run_ai_verification(_state(), "x", fail_closed=fail_closed)
        assert result["passed"] is passed
        assert wording in result["output"]
        assert "Could not tell." in result["output"]

    async def test_a_conclusive_verdict_does_not_lecture_about_policy(self) -> None:
        cm_db, cm_run, _ = _stub_session(VerifyOutcome(PASSED, True, "PASS — fine."))
        with cm_db, cm_run:
            result = await run_ai_verification(_state(), "x", fail_closed=True)
        assert "fail_closed" not in result["output"]


class TestWhatItPassesDown:
    async def test_the_policy_and_the_links_are_forwarded(self) -> None:
        cm_db, cm_run, spy = _stub_session(VerifyOutcome(PASSED, True, "PASS — fine."))
        with cm_db, cm_run:
            await run_ai_verification(
                _state(), "Confirm nginx.", fail_closed=True, host_id=3, action_run_id=99
            )
        assert spy.kwargs["fail_closed"] is True
        assert spy.kwargs["host_id"] == 3
        assert spy.kwargs["action_run_id"] == 99
        assert spy.kwargs["instructions"] == "Confirm nginx."
        assert spy.kwargs["hostname"] == "jellyfin"
        assert spy.kwargs["ip"] == "10.0.0.9"

    async def test_the_evidence_it_built_is_what_gets_sent(self) -> None:
        cm_db, cm_run, spy = _stub_session(VerifyOutcome(PASSED, True, "PASS — fine."))
        with cm_db, cm_run:
            await run_ai_verification(_state(load=None), "x")
        labels = {item.label for item in spy.kwargs["evidence"]}
        assert "System load (1 minute)" in labels
        assert any(not item.available for item in spy.kwargs["evidence"])


class TestADryRunDoesNotBuyAVerdict:
    """A preview must not open a billed AI session.

    Neither the snapshot nor the verify gate in ``action_host`` consults
    ``dry_run``, so a preview of a destructive action on a VM-mapped host
    reaches ``run_verification`` exactly like a real one. Check mode means
    the change never happened — so an AI verdict there describes the host
    as it already was, costs real money, and reads in the run detail like
    a verdict on a change that did happen.
    """

    async def _verify(self, *, dry_run: bool, prompt: str | None = "Confirm nginx.", **kw):
        from app.workflows.steps import verify as verify_step

        calls: list[dict] = []

        async def _spy(system_state, verification_prompt, **kwargs):  # noqa: ANN001
            calls.append(kwargs)
            return {"passed": True, "verdict": PASSED, "output": "PASS", "session_id": 1}

        host = SimpleNamespace(id=1, hostname="h", ip_address="10.0.0.9")
        with (
            patch.object(verify_step, "ssh_connect_host", _fake_ssh()),
            patch("app.workflows.steps.ai_verify.run_ai_verification", _spy),
        ):
            result = await verify_step.run_verification(
                host, "/tmp/key", [], [], prompt, None, dry_run=dry_run, **kw
            )
        return result, calls

    async def test_a_dry_run_skips_ai_verification(self) -> None:
        result, calls = await self._verify(dry_run=True)
        assert calls == []
        assert result["ai_result"] is None
        assert result["passed"] is True

    async def test_a_real_run_still_gets_one(self) -> None:
        """The guard is one condition wide — it must not disable the
        feature it is protecting."""
        result, calls = await self._verify(dry_run=False)
        assert len(calls) == 1
        assert result["ai_result"] is not None

    async def test_a_dry_run_skips_it_even_with_journal_errors(self) -> None:
        """The other trigger. Suppressing only the manifest prompt would
        leave a preview billing whenever the host happened to log an
        error in the last ten minutes."""
        _, calls = await self._verify(dry_run=True, prompt=None)
        assert calls == []


class TestAnUnreadableJournalIsNotAQuietOne:
    """The bug this class exists for was observed in production.

    LabDog connects to most hosts as an unprivileged user. ``journalctl``
    run by such a user prints only that user's own journal and **exits 0**,
    so a host that had just logged an error-priority entry reported none.
    The evidence rendered that as "the journal was read and had no
    error-priority entries", and the model repeated it back as proof of
    health and passed the host.
    """

    def test_an_unreadable_journal_is_marked_unavailable(self) -> None:
        text = _rendered(journal_errors=None, journal_error_reason="no sudo, unprivileged user")
        assert UNAVAILABLE in text
        assert "no sudo, unprivileged user" in text

    def test_it_does_not_claim_the_journal_was_read(self) -> None:
        """The exact phrasing that misled a verdict."""
        text = _rendered(journal_errors=None, journal_error_reason="x")
        assert "had no error-priority entries" not in text

    def test_a_genuinely_quiet_journal_still_reads_as_quiet(self) -> None:
        """The guard must not turn every clean host into an unknown one."""
        text = _rendered(journal_errors="")
        assert UNAVAILABLE not in text
        assert "had no error-priority entries" in text

    def test_real_entries_are_shown(self) -> None:
        text = _rendered(journal_errors="Aug 14 14:02:35 jellyfin labdog-example[1]: simulated")
        assert "simulated" in text
        assert UNAVAILABLE not in text

    def test_a_missing_reason_still_marks_it_unavailable(self) -> None:
        """A reason nobody set must not silently become a reading."""
        assert UNAVAILABLE in _rendered(journal_errors=None)


class TestTheJournalPrivilegeProbe:
    """Which command the collector chooses, and when it gives up.

    Sudo rather than group membership is deliberate: adding the SSH user
    to ``systemd-journal`` also works, but it is not something an operator
    would know to do, and the failure when they don't is silent.
    """

    @staticmethod
    def _conn(*, uid: str, sudo_ok: bool, journal: str = "", rc: int = 0, stderr: str = ""):
        class _Result:
            def __init__(self, stdout: str, exit_status: int = 0, err: str = "") -> None:
                self.stdout = stdout
                self.exit_status = exit_status
                self.stderr = err

        class _Conn:
            def __init__(self) -> None:
                self.commands: list[str] = []

            async def run(self, command: str, check: bool = False):  # noqa: ARG002, FBT001, FBT002
                self.commands.append(command)
                if command.startswith("id -u"):
                    return _Result(f"{uid}\n{'SUDO' if sudo_ok else 'NOSUDO'}\n")
                return _Result(journal, rc, stderr)

        return _Conn()

    async def _read(self, conn):  # noqa: ANN001
        from app.workflows.steps.verify import _read_journal_errors

        return await _read_journal_errors(conn, "10.0.0.9")

    async def test_root_reads_it_directly(self) -> None:
        conn = self._conn(uid="0", sudo_ok=False, journal="boom")
        entries, reason = await self._read(conn)
        assert entries == "boom"
        assert reason == ""
        assert not any(c.startswith("sudo") for c in conn.commands[1:])

    async def test_an_unprivileged_user_with_sudo_uses_it(self) -> None:
        conn = self._conn(uid="1000", sudo_ok=True, journal="boom")
        entries, _ = await self._read(conn)
        assert entries == "boom"
        assert conn.commands[1].startswith("sudo -n journalctl")

    async def test_an_unprivileged_user_without_sudo_reports_unknown(self) -> None:
        """Not an empty string. This is the production failure: the read
        would have succeeded, seen only its own journal, and exited 0."""
        conn = self._conn(uid="1000", sudo_ok=False, journal="")
        entries, reason = await self._read(conn)
        assert entries is None
        assert "sudo" in reason
        # It must not even attempt the read it cannot trust.
        assert len(conn.commands) == 1

    async def test_a_failed_read_is_unknown_not_quiet(self) -> None:
        """journalctl exiting non-zero with empty stdout is the same lie
        in a different coat."""
        conn = self._conn(uid="0", sudo_ok=False, journal="", rc=1, stderr="No journal files")
        entries, reason = await self._read(conn)
        assert entries is None
        assert "No journal files" in reason

    async def test_a_quiet_privileged_host_is_still_quiet(self) -> None:
        conn = self._conn(uid="0", sudo_ok=False, journal="")
        entries, reason = await self._read(conn)
        assert entries == ""
        assert reason == ""
