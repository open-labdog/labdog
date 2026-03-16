from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.schemas import UserCreate, UserRead, UserUpdate
from app.auth.users import auth_backend, fastapi_users
from app.api.hosts import router as hosts_router
from app.api.permissions import router as permissions_router
from app.api.groups import router as groups_router
from app.api.ssh_keys import router as ssh_keys_router
from app.api.rules import router as rules_router
from app.api.sync import router as sync_router
from app.api.drift import router as drift_router
from app.api.audit import router as audit_router
from app.api.discovery import router as discovery_router
from app.api.webhooks import router as webhooks_router
from app.api.git_repos import router as git_repos_router
from app.api.admin_users import router as admin_users_router


def create_app() -> FastAPI:
    app = FastAPI(title="Barricade", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth routes: POST /auth/jwt/login, POST /auth/jwt/logout
    app.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix="/auth/jwt",
        tags=["auth"],
    )

    # Register route: POST /auth/register
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix="/auth",
        tags=["auth"],
    )

    # User routes: GET /users/me, PATCH /users/me, GET /users/{id}, etc.
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/users",
        tags=["users"],
    )

    app.include_router(permissions_router, prefix="/api")
    app.include_router(groups_router, prefix="/api")
    app.include_router(hosts_router, prefix="/api")
    app.include_router(ssh_keys_router, prefix="/api")
    app.include_router(rules_router, prefix="/api")
    app.include_router(sync_router, prefix="/api")
    app.include_router(drift_router, prefix="/api")
    app.include_router(audit_router, prefix="/api")
    app.include_router(discovery_router, prefix="/api")
    app.include_router(git_repos_router, prefix="/api")
    app.include_router(admin_users_router, prefix="/api")

    # Webhooks at /webhooks/ (NOT under /api prefix)
    app.include_router(webhooks_router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
