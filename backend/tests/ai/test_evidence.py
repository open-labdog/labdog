"""What the model is shown, and what it must not be able to misread.

The failure these guard against is not a wrong answer — it is a right
answer to the wrong question. A host whose load and disk checks both
failed used to be described to the verifier as load 0.00 and disk 0%:
the healthiest possible host, assembled entirely out of readings nobody
took.

No database needed.
"""

from __future__ import annotations

from app.ai.evidence import (
    MAX_ITEM_CHARS,
    UNAVAILABLE,
    EvidenceItem,
    render,
    summarise,
)


class TestAMissingReadingIsNotAZero:
    def test_it_is_marked_unavailable(self) -> None:
        text = render([EvidenceItem.missing("System load", "SSH command failed")])
        assert UNAVAILABLE in text
        assert "SSH command failed" in text

    def test_a_real_zero_survives(self) -> None:
        """The falsy trap: 0 is a perfectly good load average, and a
        collector that treats it as "nothing to report" turns an idle
        host into an unmeasured one."""
        text = render([EvidenceItem.reading("System load", 0.0)])
        assert "0.0" in text
        assert UNAVAILABLE not in text

    def test_a_real_zero_percent_survives(self) -> None:
        text = render([EvidenceItem.reading("Disk usage", 0)])
        assert "0" in text
        assert UNAVAILABLE not in text

    def test_optional_routes_none_to_unavailable(self) -> None:
        item = EvidenceItem.optional("Disk usage", None, reason="df did not run")
        assert item.available is False
        assert "df did not run" in render([item])

    def test_optional_routes_a_value_to_a_reading(self) -> None:
        assert EvidenceItem.optional("Disk usage", 41).available is True

    def test_an_empty_result_is_not_the_same_as_a_missing_one(self) -> None:
        """A command that ran and printed nothing is a finding. A command
        that never ran is not. Rendering both as blank space would let
        the model read "we never looked" as "nothing was wrong"."""
        ran = render([EvidenceItem.reading("Recent errors", "")])
        never = render([EvidenceItem.missing("Recent errors", "journal unreadable")])
        assert "the check ran and returned nothing" in ran
        assert UNAVAILABLE not in ran
        assert UNAVAILABLE in never


class TestProvenance:
    def test_the_source_is_shown(self) -> None:
        """So the model can weigh a reading it does not trust, and an
        operator reading the verdict later can see what it came from."""
        text = render([EvidenceItem.reading("System load", "0.42", "ssh: cat /proc/loadavg")])
        assert "cat /proc/loadavg" in text

    def test_a_reading_without_a_source_still_renders(self) -> None:
        assert "0.42" in render([EvidenceItem.reading("System load", "0.42")])


class TestNothingIsCutSilently:
    def test_an_oversized_reading_says_how_much_was_dropped(self) -> None:
        journal = "error line\n" * 5000
        text = render([EvidenceItem.reading("Recent errors", journal)])
        assert "TRUNCATED" in text
        assert len(text) < len(journal)
        assert "cut short" in text

    def test_a_reading_just_under_the_limit_is_untouched(self) -> None:
        body = "x" * (MAX_ITEM_CHARS - 1)
        assert "TRUNCATED" not in render([EvidenceItem.reading("Output", body)])

    def test_a_pack_over_the_ceiling_announces_what_it_dropped(self) -> None:
        items = [EvidenceItem.reading(f"Check {i}", "y" * 400) for i in range(10)]
        text = render(items, max_chars=1000)
        assert "Not included" in text
        assert "do not assume they were clean" in text

    def test_the_first_reading_survives_a_ceiling_smaller_than_itself(self) -> None:
        """Otherwise an aggressive cap produces a pack with no evidence
        in it at all, which reads as a host with nothing wrong."""
        text = render([EvidenceItem.reading("Check", "z" * 5000)], max_chars=10)
        assert "Check" in text


class TestAnEmptyPack:
    def test_it_tells_the_model_to_say_so(self) -> None:
        """Handing a verifier nothing and letting it answer anyway is
        how a session invents its findings."""
        assert "INCONCLUSIVE" in render([])


class TestTheRunLogSummary:
    def test_it_names_what_was_missing(self) -> None:
        line = summarise(
            [
                EvidenceItem.reading("Services", "ok"),
                EvidenceItem.missing("System load", "unreadable"),
            ]
        )
        assert "1 unavailable" in line
        assert "System load" in line

    def test_a_complete_pack_says_so(self) -> None:
        assert summarise([EvidenceItem.reading("Services", "ok")]) == "1 reading(s), all collected"

    def test_no_evidence_says_so(self) -> None:
        assert summarise([]) == "no evidence collected"
