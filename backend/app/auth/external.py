"""Hooks for signing in through something other than a local password.

Community unbork has no such login method. This module is the contract an
extension (the Enterprise SSO package: OIDC, LDAP, later SAML) builds on,
so that every way into unbork ends in the same session, the same
revocation and the same audit trail as password login:

* :func:`register_login_provider_source` puts buttons on the login page,
  through the public ``GET /api/auth/providers``.
* :func:`resolve_external_user` turns "the directory vouches for this
  person" into a ``users`` row: an existing link, a link made by verified
  email, or a newly provisioned account with no password and no role.
* :func:`issue_session` mints the normal ``labdog_auth`` cookie and the CSRF
  cookie, and records the login, exactly as password login does.

Part of the plugin contract versioned by ``app.auth.permissions.PLUGIN_API``.

A typical callback, inside the extension::

    user = await resolve_external_user(db, identity, provision=True, link_by_email=False)
    await store_groups(db, user, groups)          # the extension's own table
    if not await permissions_for(user):           # maps to nothing: refuse
        await db.rollback()
        raise ExternalLoginRefused("no_access", "Your account has no unbork access.")
    await db.commit()
    response = RedirectResponse("/overview", status_code=303)
    await issue_session(user, request, response, user_manager)
    return response

:func:`resolve_external_user` flushes but never commits, so a refusal after
it (no permissions, a failed group lookup) rolls back the account it would
have created instead of leaving a powerless user behind.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import Request, Response
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import log_action
from app.models.user import User
from app.models.user_identity import UserIdentity

logger = logging.getLogger(__name__)


# ── Login providers (buttons on the login page) ─────────────────────────────


@dataclass(frozen=True)
class LoginProvider:
    """One way to sign in, as the login page shows it.

    ``kind="redirect"``: a button that navigates the browser to ``url``
    (an OIDC or SAML start endpoint). ``kind="password"``: the normal
    email/username and password form, posted as
    ``application/x-www-form-urlencoded`` ``username`` + ``password`` to
    ``url`` instead of ``/api/auth/jwt/login`` (LDAP). Either way ``url``
    is a same-origin path; the page refuses anything else.
    """

    key: str
    name: str
    kind: Literal["redirect", "password"]
    url: str

    def __post_init__(self) -> None:
        if self.kind not in ("redirect", "password"):
            raise ValueError(f"login provider {self.key!r}: unknown kind {self.kind!r}")
        if not self.url.startswith("/") or self.url.startswith("//"):
            raise ValueError(f"login provider {self.key!r}: url must be a same-origin path")


#: Returns the providers to show right now. Called on every login-page load,
#: so it should be cheap; it may query the database with its own session.
LoginProviderSource = Callable[[], Awaitable[list[LoginProvider]]]

_provider_sources: list[LoginProviderSource] = []


def register_login_provider_source(source: LoginProviderSource) -> None:
    """Add a source of login providers. Sources are asked in registration order."""
    _provider_sources.append(source)


def clear_login_provider_sources() -> None:
    """Forget every source. For tests."""
    _provider_sources.clear()


async def list_login_providers() -> list[LoginProvider]:
    """Every provider every source offers, deduplicated by key.

    A source that fails is logged and skipped: a broken extension must
    not take the login page, and with it password login, down too.
    """
    seen: set[str] = set()
    providers: list[LoginProvider] = []
    for source in list(_provider_sources):
        try:
            offered = await source()
        except Exception:
            logger.exception("login provider source %r failed; its providers are hidden", source)
            continue
        for provider in offered:
            if provider.key in seen:
                logger.warning(
                    "login provider key %r offered twice; keeping the first", provider.key
                )
                continue
            seen.add(provider.key)
            providers.append(provider)
    return providers


# ── From a directory identity to a user ─────────────────────────────────────


@dataclass(frozen=True)
class ExternalIdentity:
    """What a provider asserts about the person signing in.

    ``provider`` is the extension's stable key for the provider and
    ``subject`` the provider's immutable id for the person (see
    :class:`~app.models.user_identity.UserIdentity`). ``email_verified``
    must be what the provider says, not an assumption: it decides whether
    the identity may attach itself to an existing local account.
    """

    provider: str
    subject: str
    email: str | None
    email_verified: bool = False
    display_name: str | None = None


class ExternalLoginRefused(Exception):
    """The provider vouched for the person, but unbork will not sign them in.

    ``code`` is stable and safe to put in a redirect URL; ``message`` is
    for the person at the login page.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_EMAIL = TypeAdapter(EmailStr)


def _valid_email(email: str | None) -> str:
    if not email or not email.strip():
        raise ExternalLoginRefused(
            "no_email", "The identity provider did not send an email address for your account."
        )
    try:
        return _EMAIL.validate_python(email.strip())
    except ValidationError:
        raise ExternalLoginRefused(
            "bad_email", "The identity provider sent an email address unbork cannot use."
        ) from None


async def resolve_external_user(
    db: AsyncSession,
    identity: ExternalIdentity,
    *,
    provision: bool,
    link_by_email: bool,
) -> User:
    """The active user *identity* signs in as. Flushes; never commits.

    In order:

    1. An existing link for (provider, subject) wins, whatever the email
       says now: emails get renamed, subjects do not.
    2. Otherwise a local account with the same email is linked, but only
       when the provider says the email is verified *and* the caller allows
       linking. Anything looser lets whoever controls a provider account
       with an admin's address take over that admin.
    3. Otherwise, when *provision* is set, a new account is created with no
       password and no role: it can do nothing until something grants it
       rights (RBAC bindings on the person's groups).

    Raises :class:`ExternalLoginRefused` for a disabled account, an email
    that belongs to an account it may not link, or an unknown person when
    provisioning is off.
    """
    if not identity.provider or not identity.subject:
        raise ValueError("ExternalIdentity needs a provider and a subject")
    now = datetime.now(UTC)

    link = (
        await db.execute(
            select(UserIdentity).where(
                UserIdentity.provider == identity.provider,
                UserIdentity.subject == identity.subject,
            )
        )
    ).scalar_one_or_none()
    if link is not None:
        user = await db.get(User, link.user_id)
        if user is None:  # pragma: no cover - the FK cascades
            raise ExternalLoginRefused("no_account", "Your account no longer exists.")
        _refuse_inactive(user)
        link.last_login_at = now
        if identity.display_name and not user.display_name:
            user.display_name = identity.display_name[:255]
        await db.flush()
        return user

    email = _valid_email(identity.email)
    existing = (
        await db.execute(select(User).where(func.lower(User.email) == email.lower()))
    ).scalar_one_or_none()

    if existing is not None:
        if not (link_by_email and identity.email_verified):
            raise ExternalLoginRefused(
                "email_in_use",
                "An unbork account with your email address already exists. "
                "Ask an administrator to link it to your directory account.",
            )
        _refuse_inactive(existing)
        db.add(_new_link(existing.id, identity, now))
        await db.flush()
        await log_action(
            db,
            action="identity_linked",
            entity_type="user",
            entity_id=existing.id,
            user_id=existing.id,
            after_state={"provider": identity.provider, "by": "verified_email"},
        )
        return existing

    if not provision:
        raise ExternalLoginRefused(
            "not_provisioned", "You do not have an unbork account. Ask an administrator for one."
        )

    user = User(
        email=email,
        hashed_password=None,
        role=None,
        is_active=True,
        is_superuser=False,
        is_verified=identity.email_verified,
        display_name=(identity.display_name or None) and identity.display_name[:255],
    )
    db.add(user)
    await db.flush()
    db.add(_new_link(user.id, identity, now))
    await db.flush()
    await log_action(
        db,
        action="user_provisioned",
        entity_type="user",
        entity_id=user.id,
        user_id=user.id,
        after_state={"email": email, "provider": identity.provider},
    )
    return user


def _refuse_inactive(user: User) -> None:
    if not user.is_active:
        raise ExternalLoginRefused("disabled", "Your unbork account is disabled.")


def _new_link(user_id: int, identity: ExternalIdentity, now: datetime) -> UserIdentity:
    return UserIdentity(
        user_id=user_id,
        provider=identity.provider[:128],
        subject=identity.subject[:255],
        created_at=now,
        last_login_at=now,
    )


# ── The session ──────────────────────────────────────────────────────────────


async def issue_session(
    user: User, request: Request | None, response: Response, user_manager
) -> None:
    """Sign *user* in on *response*, the same way password login does.

    Sets the ``labdog_auth`` cookie (a token carrying the user's current
    ``token_version``, so logout, disabling and role changes revoke it like
    any other) and runs ``UserManager.on_after_login``, which sets the CSRF
    cookie and ``last_login_at``. Without the CSRF cookie every later
    mutating request from the browser would fail.
    """
    # Imported here: app.auth.users imports the settings and the database,
    # which an extension importing this module for its types should not pay.
    from app.auth.users import cookie_transport, get_jwt_strategy

    token = await get_jwt_strategy().write_token(user)
    cookie_transport._set_login_cookie(response, token)
    await user_manager.on_after_login(user, request, response)
