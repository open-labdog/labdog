"""Tests for the small redaction helper used by pack sync."""

from __future__ import annotations

from app.packs.redact import REDACTED, redact


def test_redact_replaces_each_secret():
    out = redact("the token is abcd1234 and xyz9876", ["abcd1234", "xyz9876"])
    assert out == f"the token is {REDACTED} and {REDACTED}"


def test_redact_ignores_none_text():
    assert redact(None, ["abcd"]) is None


def test_redact_ignores_empty_secret_list():
    assert redact("hello", None) == "hello"
    assert redact("hello", []) == "hello"


def test_redact_skips_short_secrets():
    """Three-char secrets are too likely to hit common substrings."""
    out = redact("the cat sat on the mat", ["cat"])
    assert out == "the cat sat on the mat"


def test_redact_handles_repeats():
    out = redact("secret1 and secret1 again", ["secret1"])
    assert out.count(REDACTED) == 2
    assert "secret1" not in out
