from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from bot.db import Database
from bot.services.credentials import CredentialsService
from bot.services.etsy_access import EtsyVncAccess
from bot.services.poller import PollerService
from bot.services.taxonomies import TaxonomyService
from bot.web.api import create_api_router
from bot.web.deletion import create_deletion_router
from bot.web.etsy_access import create_etsy_access_router
from bot.web.rate_limit import RateLimitMiddleware

WEBAPP_DIR = Path(__file__).resolve().parents[2] / "webapp"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _webapp_asset_version() -> str:
    stamps: list[int] = []
    for name in ("app.js", "styles.css", "index.html"):
        path = WEBAPP_DIR / name
        if path.exists():
            stamps.append(path.stat().st_mtime_ns)
    return str(max(stamps) if stamps else 0)


def create_app(
    db: Database,
    public_base_url: str,
    *,
    bot_token: str,
    credentials: CredentialsService,
    bot_username: str = "",
    poller: PollerService | None = None,
    http_proxy: str = "",
    etsy_vnc_access: EtsyVncAccess | None = None,
    taxonomies: TaxonomyService | None = None,
) -> FastAPI:
    app = FastAPI(title="DecoParser web", docs_url=None, redoc_url=None)
    app.add_middleware(RateLimitMiddleware)
    app.include_router(create_deletion_router(db, public_base_url))
    if etsy_vnc_access is not None:
        app.include_router(create_etsy_access_router(etsy_vnc_access))
    app.include_router(
        create_api_router(
            db,
            bot_token,
            credentials=credentials,
            public_base_url=public_base_url,
            bot_username=bot_username,
            http_proxy=http_proxy,
            poller=poller,
            taxonomies=taxonomies,
        )
    )

    @app.get("/health")
    async def health():
        try:
            cursor = await db.conn.execute("SELECT 1")
            await cursor.fetchone()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"ok": True}

    @app.get("/public-config")
    async def public_config():
        return {"bot_username": bot_username}

    if WEBAPP_DIR.exists():
        app.mount("/app/static", StaticFiles(directory=WEBAPP_DIR), name="webapp-static")

        @app.get("/")
        async def landing_index():
            return FileResponse(WEBAPP_DIR / "landing.html")

        @app.get("/landing.css")
        async def landing_css():
            return FileResponse(WEBAPP_DIR / "landing.css", media_type="text/css")

        @app.get("/about")
        @app.get("/about/")
        async def about_page():
            return FileResponse(WEBAPP_DIR / "landing.html")

        @app.get("/app")
        @app.get("/app/")
        async def miniapp_index():
            html = (WEBAPP_DIR / "index.html").read_text(encoding="utf-8")
            version = _webapp_asset_version()
            html = html.replace(
                'href="/app/styles.css"',
                f'href="/app/styles.css?v={version}"',
            )
            html = html.replace(
                'src="/app/app.js"',
                f'src="/app/app.js?v={version}"',
            )
            return HTMLResponse(content=html, headers=dict(NO_CACHE_HEADERS))

        @app.get("/app/styles.css")
        async def miniapp_css():
            return FileResponse(
                WEBAPP_DIR / "styles.css",
                media_type="text/css",
                headers=dict(NO_CACHE_HEADERS),
            )

        @app.get("/app/app.js")
        async def miniapp_js():
            return FileResponse(
                WEBAPP_DIR / "app.js",
                media_type="application/javascript",
                headers=dict(NO_CACHE_HEADERS),
            )

    return app
