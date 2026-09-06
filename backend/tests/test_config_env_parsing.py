"""Tests for the env-var override coercion in app.config.

Exercises list/dict parsing, nested-model traversal, and unknown-path
robustness without touching the real ``settings`` singleton.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.config import Settings, _validate_required


def _env(**overrides: str) -> dict[str, str]:
    """Start from a minimal env that satisfies the secret-key validator."""
    base = {
        # 32+ chars: _validate_required rejects anything shorter (SEC-31).
        "LABDOG_SECURITY__SECRET_KEY": "test-secret-key-not-for-production",
        "LABDOG_SECURITY__ENCRYPTION_KEY": "vrPDeLMuFGehy2sYV//fyTd7EmnvOKbE2n4h7XM/8zg=",
    }
    base.update(overrides)
    return base


def test_scalar_fields_pass_through():
    with patch.dict(os.environ, _env(LABDOG_SERVER__PORT="9001"), clear=True):
        s = Settings()
    assert s.server.port == 9001


def test_existing_allowed_origins_list_accepts_csv():
    """Regression check — this list field broke under the original
    raw-string behaviour when set via env var."""
    with patch.dict(
        os.environ,
        _env(LABDOG_SECURITY__ALLOWED_ORIGINS="http://a,http://b"),
        clear=True,
    ):
        s = Settings()
    assert s.security.allowed_origins == ["http://a", "http://b"]


def test_allowed_origins_accepts_json_array():
    with patch.dict(
        os.environ,
        _env(LABDOG_SECURITY__ALLOWED_ORIGINS='["http://a","http://b"]'),
        clear=True,
    ):
        s = Settings()
    assert s.security.allowed_origins == ["http://a", "http://b"]


def test_allowed_origins_empty_env_yields_empty_list():
    with patch.dict(os.environ, _env(LABDOG_SECURITY__ALLOWED_ORIGINS=""), clear=True):
        s = Settings()
    assert s.security.allowed_origins == []


def test_allowed_origins_csv_strips_whitespace_and_drops_empties():
    with patch.dict(
        os.environ,
        _env(LABDOG_SECURITY__ALLOWED_ORIGINS=" http://a , , http://b "),
        clear=True,
    ):
        s = Settings()
    assert s.security.allowed_origins == ["http://a", "http://b"]


def test_ansible_packs_root_dir_settable_from_env():
    with patch.dict(
        os.environ,
        _env(LABDOG_ANSIBLE__PACKS_ROOT_DIR="/custom/packs"),
        clear=True,
    ):
        s = Settings()
    assert s.ansible.packs_root_dir == "/custom/packs"


def test_unknown_env_path_passes_through_without_error():
    """Typos in env var names must not crash config load."""
    with patch.dict(
        os.environ,
        _env(LABDOG_NONSENSE__NESTED__KEY="value"),
        clear=True,
    ):
        s = Settings()
    assert not hasattr(s, "nonsense")


# ---------------------------------------------------------------------------
# _validate_required — localhost-origin / cookie_secure checks (SEC-18)
# ---------------------------------------------------------------------------


def _make_settings(**security_overrides) -> Settings:
    """Build a Settings instance with secure key defaults and given security overrides."""
    with patch.dict(
        os.environ,
        _env(),
        clear=True,
    ):
        s = Settings()
    # Apply security overrides directly on the model after construction so we
    # bypass env-var parsing (keeping tests simple and readable).
    for key, value in security_overrides.items():
        setattr(s.security, key, value)
    return s


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://localhost",
        "http://127.0.0.1:8080",
        "http://127.0.0.2",
        "http://127.255.255.255:9000",
        "http://0.0.0.0:3000",
        "http://[::1]:3000",
        "http://[::]:3000",
    ],
)
def test_cookie_secure_true_rejects_localhost_origin(origin):
    """cookie_secure=True + any localhost/loopback origin must raise SystemExit."""
    s = _make_settings(cookie_secure=True, allowed_origins=[origin])
    with pytest.raises(SystemExit) as exc_info:
        _validate_required(s)
    message = str(exc_info.value)
    assert "allowed_origins" in message
    assert origin in message


def test_cookie_secure_true_accepts_production_origin():
    """cookie_secure=True with only a real HTTPS origin must not raise."""
    s = _make_settings(
        cookie_secure=True,
        allowed_origins=["https://example.com", "https://app.example.com"],
    )
    _validate_required(s)  # must not raise


def test_cookie_secure_false_allows_localhost():
    """cookie_secure=False (dev posture) must not reject localhost origins."""
    s = _make_settings(
        cookie_secure=False,
        allowed_origins=["http://localhost:3000"],
    )
    _validate_required(s)  # must not raise


def test_cookie_secure_true_mixed_origins_reports_only_bad_ones():
    """Only the offending localhost entry is reported; the good one is not flagged."""
    s = _make_settings(
        cookie_secure=True,
        allowed_origins=["https://example.com", "http://localhost:3000"],
    )
    with pytest.raises(SystemExit) as exc_info:
        _validate_required(s)
    message = str(exc_info.value)
    assert "localhost" in message
    # The production origin should not appear in the error as a bad entry.
    # (It may appear in context, but the specific error targets localhost.)
    assert "allowed_origins" in message


# ---------------------------------------------------------------------------
# _validate_required — secret strength, key format, CORS wildcard (SEC-31)
# ---------------------------------------------------------------------------
#
# Startup validation used to reject exactly two literal placeholder
# strings. Everything else passed: a six-character HS256 signing key, a
# malformed encryption key (which then failed hours later at the first
# host sync), and ``allowed_origins=["*"]`` — which, because LabDog
# always sends credentials, makes Starlette reflect whatever Origin the
# request carried, i.e. credentialed cross-origin access from any site a
# logged-in user visits.


class TestTheSigningKeyMustBeWorthSigningWith:
    def test_a_short_key_is_refused(self):
        """The auth cookie is signed with it. A key short enough to
        brute-force offline forges any account, superuser included."""
        s = _make_settings(secret_key="hunter2")
        with pytest.raises(SystemExit, match="secret_key"):
            _validate_required(s)

    def test_the_boundary_is_accepted(self):
        s = _make_settings(secret_key="x" * 32)
        _validate_required(s)

    def test_one_short_of_the_boundary_is_refused(self):
        s = _make_settings(secret_key="x" * 31)
        with pytest.raises(SystemExit):
            _validate_required(s)

    def test_what_the_docs_tell_you_to_generate_is_accepted(self):
        """``openssl rand -base64 32`` produces 44 characters."""
        s = _make_settings(secret_key="A" * 43 + "=")
        _validate_required(s)

    def test_the_shipped_packaging_placeholder_is_refused(self):
        """packaging/etc/labdog.toml ships ``CHANGE_ME`` and it was not on
        the placeholder list, so an install that skipped the "generate
        your secrets" step booted with a nine-character signing key that
        is public in this repository."""
        s = _make_settings(secret_key="CHANGE_ME")
        with pytest.raises(SystemExit) as exc:
            _validate_required(s)
        assert "secret_key" in str(exc.value)

    def test_a_placeholder_is_reported_once_not_twice(self):
        """A placeholder is also too short. Two errors about one value
        help nobody."""
        s = _make_settings(secret_key="CHANGE_ME")
        with pytest.raises(SystemExit) as exc:
            _validate_required(s)
        assert str(exc.value).count("security.secret_key") == 1


class TestTheEncryptionKeyIsCheckedAtBoot:
    def test_a_malformed_key_fails_at_startup(self):
        """It used to fail at the first host sync instead — hours later,
        as a task traceback nobody was watching."""
        s = _make_settings(encryption_key="not-base64-at-all!!")
        with pytest.raises(SystemExit, match="encryption_key"):
            _validate_required(s)

    def test_a_key_of_the_wrong_length_fails_at_startup(self):
        import base64

        s = _make_settings(encryption_key=base64.b64encode(b"x" * 16).decode())
        with pytest.raises(SystemExit, match="encryption_key"):
            _validate_required(s)

    def test_a_valid_key_passes(self):
        s = _make_settings()
        _validate_required(s)

    def test_the_url_safe_alphabet_is_still_accepted(self):
        """Operators paste keys from whichever generator they used."""
        import base64

        s = _make_settings(encryption_key=base64.urlsafe_b64encode(b"y" * 32).decode())
        _validate_required(s)

    def test_the_placeholder_reports_as_unset_not_as_malformed(self):
        s = _make_settings(encryption_key="change-me-32-bytes-base64-encoded")
        with pytest.raises(SystemExit) as exc:
            _validate_required(s)
        assert "is not set" in str(exc.value)


class TestTheCorsWildcard:
    def test_a_wildcard_origin_is_refused(self):
        s = _make_settings(allowed_origins=["*"])
        with pytest.raises(SystemExit) as exc:
            _validate_required(s)
        message = str(exc.value)
        assert "allowed_origins" in message
        assert "credentials" in message

    def test_a_wildcard_alongside_real_origins_is_still_refused(self):
        s = _make_settings(allowed_origins=["https://example.com", "*"])
        with pytest.raises(SystemExit):
            _validate_required(s)

    def test_explicit_origins_are_fine(self):
        s = _make_settings(allowed_origins=["https://example.com"])
        _validate_required(s)


class TestTheInsecureCookieWarning:
    """A warning, not an error. Plenty of homelabs run this over plain
    HTTP on a trusted LAN and refusing to start would be wrong for them."""

    def test_a_routable_origin_without_cookie_secure_warns(self, caplog):
        s = _make_settings(cookie_secure=False, allowed_origins=["http://labdog.lan:8000"])
        with caplog.at_level("WARNING"):
            _validate_required(s)
        assert "cookie_secure" in caplog.text
        assert "labdog.lan" in caplog.text

    def test_localhost_without_cookie_secure_does_not_warn(self, caplog):
        s = _make_settings(cookie_secure=False, allowed_origins=["http://localhost:3000"])
        with caplog.at_level("WARNING"):
            _validate_required(s)
        assert "cookie_secure" not in caplog.text

    def test_the_warning_does_not_stop_startup(self):
        s = _make_settings(cookie_secure=False, allowed_origins=["http://labdog.lan:8000"])
        _validate_required(s)  # must not raise
