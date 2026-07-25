from __future__ import annotations

from bot.crypto import CredentialsCrypto
from bot.db import Database
from bot.models import User
from bot.providers.ebay_api import EbayApiProvider
from bot.providers.etsy import EtsyProvider


def normalize_etsy_api_key(keystring: str, shared_secret: str = "") -> str:
    """Собирает x-api-key в формате keystring:shared_secret."""
    key = (keystring or "").strip()
    secret = (shared_secret or "").strip()
    if not key:
        return ""
    if ":" in key and not secret:
        return key
    if secret:
        return f"{key}:{secret}"
    return key


class CredentialsService:
    def __init__(self, db: Database, crypto: CredentialsCrypto) -> None:
        self.db = db
        self.crypto = crypto
        self._ebay_cache: dict[int, tuple[str, str]] = {}
        self._etsy_cache: dict[int, str] = {}

    async def save_ebay_keys(
        self, telegram_id: int, client_id: str, client_secret: str
    ) -> None:
        enc_id = self.crypto.encrypt(client_id.strip(), telegram_id)
        enc_secret = self.crypto.encrypt(client_secret.strip(), telegram_id)
        await self.db.save_credentials(telegram_id, enc_id, enc_secret)
        self._ebay_cache[telegram_id] = (client_id.strip(), client_secret.strip())

    async def get_ebay_keys(self, telegram_id: int) -> tuple[str, str] | None:
        cached = self._ebay_cache.get(telegram_id)
        if cached is not None:
            return cached
        enc = await self.db.get_credentials_enc(telegram_id)
        if enc is None:
            return None
        client_id = self.crypto.decrypt(enc[0], telegram_id)
        client_secret = self.crypto.decrypt(enc[1], telegram_id)
        self._ebay_cache[telegram_id] = (client_id, client_secret)
        return self._ebay_cache[telegram_id]

    async def save_etsy_key(
        self,
        telegram_id: int,
        keystring: str,
        shared_secret: str = "",
    ) -> None:
        api_key = normalize_etsy_api_key(keystring, shared_secret)
        if not api_key or ":" not in api_key:
            raise ValueError(
                "Etsy API key: укажите keystring и shared_secret "
                "(или одну строку keystring:shared_secret)"
            )
        enc = self.crypto.encrypt(api_key, telegram_id)
        await self.db.save_etsy_credentials(telegram_id, enc)
        self._etsy_cache[telegram_id] = api_key

    async def get_etsy_key(self, telegram_id: int) -> str | None:
        cached = self._etsy_cache.get(telegram_id)
        if cached is not None:
            return cached
        enc = await self.db.get_etsy_credentials_enc(telegram_id)
        if enc is None:
            return None
        self._etsy_cache[telegram_id] = self.crypto.decrypt(enc, telegram_id)
        return self._etsy_cache[telegram_id]

    async def revoke(self, telegram_id: int) -> bool:
        revoked = await self.db.revoke_credentials(telegram_id)
        self._ebay_cache.pop(telegram_id, None)
        return revoked

    async def revoke_etsy(self, telegram_id: int) -> bool:
        revoked = await self.db.revoke_etsy_credentials(telegram_id)
        self._etsy_cache.pop(telegram_id, None)
        return revoked

    async def build_ebay_api_provider(
        self,
        user: User,
        *,
        proxy: str | None = None,
        marketplace_id: str | None = None,
    ) -> EbayApiProvider:
        keys = await self.get_ebay_keys(user.telegram_id)
        if keys is None:
            raise ValueError("eBay API ключи не сохранены")
        client_id, client_secret = keys
        return EbayApiProvider(
            client_id=client_id,
            client_secret=client_secret,
            marketplace_id=marketplace_id or user.ebay_marketplace,
            telegram_id=user.telegram_id,
            proxy=proxy,
        )

    async def build_etsy_provider(
        self,
        user: User,
        *,
        proxy: str | None = None,
    ) -> EtsyProvider:
        # Ключ опционален: без него используется Playwright
        api_key = await self.get_etsy_key(user.telegram_id)
        return EtsyProvider(proxy=proxy, api_key=api_key)

    async def keys_status_text(self, telegram_id: int) -> str:
        has_ebay = await self.db.has_credentials(telegram_id)
        has_etsy = await self.db.has_etsy_credentials(telegram_id)
        ebay_bit = (
            "🔑 eBay API ключи: сохранены (зашифрованы)"
            if has_ebay
            else "🔑 eBay API ключи: не заданы"
        )
        etsy_bit = (
            "🔑 Etsy API ключ: сохранён (зашифрован)"
            if has_etsy
            else "🔑 Etsy: Playwright (ключ не нужен)"
        )
        return f"{ebay_bit}\n{etsy_bit}"
