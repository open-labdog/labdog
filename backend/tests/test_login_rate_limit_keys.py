"""SEC-32: the login rate limit must throttle the attacker, not the install.

The limiter keyed on the client IP alone. Behind a reverse proxy with
``server.trusted_proxies`` unset — the default, and what
``packaging/etc/labdog.toml`` ships — ``_get_client_ip`` returns the
proxy's address for every request, so the whole install shared one
5/minute bucket. Five bad passwords from anywhere locked everybody out,
and an attacker was throttled no harder than a typo.

Keying on the account being attacked fixes both halves at once, and the
CIDR support makes ``trusted_proxies`` usable in a container where the
proxy's address is not known when the config is written — the comment in
the shipped config claimed CIDRs worked, and they did not.
"""

from __future__ import annotations

import json

import pytest

from app.main import (
    _MAX_LOGIN_BODY,
    _buffered_receive,
    _is_trusted_proxy,
    login_identity,
    login_rate_limit_keys,
)


class TestOneAccountCannotLockOutTheOthers:
    """The reported symptom."""

    def test_two_accounts_from_the_same_address_use_different_buckets(self):
        a = login_rate_limit_keys("10.0.0.1", "alice@example.com")
        b = login_rate_limit_keys("10.0.0.1", "bob@example.com")
        assert set(a).isdisjoint(b)

    def test_nothing_is_keyed_on_the_address_alone(self):
        """That bucket is the one this replaces — behind a proxy it is
        the whole install by another name."""
        keys = login_rate_limit_keys("10.0.0.1", "alice@example.com")
        assert "10.0.0.1" not in keys
        assert all("alice@example.com" in k for k in keys)


class TestBothAttackShapesAreCovered:
    def test_one_source_against_one_account_is_throttled(self):
        keys = login_rate_limit_keys("203.0.113.9", "alice@example.com")
        assert any(k.startswith("ip-user:") for k in keys)

    def test_many_sources_against_one_account_share_a_bucket(self):
        """The distributed case, which per-(ip, account) keying alone
        would not catch."""
        a = login_rate_limit_keys("203.0.113.9", "alice@example.com")
        b = login_rate_limit_keys("198.51.100.4", "alice@example.com")
        assert set(a) & set(b) == {"user:alice@example.com"}


class TestARequestWithNoAccountNameIsNotThrottled:
    """Keying those on the IP would put the shared bucket straight back,
    reachable by anyone willing to POST nonsense. They cannot test a
    password, so throttling them buys nothing."""

    @pytest.mark.parametrize("identity", [None, ""])
    def test_no_buckets_are_consumed(self, identity):
        assert login_rate_limit_keys("10.0.0.1", identity) == []


class TestReadingTheAccountNameOutOfTheBody:
    def test_an_oauth2_password_form(self):
        body = b"grant_type=password&username=Alice%40example.com&password=hunter2"
        assert login_identity("application/x-www-form-urlencoded", body) == "alice@example.com"

    def test_a_json_register_body(self):
        body = json.dumps({"email": "Bob@Example.com", "password": "x"}).encode()
        assert login_identity("application/json", body) == "bob@example.com"

    def test_json_with_a_username_field(self):
        body = json.dumps({"username": "carol@example.com"}).encode()
        assert login_identity("application/json", body) == "carol@example.com"

    def test_a_content_type_with_parameters_is_understood(self):
        body = json.dumps({"email": "dave@example.com"}).encode()
        assert login_identity("application/json; charset=utf-8", body) == "dave@example.com"

    @pytest.mark.parametrize(
        "raw",
        ["Alice@Example.com", "  alice@example.com  ", "ALICE@EXAMPLE.COM"],
    )
    def test_case_and_padding_do_not_buy_a_second_budget(self, raw):
        body = json.dumps({"email": raw}).encode()
        assert login_identity("application/json", body) == "alice@example.com"

    def test_a_very_long_name_is_truncated(self):
        """The key ends up in Redis; its size should be bounded by us,
        not by the caller."""
        body = json.dumps({"email": "a" * 5000}).encode()
        assert len(login_identity("application/json", body)) <= 128

    @pytest.mark.parametrize(
        ("ctype", "body"),
        [
            ("application/json", b"{not json"),
            ("application/json", b"[]"),
            ("application/json", b'{"password":"only"}'),
            ("application/x-www-form-urlencoded", b"password=only"),
            ("application/json", b""),
            ("application/json", b"\xff\xfe\x00"),
        ],
    )
    def test_an_unusable_body_yields_no_identity(self, ctype, body):
        assert login_identity(ctype, body) is None


class TestTheBodyIsHandedBackIntact:
    """The middleware reads the body before the route does. ASGI bodies
    are consumed once, so getting this wrong means every login sees an
    empty form and fails."""

    @staticmethod
    def _receive_from(messages):
        queue = list(messages)

        async def receive():
            return queue.pop(0)

        return receive

    async def test_a_single_chunk_body_is_replayed(self):
        body, replay = await _buffered_receive(
            self._receive_from([{"type": "http.request", "body": b"a=1", "more_body": False}])
        )
        assert body == b"a=1"
        assert await replay() == {"type": "http.request", "body": b"a=1", "more_body": False}

    async def test_a_chunked_body_is_reassembled_and_replayed_in_order(self):
        messages = [
            {"type": "http.request", "body": b"user", "more_body": True},
            {"type": "http.request", "body": b"name=x", "more_body": False},
        ]
        body, replay = await _buffered_receive(self._receive_from(messages))
        assert body == b"username=x"
        assert (await replay())["body"] == b"user"
        assert (await replay())["body"] == b"name=x"

    async def test_a_disconnect_is_passed_through_not_swallowed(self):
        body, replay = await _buffered_receive(self._receive_from([{"type": "http.disconnect"}]))
        assert body == b""
        assert (await replay())["type"] == "http.disconnect"

    async def test_an_oversized_body_is_not_buffered_but_still_flows(self):
        """Refusing to hold arbitrary memory must not break the request;
        the identity simply comes out unknown."""
        big = b"x" * (_MAX_LOGIN_BODY + 1)
        body, replay = await _buffered_receive(
            self._receive_from([{"type": "http.request", "body": big, "more_body": False}])
        )
        assert body == b""
        assert (await replay())["body"] == big


class TestTrustedProxyMatching:
    def test_a_literal_address_matches(self):
        assert _is_trusted_proxy("10.0.0.1", ["10.0.0.1"])

    def test_a_cidr_entry_matches(self):
        """The shipped config's comment promised this and the plain `in`
        check never delivered it."""
        assert _is_trusted_proxy("172.18.0.7", ["172.18.0.0/16"])

    def test_an_address_outside_the_range_does_not_match(self):
        assert not _is_trusted_proxy("172.19.0.7", ["172.18.0.0/16"])

    def test_ipv6_ranges_work(self):
        assert _is_trusted_proxy("fd00::5", ["fd00::/8"])

    def test_a_malformed_entry_does_not_break_the_others(self):
        """A typo in one entry must not stop IP resolution for every
        request that follows."""
        assert _is_trusted_proxy("10.0.0.1", ["not-an-ip", "10.0.0.0/8"])

    def test_nothing_matches_an_empty_list(self):
        assert not _is_trusted_proxy("10.0.0.1", [])
