"""SEC-30: the password policy applies wherever a password is set.

The 12-character rule lived as a pydantic validator on ``UserCreate``
alone, so it held on exactly the one path nobody needs to use. Every
other way to set a password went round it:

* ``PATCH /api/users/me`` accepts a ``password`` field and ``UserUpdate``
  had no validator — any user could downgrade their own password to a
  single character.
* ``POST /api/admin/users`` and ``POST /api/admin/users/{id}/reset-password``
  take a bare ``str`` and hash it directly, never touching the manager.

The rule now lives in one function, called from
``UserManager.validate_password`` (which fastapi-users invokes on
create, update and reset) and explicitly from the two admin endpoints
that build ``User`` rows by hand.

The tests below assert on the endpoints rather than the function,
because "the function rejects it" was already true before this change —
what was false is that the endpoints called it.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth.password_policy import (
    MAX_LENGTH,
    MIN_LENGTH,
    PasswordPolicyError,
    check_password,
)

SHORT = "short1!"
GOOD = "CorrectHorseBattery1!"


class TestTheRuleItself:
    def test_a_password_at_the_minimum_is_accepted(self):
        check_password("x" * MIN_LENGTH)

    def test_one_character_short_is_refused(self):
        with pytest.raises(PasswordPolicyError, match=str(MIN_LENGTH)):
            check_password("x" * (MIN_LENGTH - 1))

    def test_an_absurdly_long_password_is_refused(self):
        """Argon2 cost is paid on every login attempt and the input is
        chosen by an unauthenticated caller."""
        with pytest.raises(PasswordPolicyError, match="at most"):
            check_password("x" * (MAX_LENGTH + 1))

    def test_the_password_may_not_be_the_email(self):
        with pytest.raises(PasswordPolicyError, match="email"):
            check_password("someone@example.com", email="someone@example.com")

    def test_the_email_comparison_ignores_case_and_padding(self):
        with pytest.raises(PasswordPolicyError, match="email"):
            check_password("  Someone@Example.COM ", email="someone@example.com")

    def test_a_password_merely_containing_the_email_is_fine(self):
        """Refusing substrings would reject reasonable passphrases; the
        worthless case is equality."""
        check_password("someone@example.com-and-then-some", email="someone@example.com")

    def test_no_email_supplied_still_checks_length(self):
        with pytest.raises(PasswordPolicyError):
            check_password(SHORT)


class TestSelfServiceUpdateCannotBypassIt:
    """The hole that mattered: any user, on their own account, with no
    administrator involved."""

    async def test_patching_my_own_password_to_something_short_is_refused(
        self, regular_user_client
    ):
        resp = await regular_user_client.patch("/api/users/me", json={"password": SHORT})
        assert resp.status_code in (400, 422), resp.text
        assert "12" in resp.text

    async def test_a_conforming_password_is_still_accepted(self, regular_user_client):
        resp = await regular_user_client.patch("/api/users/me", json={"password": GOOD})
        assert resp.status_code == 200, resp.text

    async def test_an_update_that_does_not_touch_the_password_is_unaffected(
        self, regular_user_client
    ):
        """The validator must not fire on an absent field and turn every
        profile edit into a 400."""
        resp = await regular_user_client.patch(
            "/api/users/me", json={"email": f"moved_{uuid.uuid4().hex[:8]}@test.com"}
        )
        assert resp.status_code == 200, resp.text

    async def test_a_superuser_gets_no_exemption(self, superuser_client):
        """The privilege model is flat; the policy is not a permission."""
        resp = await superuser_client.patch("/api/users/me", json={"password": SHORT})
        assert resp.status_code in (400, 422), resp.text


class TestRegistrationStillEnforcesIt:
    def test_the_create_schema_still_rejects_a_short_password(self):
        from pydantic import ValidationError

        from app.auth.schemas import UserCreate

        with pytest.raises(ValidationError):
            UserCreate(email="a@b.com", password=SHORT)

    def test_the_create_schema_accepts_a_conforming_one(self):
        from app.auth.schemas import UserCreate

        assert UserCreate(email="a@b.com", password=GOOD).password == GOOD


class TestTheAdminEndpointsEnforceItToo:
    """These build ``User`` rows directly and never reach the manager, so
    they are a separate call site and a separate way to get it wrong."""

    async def test_creating_a_user_with_a_short_password_is_refused(self, superuser_client):
        resp = await superuser_client.post(
            "/api/admin/users",
            json={"email": f"new_{uuid.uuid4().hex[:8]}@test.com", "password": SHORT},
        )
        assert resp.status_code == 400, resp.text
        assert "12" in resp.json()["detail"]

    async def test_creating_a_user_with_a_conforming_password_works(self, superuser_client):
        resp = await superuser_client.post(
            "/api/admin/users",
            json={"email": f"new_{uuid.uuid4().hex[:8]}@test.com", "password": GOOD},
        )
        assert resp.status_code == 201, resp.text

    async def test_a_user_may_not_be_created_with_their_email_as_the_password(
        self, superuser_client
    ):
        email = f"same_{uuid.uuid4().hex[:8]}@test.com"
        resp = await superuser_client.post(
            "/api/admin/users", json={"email": email, "password": email}
        )
        assert resp.status_code == 400, resp.text

    async def test_resetting_to_a_short_password_is_refused(self, superuser_client):
        create = await superuser_client.post(
            "/api/admin/users",
            json={"email": f"reset_{uuid.uuid4().hex[:8]}@test.com", "password": GOOD},
        )
        user_id = create.json()["id"]
        resp = await superuser_client.post(
            f"/api/admin/users/{user_id}/reset-password", json={"password": SHORT}
        )
        assert resp.status_code == 400, resp.text

    async def test_resetting_to_a_conforming_password_works(self, superuser_client):
        create = await superuser_client.post(
            "/api/admin/users",
            json={"email": f"reset2_{uuid.uuid4().hex[:8]}@test.com", "password": GOOD},
        )
        user_id = create.json()["id"]
        resp = await superuser_client.post(
            f"/api/admin/users/{user_id}/reset-password",
            json={"password": "AnotherGoodOne1!"},
        )
        assert resp.status_code == 200, resp.text

    async def test_the_reset_is_checked_against_the_target_users_email(self, superuser_client):
        """Not the administrator's — the password ends up on the target
        account."""
        email = f"target_{uuid.uuid4().hex[:8]}@test.com"
        create = await superuser_client.post(
            "/api/admin/users", json={"email": email, "password": GOOD}
        )
        user_id = create.json()["id"]
        resp = await superuser_client.post(
            f"/api/admin/users/{user_id}/reset-password", json={"password": email}
        )
        assert resp.status_code == 400, resp.text
