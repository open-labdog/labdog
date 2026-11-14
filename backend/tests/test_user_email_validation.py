"""Accounts only get addresses that ``GET /api/users/me`` can serialise.

``UserRead`` types ``email`` as ``EmailStr``. ``/api/admin/users`` and
``users_cli create`` took any string, so an account made with, say,
``someone@host.invalid`` could sign in and then got a 500 from ``/me`` on
every page load, which the frontend reads as being signed out.
"""

from __future__ import annotations

import pytest

from app.auth.users_cli import _valid_email

NEW_PASSWORD = "a-perfectly-good-passphrase"


class TestCli:
    def test_a_reserved_domain_is_refused(self):
        with pytest.raises(SystemExit, match="Not a valid email address"):
            _valid_email("someone@host.invalid")

    def test_garbage_is_refused(self):
        with pytest.raises(SystemExit):
            _valid_email("not-an-address")

    def test_a_good_address_passes(self):
        assert _valid_email("someone@example.com") == "someone@example.com"


@pytest.mark.integration
class TestAdminApi:
    async def test_create_refuses_an_address_me_cannot_serialise(self, superuser_client):
        resp = await superuser_client.post(
            "/api/admin/users",
            json={"email": "someone@host.invalid", "password": NEW_PASSWORD},
        )
        assert resp.status_code == 422, resp.text

    async def test_update_refuses_it_too(self, superuser_client):
        resp = await superuser_client.post(
            "/api/admin/users",
            json={"email": "valid-first@example.com", "password": NEW_PASSWORD},
        )
        assert resp.status_code == 201, resp.text
        resp = await superuser_client.patch(
            f"/api/admin/users/{resp.json()['id']}", json={"email": "someone@host.invalid"}
        )
        assert resp.status_code == 422, resp.text
