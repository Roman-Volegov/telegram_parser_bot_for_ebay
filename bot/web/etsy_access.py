from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from bot.services.etsy_access import ETSY_VNC_COOKIE, EtsyVncAccess


def create_etsy_access_router(access: EtsyVncAccess) -> APIRouter:
    router = APIRouter()

    @router.get("/etsy-captcha/access")
    async def open_etsy_browser(ticket: str = Query(...)):
        if not access.consume_ticket(ticket):
            raise HTTPException(status_code=403, detail="Ссылка истекла или уже использована")
        response = RedirectResponse(access.viewer_url(), status_code=303)
        response.set_cookie(
            ETSY_VNC_COOKIE,
            ticket,
            max_age=access.ttl_sec,
            secure=True,
            httponly=True,
            samesite="strict",
            path=f"/{access.route_token}/",
        )
        return response

    @router.get("/internal/etsy-vnc-auth")
    async def authorize_etsy_browser(request: Request):
        ticket = request.cookies.get(ETSY_VNC_COOKIE, "")
        if not access.validate_ticket(ticket):
            return Response(status_code=401)
        return Response(status_code=204)

    return router
