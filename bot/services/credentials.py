from __future__ import annotations

from bot.crypto import CredentialsCrypto
from bot.db import Database
from bot.models import Source, User
from bot.providers.ebay_api import EbayApiProvider


class CredentialsService:
    def __init__(self, db: Database, crypto: CredentialsCrypto) -> None:
        self.db = db
        self.crypto = crypto

    async def save_ebay_keys(
        self, telegram_id: int, client_id: str, client_secret: str
    ) -> None:
        enc_id = self.crypto.encrypt(client_id.strip(), telegram_id)
        enc_secret = self.crypto.encrypt(client_secret.strip(), telegram_id)
        await self.db.save_credentials(telegram_id, enc_id, enc_secret)

    async def get_ebay_keys(self, telegram_id: int) -> tuple[str, str] | None:
        enc = await self.db.get_credentials_enc(telegram_id)
        if enc is None:
            return None
        client_id = self.crypto.decrypt(enc[0], telegram_id)
        client_secret = self.crypto.decrypt(enc[1], telegram_id)
        return client_id, client_secret

    async def revoke(self, telegram_id: int) -> bool:
        return await self.db.revoke_credentials(telegram_id)

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

    async def keys_status_text(self, telegram_id: int) -> str:
        has = await self.db.has_credentials(telegram_id)
        if has:
            return "🔑 eBay API ключи: сохранены (зашифрованы)"
        return "🔑 eBay API ключи: не заданы"
