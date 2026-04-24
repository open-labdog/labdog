"""Tests for SSH known_hosts assembly."""

from __future__ import annotations

from app.packs.known_hosts import build_known_hosts


def test_build_known_hosts_empty_input_returns_empty_string():
    assert build_known_hosts(None) == ""
    assert build_known_hosts("") == ""
    assert build_known_hosts("   \n  \n") == ""


def test_build_known_hosts_strips_comments_and_blanks():
    body = build_known_hosts(
        "# this is a comment\n"
        "host1 ssh-rsa AAAA\n"
        "\n"
        "host2 ssh-ed25519 BBBB\n"
    )
    lines = body.strip().splitlines()
    assert lines == ["host1 ssh-rsa AAAA", "host2 ssh-ed25519 BBBB"]


def test_build_known_hosts_deduplicates():
    body = build_known_hosts(
        "host1 ssh-rsa AAAA\n"
        "host1 ssh-rsa AAAA\n"
        "host2 ssh-ed25519 BBBB\n"
    )
    lines = body.strip().splitlines()
    assert lines == ["host1 ssh-rsa AAAA", "host2 ssh-ed25519 BBBB"]


def test_build_known_hosts_trailing_newline_when_populated():
    body = build_known_hosts("host1 ssh-rsa AAAA\n")
    assert body.endswith("\n")
