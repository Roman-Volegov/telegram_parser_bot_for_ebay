from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import quote, urlencode

ETSY_VNC_COOKIE = "etsy_vnc_access"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class EtsyVncAccess:
    def __init__(
        self,
        public_base_url: str,
        secret: str,
        *,
        ttl_sec: int = 600,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("ETSY_NOVNC_TOKEN must contain at least 32 characters")
        self.public_base_url = public_base_url.rstrip("/")
        self.secret = secret.encode("utf-8")
        self.route_token = quote(secret, safe="")
        self.ttl_sec = ttl_sec
        self._consumed_nonces: dict[str, int] = {}

    def create_ticket_url(self) -> str:
        expires_at = int(time.time()) + self.ttl_sec
        payload = json.dumps(
            {
                "exp": expires_at,
                "nonce": secrets.token_urlsafe(12),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = _b64encode(payload)
        signature = _b64encode(
            hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
        )
        return f"{self.public_base_url}/etsy-captcha/access?ticket={encoded}.{signature}"

    def validate_ticket(self, ticket: str) -> bool:
        payload = self._decode_ticket(ticket)
        return payload is not None

    def consume_ticket(self, ticket: str) -> bool:
        payload = self._decode_ticket(ticket)
        if payload is None:
            return False
        now = int(time.time())
        self._consumed_nonces = {
            nonce: expiry
            for nonce, expiry in self._consumed_nonces.items()
            if expiry >= now
        }
        nonce = payload["nonce"]
        if nonce in self._consumed_nonces:
            return False
        self._consumed_nonces[nonce] = payload["exp"]
        return True

    def viewer_url(self) -> str:
        query = urlencode(
            {
                "autoconnect": "1",
                "resize": "scale",
                "path": f"{self.route_token}/websockify",
            }
        )
        return (
            f"{self.public_base_url}/{self.route_token}/vnc.html?"
            f"{query}"
        )

    def _decode_ticket(self, ticket: str) -> dict[str, int | str] | None:
        try:
            encoded, signature = ticket.split(".", 1)
            expected = hmac.new(
                self.secret,
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(expected, _b64decode(signature)):
                return None
            payload = json.loads(_b64decode(encoded))
            expires_at = int(payload["exp"])
            nonce = str(payload["nonce"])
            if expires_at < int(time.time()) or not nonce:
                return None
            return {"exp": expires_at, "nonce": nonce}
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error):
            return None
