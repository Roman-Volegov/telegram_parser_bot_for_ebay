from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from bot.db import Database

logger = logging.getLogger(__name__)


def create_deletion_router(db: Database, public_base_url: str) -> APIRouter:
    router = APIRouter()

    @router.get("/ebay/deletion/{telegram_id}")
    async def deletion_challenge(
        telegram_id: int,
        challenge_code: str | None = Query(default=None),
    ):
        if not challenge_code:
            return {
                "ok": True,
                "endpoint": f"{public_base_url}/ebay/deletion/{telegram_id}",
            }
        user = await db.get_user(telegram_id)
        if user is None or not user.ebay_deletion_token:
            raise HTTPException(status_code=404, detail="Unknown user/token")
        endpoint = f"{public_base_url}/ebay/deletion/{telegram_id}"
        digest = hashlib.sha256(
            (challenge_code + user.ebay_deletion_token + endpoint).encode("utf-8")
        ).hexdigest()
        return JSONResponse({"challengeResponse": digest})

    @router.post("/ebay/deletion/{telegram_id}")
    async def deletion_notification(telegram_id: int, request: Request):
        user = await db.get_user(telegram_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Unknown user")
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
