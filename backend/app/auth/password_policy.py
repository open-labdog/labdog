"""SEC-30: one password policy, enforced everywhere a password is set.

The 12-character rule lived as a pydantic validator on ``UserCreate``
alone. Every other way to set a password skipped it:

* ``PATCH /api/users/me`` accepts a ``password`` field and ``UserUpdate``
  had no validator, so any user could downgrade their own password to a
  single character.
* ``POST /api/admin/users`` and ``POST /api/admin/users/{id}/reset-password``
  take a bare ``str`` and hash it directly, never touching the manager.

A rule that holds only on the one path an attacker does not need is not
a rule. It is enforced here instead, in one function, reachable from
both the fastapi-users manager (which calls ``validate_password`` on
create, update *and* password reset) and the admin endpoints that build
``User`` rows by hand.

Deliberately the same rule as before, not a stricter one. Tightening it
locks out existing accounts at their next password change and is an
operator-visible product decision; this change is about the policy
applying where it already claimed to.
"""

from __future__ import annotations

#: Unchanged from the original ``UserCreate`` validator.
MIN_LENGTH = 12

#: A password long enough to be a denial-of-service against the hasher
#: rather than a secret. Argon2 cost is paid on every login attempt, and
#: an unauthenticated caller chooses this input.
MAX_LENGTH = 128


class PasswordPolicyError(ValueError):
    """Raised when a password does not meet the policy.

    A plain ``ValueError`` subclass so pydantic validators surface it as
    a 422 and the admin endpoints can turn it into a 400 — fastapi-users
    wraps it into its own ``InvalidPasswordException`` at the manager.
    """


def check_password(password: str, *, email: str | None = None) -> None:
    """Raise :class:`PasswordPolicyError` if *password* is unacceptable.

    *email* is supplied where it is known so a password identical to the
    account name is refused. That is the one case where the length rule
    is satisfied and the password is still worthless.
    """
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_LENGTH} characters")
    if email and password.strip().casefold() == email.strip().casefold():
        raise PasswordPolicyError("Password must not be the email address")
