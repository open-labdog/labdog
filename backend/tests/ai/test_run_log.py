"""What an investigation of a run is told about its log.

The sample is the run page's own e2e fixture: a real linux-upgrade whose
verify playbook failed and was rolled back. The backend's classifier is a
port of the frontend's, so they are checked against the same text.
"""

from __future__ import annotations

from pathlib import Path

from app.ai import run_log

SAMPLE = (
    Path(__file__).resolve().parents[3] / "frontend" / "e2e" / "ansible-log-sample.txt"
).read_text()


def _kinds(text: str) -> list[str]:
    return [line.kind for line in run_log.parse(text)]


class TestParse:
    def test_matches_the_run_page(self):
        """frontend/e2e/run-log.spec.ts: the fatal result and its JSON body
        are six lines painted as fatal."""
        lines = run_log.parse(SAMPLE)
        assert sum(1 for line in lines if line.kind == "fatal") == 6
        assert [line.text for line in lines if line.kind == "labdog-failed"] == [
            "[verify] pack playbook exit=2 status=failed passed=False"
        ]
        assert "\n".join(line.text for line in lines) == SAMPLE

    def test_lines_are_numbered_from_one(self):
        lines = run_log.parse("a\nb\nc")
        assert [(line.number, line.text) for line in lines] == [(1, "a"), (2, "b"), (3, "c")]

    def test_an_error_block_runs_to_the_next_result(self):
        text = "TASK [x] ***\n[ERROR]: boom\nOrigin: here\n\n  12 code\nfatal: [h]: FAILED! => {}"
        assert _kinds(text) == ["task", "error", "error", "error", "error", "fatal"]

    def test_recap_rows_only_under_a_recap(self):
        row = "web1 : ok=1 changed=0 unreachable=0 failed=0"
        assert _kinds(f"PLAY RECAP ***\n{row}\n\n{row}\n[cleanup] done\n{row}") == [
            "recap-head",
            "recap-row",
            "plain",
            "recap-row",
            "labdog",
            "plain",
        ]


class TestReadable:
    def test_strips_terminal_codes(self):
        assert run_log.readable("\x1b[0;31mfatal\x1b[0m: x\r\n\x1b]0;title\x07ok") == (
            "fatal: x\r\nok"
        )

    def test_redacts_a_key_across_lines(self):
        key = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\nBBBB\n-----END OPENSSH PRIVATE KEY-----"
        text = run_log.readable(f"before\n{key}\nafter")
        assert "AAAA" not in text and "BEGIN OPENSSH" not in text
        assert text.startswith("before\n") and text.endswith("\nafter")

    def test_empty(self):
        assert run_log.readable(None) == ""


class TestOutline:
    def test_labdogs_steps_and_both_recaps(self):
        steps = [line.text.split(" ***")[0] for line in run_log.outline(run_log.parse(SAMPLE))]
        assert steps[:4] == [
            "[preflight] host reachable",
            "[snapshot] created labdog-242-1790857608 on pve1/200",
            "[playbook] exit=0 status=successful",
            "=== Ansible output ===",
        ]
        assert steps.count("PLAY RECAP") == 2
        assert "[rollback] success=True" in steps
        assert steps[-1] == "[cleanup] snapshot labdog-242-1790857608 deleted after rollback"
        assert not any(s.startswith(("ok:", "TASK", "[WARNING]")) for s in steps)


class TestFailures:
    def test_the_sample(self):
        found = run_log.failures(run_log.parse(SAMPLE))

        verify, task = found
        assert verify.task is None
        assert [line.text for line in verify.lines] == [
            "[verify] pack playbook exit=2 status=failed passed=False"
        ]
        # The [ERROR] and the fatal result are one task's failure.
        assert task.task == "TASK [Assert no unexpected systemd unit is in 'failed' state]"
        texts = [line.text for line in task.lines]
        assert texts[0].startswith("[ERROR]: Task failed")
        assert texts[-1] == "}"
        assert any(t.startswith("fatal: [web1]: FAILED!") for t in texts)
        assert task.lines[0].number == 138

    def test_keeps_the_lines_before_within_the_task(self):
        text = "\n".join(
            [
                "TASK [first] ***",
                "ok: [h]",
                "",
                "TASK [second] ***",
                "some output",
                "more output",
                "fatal: [h]: FAILED! => {",
                '    "msg": "no"',
                "}",
            ]
        )
        (failure,) = run_log.failures(run_log.parse(text))
        assert failure.task == "TASK [second]"
        assert [line.text for line in failure.lines] == [
            "some output",
            "more output",
            "fatal: [h]: FAILED! => {",
            '    "msg": "no"',
            "}",
        ]

    def test_an_ignored_failure_says_so(self):
        text = "TASK [t] ***\nfatal: [h]: FAILED! => {\n}\n...ignoring\n\nTASK [u] ***\nok: [h]"
        (failure,) = run_log.failures(run_log.parse(text))
        assert failure.ignored

    def test_a_clean_log_has_none(self):
        assert run_log.failures(run_log.parse("TASK [t] ***\nok: [h]\nchanged: [h]")) == []

    def test_a_long_result_is_cut(self):
        body = "\n".join(f'    "line{i}": 1,' for i in range(200))
        (failure,) = run_log.failures(
            run_log.parse(f"TASK [t] ***\nfatal: [h]: x => {{\n{body}\n}}")
        )
        assert len(failure.lines) == 40
