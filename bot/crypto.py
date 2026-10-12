from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class CredentialsCrypto:
    """Fernet-шифрование с привязкой ciphertext к telegram_id (AAD)."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str, telegram_id: int) -> str:
        payload = json.dumps(
            {"aad": int(telegram_id), "v": plaintext},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def decrypt(self, token: str, telegram_id: int) -> str:
        try:
            raw = self._fernet.decrypt(token.encode("ascii"))
        except InvalidToken as exc:
            raise ValueError("Не удалось расшифровать credentials") from exc
        try:
            payload: dict[str, Any] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Повреждённый ciphertext") from exc
        if int(payload.get("aad", -1)) != int(telegram_id):
            raise ValueError("AAD mismatch: credentials принадлежат другому пользователю")
        value = payload.get("v")
        if not isinstance(value, str):
            raise ValueError("Некорректный формат credentials")
        return value
