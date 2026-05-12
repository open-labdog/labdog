"""Tests for GET /api/version — public build-metadata endpoint."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestVersionEndpoint:
    """Test suite for GET /api/version."""

    # ------------------------------------------------------------------
    # Happy path: env vars supply all optional fields
    # ------------------------------------------------------------------

    async def test_env_vars_populate_response(self, client, monkeypatch):
        """Env vars set → response carries those values and the derived short SHA."""
        sha = "abcdef1234567890abcdef1234567890abcdef12"
        date = "2026-05-12T14:30:00Z"

        import app.api.version as ver

        monkeypatch.setattr(ver, "_COMMIT_SHA", sha)
        monkeypatch.setattr(ver, "_BUILD_DATE", date)

        resp = await client.get("/api/version")
        assert resp.status_code == 200

        data = resp.json()
        assert data["commit_sha"] == sha
        assert data["commit_sha_short"] == sha[:7]
        assert data["build_date"] == date
        assert data["version"]  # non-empty string
        assert data["license"] == "AGPL-3.0-or-later"
        assert data["repo_url"] == "https://github.com/open-labdog/labdog"

    # ------------------------------------------------------------------
    # Dev environment: no env vars, no _build_info.py
    # ------------------------------------------------------------------

    async def test_no_env_vars_no_build_info_returns_nulls(self, client, monkeypatch):
        """With no env vars and no _build_info module, optional fields are null."""
        import app.api.version as ver

        monkeypatch.setattr(ver, "_COMMIT_SHA", None)
        monkeypatch.setattr(ver, "_BUILD_DATE", None)

        resp = await client.get("/api/version")
        assert resp.status_code == 200

        data = resp.json()
        assert data["commit_sha"] is None
        assert data["commit_sha_short"] is None
        assert data["build_date"] is None
        # Static fields are always present
        assert data["version"]
        assert data["license"] == "AGPL-3.0-or-later"
        assert data["repo_url"] == "https://github.com/open-labdog/labdog"

    # ------------------------------------------------------------------
    # _build_info.py present, no env vars → values come from the file
    # ------------------------------------------------------------------

    async def test_build_info_module_used_when_no_env_vars(self, monkeypatch):
        """_resolve_commit_sha/_resolve_build_date read from _build_info when env absent."""
        import sys
        import types

        sha = "deadbeef12345678deadbeef12345678deadbeef"
        date = "2026-01-01T00:00:00Z"

        # Inject a fake app._build_info module
        fake_module = types.ModuleType("app._build_info")
        fake_module.COMMIT_SHA = sha
        fake_module.BUILD_DATE = date
        monkeypatch.setitem(sys.modules, "app._build_info", fake_module)

        # Remove env vars so resolution falls through to the module
        monkeypatch.delenv("LABDOG_COMMIT_SHA", raising=False)
        monkeypatch.delenv("LABDOG_BUILD_DATE", raising=False)

        import app.api.version as ver

        resolved_sha = ver._resolve_commit_sha()
        resolved_date = ver._resolve_build_date()

        assert resolved_sha == sha
        assert resolved_date == date

    # ------------------------------------------------------------------
    # Env vars take precedence over _build_info.py
    # ------------------------------------------------------------------

    async def test_env_vars_take_precedence_over_build_info(self, monkeypatch):
        """Env vars win over _build_info module values."""
        import sys
        import types

        file_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        env_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        file_date = "2025-01-01T00:00:00Z"
        env_date = "2026-06-01T12:00:00Z"

        fake_module = types.ModuleType("app._build_info")
        fake_module.COMMIT_SHA = file_sha
        fake_module.BUILD_DATE = file_date
        monkeypatch.setitem(sys.modules, "app._build_info", fake_module)

        monkeypatch.setenv("LABDOG_COMMIT_SHA", env_sha)
        monkeypatch.setenv("LABDOG_BUILD_DATE", env_date)

        import app.api.version as ver

        assert ver._resolve_commit_sha() == env_sha
        assert ver._resolve_build_date() == env_date

    # ------------------------------------------------------------------
    # Invalid LABDOG_BUILD_DATE → null, no 500
    # ------------------------------------------------------------------

    async def test_invalid_build_date_returns_null(self, client, monkeypatch):
        """A non-ISO LABDOG_BUILD_DATE yields null build_date without crashing."""
        monkeypatch.setenv("LABDOG_BUILD_DATE", "not-a-date")
        monkeypatch.delenv("LABDOG_COMMIT_SHA", raising=False)

        import app.api.version as ver

        # Verify the resolver returns None for garbage input
        resolved = ver._resolve_build_date()
        assert resolved is None

        # And the endpoint stays healthy with the module-level value patched to None
        monkeypatch.setattr(ver, "_BUILD_DATE", None)
        resp = await client.get("/api/version")
        assert resp.status_code == 200
        assert resp.json()["build_date"] is None

    # ------------------------------------------------------------------
    # Endpoint requires no authentication
    # ------------------------------------------------------------------

    async def test_endpoint_is_unauthenticated(self, client):
        """GET /api/version must succeed without any auth token."""
        # `client` fixture carries no credentials by default
        resp = await client.get("/api/version")
        # 200 proves no auth check is in the path
        assert resp.status_code == 200

    # ------------------------------------------------------------------
    # Response schema completeness
    # ------------------------------------------------------------------

    async def test_response_schema_has_all_required_keys(self, client, monkeypatch):
        """Response always contains all six keys defined by the frontend contract."""
        import app.api.version as ver

        monkeypatch.setattr(ver, "_COMMIT_SHA", None)
        monkeypatch.setattr(ver, "_BUILD_DATE", None)

        resp = await client.get("/api/version")
        data = resp.json()

        expected_keys = {
            "version",
            "commit_sha",
            "commit_sha_short",
            "build_date",
            "license",
            "repo_url",
        }
        assert expected_keys == set(data.keys())
