"""Manage accounts from the server shell, the way back in when no admin can log in.

Usage::

    python -m app.auth.users_cli create EMAIL --role admin [--password-stdin]
    python -m app.auth.users_cli set-role EMAIL viewer
    python -m app.auth.users_cli reset-password EMAIL [--password-stdin]
    python -m app.auth.users_cli list

A password is read from a prompt, or from stdin with ``--password-stdin``,
never from the command line, where it would land in shell history and in
``ps`` output. It must meet the same policy as everywhere else.

These commands skip the API's guards on purpose (this is the recovery
path), with one exception: ``set-role`` still refuses to leave no active
admin. Each command writes an audit row with no acting user, and
``set-role`` and ``reset-password`` end the account's sessions.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from fastapi_users.password import PasswordHelper
from sqlalchemy import func, select

from app.audit.logger import log_action
from app.auth.password_policy import PasswordPolicyError, check_password
from app.auth.token_version import bump_token_version
from app.db import AsyncSessionLocal
from app.models.user import ROLE_VALUES, User


def _read_password(from_stdin: bool, email: str) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("New password: ")
        if getpass.getpass("Repeat it: ") != password:
            raise SystemExit("Passwords do not match")
    try:
        check_password(password, email=email)
    except PasswordPolicyError as exc:
        raise SystemExit(str(exc)) from exc
    return password


async def _get(session, email: str) -> User:
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        raise SystemExit(f"No user {email}")
    return user


async def create(email: str, role: str, from_stdin: bool, display_name: str | None) -> None:
    password = _read_password(from_stdin, email)
    async with AsyncSessionLocal() as session:
        if (await session.execute(select(User.id).where(User.email == email))).first():
            raise SystemExit(f"User {email} already exists")
        user = User(
            email=email,
            hashed_password=PasswordHelper().hash(password),
            display_name=display_name,
            is_active=True,
            is_verified=True,
        )
        user.role = role
        session.add(user)
        await session.flush()
        await log_action(
            session,
            "create",
            "user",
            entity_id=user.id,
            after_state={"email": email, "role": role, "via": "cli"},
        )
        await session.commit()
        print(f"Created {email} as {role} (id={user.id})")


async def set_role(email: str, role: str) -> None:
    async with AsyncSessionLocal() as session:
        user = await _get(session, email)
        if user.role == "admin" and role != "admin":
            others = await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.role == "admin", User.is_active.is_(True), User.id != user.id)
            )
            if others.scalar_one() == 0:
                raise SystemExit("Refusing: that would leave no active admin")
        before = user.role
        user.role = role
        user.is_active = True
        await log_action(
            session,
            "update",
            "user",
            entity_id=user.id,
            before_state={"role": before},
            after_state={"role": role, "via": "cli"},
        )
        await session.commit()
        await bump_token_version(user)
        print(f"{email}: {before} -> {role}")


async def reset_password(email: str, from_stdin: bool) -> None:
    async with AsyncSessionLocal() as session:
        user = await _get(session, email)
        user.hashed_password = PasswordHelper().hash(_read_password(from_stdin, email))
        user.must_change_password = False
        await log_action(
            session, "reset_password", "user", entity_id=user.id, after_state={"via": "cli"}
        )
        await session.commit()
        await bump_token_version(user)
        print(f"Password reset for {email}; their sessions have ended")


async def list_users() -> None:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(User).order_by(User.id))).scalars()
        for u in rows:
            state = "active" if u.is_active else "disabled"
            print(f"{u.id:>5}  {u.role or '-':<9} {state:<9} {u.email}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.auth.users_cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create", help="create an account")
    p.add_argument("email")
    p.add_argument("--role", choices=ROLE_VALUES, default="viewer")
    p.add_argument("--display-name")
    p.add_argument("--password-stdin", action="store_true")

    p = sub.add_parser("set-role", help="change an account's role (re-enables it)")
    p.add_argument("email")
    p.add_argument("role", choices=ROLE_VALUES)

    p = sub.add_parser("reset-password", help="set a new password")
    p.add_argument("email")
    p.add_argument("--password-stdin", action="store_true")

    sub.add_parser("list", help="list accounts")

    args = parser.parse_args(argv)
    if args.command == "create":
        coro = create(args.email, args.role, args.password_stdin, args.display_name)
    elif args.command == "set-role":
        coro = set_role(args.email, args.role)
    elif args.command == "reset-password":
        coro = reset_password(args.email, args.password_stdin)
    else:
        coro = list_users()
    asyncio.run(coro)


if __name__ == "__main__":
    main()
