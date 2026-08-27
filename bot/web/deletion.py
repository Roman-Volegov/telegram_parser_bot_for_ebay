from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from bot.db import Database

logger = logging.getLogger(__name__)


def deletion_endpoint(
    public_base_url: str,
    telegram_id: int,
    verification_token: str,
) -> str:
    route_token = hashlib.sha256(
        f"endpoint:{verification_token}".encode("utf-8")
    ).hexdigest()[:32]
    return (
        f"{public_base_url.rstrip('/')}/ebay/deletion/"
        f"{telegram_id}/{route_token}"
    )


def create_deletion_router(db: Database, public_base_url: str) -> APIRouter:
    router = APIRouter()

    @router.get("/ebay/deletion/{telegram_id}/{route_token}")
    async def deletion_challenge(
        telegram_id: int,
        route_token: str,
        challenge_code: str | None = Query(default=None),
    ):
        user = await db.get_user(telegram_id)
        if user is None or not user.ebay_deletion_token:
            raise HTTPException(status_code=404, detail="Unknown endpoint")
        endpoint = deletion_endpoint(
            public_base_url,
            telegram_id,
            user.ebay_deletion_token,
        )
        if not hmac.compare_digest(endpoint.rsplit("/", 1)[-1], route_token):
            raise HTTPException(status_code=404, detail="Unknown endpoint")
        if not challenge_code:
            return {"ok": True, "endpoint": endpoint}
        digest = hashlib.sha256(
            (challenge_code + user.ebay_deletion_token + endpoint).encode("utf-8")
        ).hexdigest()
        return JSONResponse({"challengeResponse": digest})

    @router.post("/ebay/deletion/{telegram_id}/{route_token}")
    async def deletion_notification(
        telegram_id: int,
        route_token: str,
        request: Request,
    ):
        user = await db.get_user(telegram_id)
        if user is None or not user.ebay_deletion_token:
            raise HTTPException(status_code=404, detail="Unknown endpoint")
        expected_route = deletion_endpoint(
            public_base_url,
            telegram_id,
            user.ebay_deletion_token,
        ).rsplit("/", 1)[-1]
        if not hmac.compare_digest(expected_route, route_token):
            raise HTTPException(status_code=404, detail="Unknown endpoint")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        logger.info(
            "eBay deletion notification for telegram_id=%s payload_keys=%s",
            telegram_id,
            list(payload.keys()) if isinstance(payload, dict) else type(payload),
        )
        # Согласно требованиям eBay — отвечаем 200 OK.
        return {"ok": True}

    return router
