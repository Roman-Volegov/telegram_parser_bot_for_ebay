from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class TelegramAuthError(Exception):
    pass


@dataclass(slots=True, frozen=True)
class TelegramWebAppUser:
    id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_sec: int = 86400,
) -> TelegramWebAppUser:
    """Проверка подписи Telegram WebApp initData."""
    if not init_data or not init_data.strip():
        raise TelegramAuthError("initData пустой")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise TelegramAuthError("hash отсутствует")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise TelegramAuthError("Неверная подпись initData")

    auth_date = int(parsed.get("auth_date") or "0")
    if auth_date <= 0:
        raise TelegramAuthError("auth_date отсутствует")
    if max_age_sec > 0 and time.time() - auth_date > max_age_sec:
        raise TelegramAuthError("initData устарел")

    user_raw = parsed.get("user")
    if not user_raw:
        raise TelegramAuthError("user отсутствует")
    try:
        user_obj = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise TelegramAuthError("user повреждён") from exc

    user_id = user_obj.get("id")
    if not isinstance(user_id, int):
        raise TelegramAuthError("user.id некорректен")

    return TelegramWebAppUser(
        id=user_id,
        username=user_obj.get("username"),
        first_name=user_obj.get("first_name"),
        last_name=user_obj.get("last_name"),
    )
