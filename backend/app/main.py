from fastapi import FastAPI
from app.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="Barricade", version="0.1.0")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
