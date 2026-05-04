"""Tests for the env-var override coercion in app.config.

Exercises list/dict parsing, nested-model traversal, and unknown-path
robustness without touching the real ``settings`` singleton.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from app.config import Settings


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
