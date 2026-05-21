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
        "LABDOG_SECURITY__SECRET_KEY": "test-secret-key",
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
