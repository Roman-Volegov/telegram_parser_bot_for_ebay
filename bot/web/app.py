from __future__ import annotations

from fastapi import FastAPI

from bot.db import Database
from bot.web.deletion import create_deletion_router


def create_app(db: Database, public_base_url: str) -> FastAPI:
    app = FastAPI(title="eBay deletion webhook", docs_url=None, redoc_url=None)
    app.include_router(create_deletion_router(db, public_base_url))

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app
