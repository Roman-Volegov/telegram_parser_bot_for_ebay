from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bot.db import Database
from bot.services.poller import PollerService
from bot.web.api import create_api_router
from bot.web.deletion import create_deletion_router

WEBAPP_DIR = Path(__file__).resolve().parents[2] / "webapp"


def create_app(
    db: Database,
    public_base_url: str,
    *,
    bot_token: str,
    poller: PollerService | None = None,
) -> FastAPI:
    app = FastAPI(title="DecoParser web", docs_url=None, redoc_url=None)
    app.include_router(create_deletion_router(db, public_base_url))
    app.include_router(create_api_router(db, bot_token, poller=poller))

    @app.get("/health")
    async def health():
        return {"ok": True}

    if WEBAPP_DIR.exists():
        # Статика: /app/styles.css, /app/app.js, /app/
        app.mount("/app/static", StaticFiles(directory=WEBAPP_DIR), name="webapp-static")

        @app.get("/app")
        @app.get("/app/")
        async def miniapp_index():
            return FileResponse(WEBAPP_DIR / "index.html")

        @app.get("/app/styles.css")
        async def miniapp_css():
            return FileResponse(WEBAPP_DIR / "styles.css", media_type="text/css")

        @app.get("/app/app.js")
        async def miniapp_js():
            return FileResponse(
                WEBAPP_DIR / "app.js",
                media_type="application/javascript",
            )

    return app
