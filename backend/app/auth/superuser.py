"""CLI utility to create a superuser.

Usage:
    python -m app.auth.superuser <email> <password>
"""

import asyncio
import sys

from fastapi_users.password import PasswordHelper
from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.models.user import User


async def create_superuser(email: str, password: str) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        existing = result.scalar_one_or_none()
        if existing:
            print(f"User {email} already exists")
            return

        ph = PasswordHelper()
        user = User(
            email=email,
            hashed_password=ph.hash(password),
            is_active=True,
            is_superuser=True,
            is_verified=True,
        )
        session.add(user)
        await session.commit()
        print(f"Superuser {email} created (id={user.id})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m app.auth.superuser <email> <password>")
        sys.exit(1)
    asyncio.run(create_superuser(sys.argv[1], sys.argv[2]))
