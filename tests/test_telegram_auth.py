import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode

from bot.web.telegram_auth import TelegramAuthError, validate_init_data


def _sign(bot_token: str, fields: dict[str, str]) -> str:
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()


class TelegramAuthTests(unittest.TestCase):
    def test_valid_init_data(self):
        token = "123456:ABC-DEF"
        user = {"id": 42, "username": "roman", "first_name": "Roman"}
        fields = {
            "auth_date": str(int(time.time())),
            "user": json.dumps(user, separators=(",", ":")),
        }
        fields["hash"] = _sign(token, fields)
        init_data = urlencode(fields)
        parsed = validate_init_data(init_data, token)
        self.assertEqual(parsed.id, 42)
        self.assertEqual(parsed.username, "roman")

    def test_bad_signature(self):
        token = "123456:ABC-DEF"
        fields = {
            "auth_date": str(int(time.time())),
            "user": json.dumps({"id": 1}),
            "hash": "00" * 32,
        }
        with self.assertRaises(TelegramAuthError):
            validate_init_data(urlencode(fields), token)


if __name__ == "__main__":
    unittest.main()
