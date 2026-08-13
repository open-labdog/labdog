"""Reading a verify verdict, and what an unclear one costs.

A FAIL here can restore a pre-change snapshot, discarding the change and
everything since. These tests exist because the previous parser searched
the whole reply for the substrings PASS and FAIL, so

    Everything looks fine; nothing failed.

parsed as a failure — ``find("PASS")`` returns -1 while ``find("FAIL")``
matches inside *failed*. That is not a contrived reply. It is how a model
answers "is this host healthy?" when the answer is yes.

No database needed: this is the decision logic on its own.
"""

from __future__ import annotations

import pytest

from app.ai import verdict as verdicts
from app.ai.verdict import FAILED, INCONCLUSIVE, PASSED, parse_verdict, resolve


class TestReadingTheVerdict:
    @pytest.mark.parametrize(
        "reply",
        [
            "PASS",
            "PASS — services are up and the journal is quiet.",
            "pass: everything checks out",
            "  PASS with a leading indent",
            "\n\nPASS after blank lines",
        ],
    )
    def test_a_pass_is_read_as_a_pass(self, reply: str) -> None:
        assert parse_verdict(reply) == PASSED

    @pytest.mark.parametrize(
        "reply",
        ["FAIL", "FAIL — nginx did not come back up.", "fail: disk is full"],
    )
    def test_a_fail_is_read_as_a_fail(self, reply: str) -> None:
        assert parse_verdict(reply) == FAILED

    def test_inconclusive_is_a_verdict_the_model_can_state(self) -> None:
        """The point of the third value: the model can say it does not
        know, instead of being forced to pick a side and having the
        parser guess at which."""
        assert parse_verdict("INCONCLUSIVE — the disk reading was missing.") == INCONCLUSIVE

    def test_an_english_pass_is_not_read_as_a_failure(self) -> None:
        """The regression this whole module exists for."""
        assert parse_verdict("Everything looks fine; nothing failed.") == INCONCLUSIVE

    @pytest.mark.parametrize(
        "reply",
        [
            "FAILED to find any problems — the host is healthy.",
            "PASSING all checks",
            "Passed? Yes.",
        ],
    )
    def test_a_word_that_merely_starts_with_a_verdict_is_not_one(self, reply: str) -> None:
        """``\\b`` is what separates FAIL from FAILED. Without it, the
        reply that most clearly means "healthy" reads as a rollback."""
        assert parse_verdict(reply) == INCONCLUSIVE

    def test_a_verdict_below_the_first_line_is_not_read(self) -> None:
        """The contract is the first word. A reply that reasons its way
        to a verdict has not followed it, and picking the first verdict
        word out of prose is how a discussion of what would constitute a
        failure becomes one."""
        reply = "Let me look at the services first.\nFAIL"
        assert parse_verdict(reply) == INCONCLUSIVE

    @pytest.mark.parametrize("reply", ["", "   ", "\n\n"])
    def test_an_empty_reply_is_inconclusive(self, reply: str) -> None:
        assert parse_verdict(reply) == INCONCLUSIVE


class TestThePolicyOnlyDecidesTheMiddle:
    @pytest.mark.parametrize("fail_closed", [True, False])
    def test_a_stated_pass_passes_either_way(self, fail_closed: bool) -> None:
        assert resolve(PASSED, fail_closed=fail_closed) is True

    @pytest.mark.parametrize("fail_closed", [True, False])
    def test_a_stated_fail_fails_either_way(self, fail_closed: bool) -> None:
        """fail_closed must not be able to override a verdict the model
        actually gave — in either direction. It is a rule about silence."""
        assert resolve(FAILED, fail_closed=fail_closed) is False

    def test_inconclusive_passes_by_default(self) -> None:
        """The conservative direction, and the behaviour every existing
        manifest was written against: a verdict nobody can read is not
        evidence that anything is wrong, and rolling a host back on it
        would be inventing a failure."""
        assert resolve(INCONCLUSIVE, fail_closed=False) is True

    def test_inconclusive_fails_when_the_manifest_asks_for_it(self) -> None:
        assert resolve(INCONCLUSIVE, fail_closed=True) is False


class TestTheOutcomeExplainsItself:
    def test_a_readable_verdict_keeps_the_model_s_own_words(self) -> None:
        outcome = verdicts.outcome_from_reply(
            "PASS — nginx and postgres are both active.", fail_closed=False
        )
        assert outcome.verdict == PASSED
        assert outcome.passed is True
        assert outcome.conclusive is True
        assert "nginx and postgres" in outcome.detail

    def test_an_unreadable_reply_is_quoted_not_discarded(self) -> None:
        """This resolves to a pass under the default policy, and a pass
        whose reasoning nobody can read should say so where the operator
        is looking — not only in a log line."""
        outcome = verdicts.outcome_from_reply("It seems okay to me?", fail_closed=False)
        assert outcome.verdict == INCONCLUSIVE
        assert outcome.passed is True
        assert outcome.conclusive is False
        assert "It seems okay to me?" in outcome.detail
        assert "readable" in outcome.detail

    def test_an_empty_reply_says_so(self) -> None:
        outcome = verdicts.outcome_from_reply("", fail_closed=False)
        assert "(no output)" in outcome.detail

    def test_a_run_that_never_happened_is_inconclusive_not_a_pass(self) -> None:
        """No provider, AI off, budget spent, backend error. None of
        these say anything about the host, so none of them may quietly
        return a pass on a fail-closed action."""
        outcome = verdicts.inconclusive("AI is disabled.", fail_closed=True)
        assert outcome.verdict == INCONCLUSIVE
        assert outcome.passed is False
        assert outcome.detail == "AI is disabled."

    def test_the_same_non_run_passes_when_the_policy_is_open(self) -> None:
        assert verdicts.inconclusive("AI is disabled.", fail_closed=False).passed is True
