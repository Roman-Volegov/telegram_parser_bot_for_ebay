import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from cryptography.fernet import Fernet

from bot.crypto import CredentialsCrypto
from bot.db import Database
from bot.models import Source, UserStatus
from bot.services.credentials import CredentialsService
from bot.services.etsy_access import ETSY_VNC_COOKIE, EtsyVncAccess
from bot.web.app import create_app
from bot.web.deletion import deletion_endpoint


class DeletionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.db"))
        await self.db.connect()
        await self.db.upsert_pending_user(10, "u", "User")
        await self.db.set_user_status(10, UserStatus.APPROVED)
        await self.db.save_setup(
            10,
            enabled_sources=[Source.ETSY, Source.POSHMARK, Source.EBAY_PARSER],
            setup_completed=True,
        )
        self.token = await self.db.ensure_deletion_token(10)
        self.deletion_endpoint = deletion_endpoint(
            "https://example.com",
            10,
            self.token,
        )
        self.deletion_path = self.deletion_endpoint.removeprefix("https://example.com")
        credentials = CredentialsService(
            self.db, CredentialsCrypto(Fernet.generate_key().decode())
        )
        self.etsy_access = EtsyVncAccess("https://example.com", "a" * 32)
        self.app = create_app(
            self.db,
            "https://example.com",
            bot_token="123:test",
            credentials=credentials,
            etsy_vnc_access=self.etsy_access,
        )
        self.client = TestClient(self.app)
        self.auth_headers = {
            "X-Telegram-Init-Data": self._telegram_init_data(10, "123:test")
        }

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_challenge(self):
        response = self.client.get(
            self.deletion_path,
            params={"challenge_code": "hello"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("challengeResponse", body)
        self.assertEqual(len(body["challengeResponse"]), 64)

    async def test_post_ok(self):
        response = self.client.post(self.deletion_path, json={"notification": {}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    async def test_deletion_rejects_unknown_route(self):
        response = self.client.post(
            "/ebay/deletion/10/not-the-route-token",
            json={"notification": {}},
        )
        self.assertEqual(response.status_code, 404)

    async def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

    async def test_api_requires_telegram_auth(self):
        response = self.client.get("/api/me")
        self.assertEqual(response.status_code, 401)

    async def test_multi_source_search_is_one_card_and_edits_as_group(self):
        created = self.client.post(
            "/api/searches",
            headers=self.auth_headers,
            json={
                "sources": ["etsy", "poshmark"],
                "keywords": "vintage brooch",
                "min_price": 10,
                "max_price": 100,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        search_id = created.json()["id"]

        listed = self.client.get("/api/searches", headers=self.auth_headers).json()["items"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(set(listed[0]["sources"]), {"etsy", "poshmark"})

        updated = self.client.patch(
            f"/api/searches/{search_id}",
            headers=self.auth_headers,
            json={
                "sources": ["etsy", "ebay_parser"],
                "keywords": "signed vintage brooch",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(set(updated.json()["sources"]), {"etsy", "ebay_parser"})
        listed = self.client.get("/api/searches", headers=self.auth_headers).json()["items"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["keywords"], "signed vintage brooch")

        deleted = self.client.delete(
            f"/api/searches/{search_id}",
            headers=self.auth_headers,
        )
        self.assertEqual(deleted.status_code, 200)
        listed = self.client.get("/api/searches", headers=self.auth_headers).json()["items"]
        self.assertEqual(listed, [])

    async def test_etsy_access_ticket_is_single_use(self):
        ticket_url = self.etsy_access.create_ticket_url()
        response = self.client.get(ticket_url, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("password=", response.headers["location"])
        ticket = response.cookies.get(ETSY_VNC_COOKIE)
        self.assertTrue(ticket)

        auth = self.client.get(
            "/internal/etsy-vnc-auth",
            headers={"Cookie": f"{ETSY_VNC_COOKIE}={ticket}"},
        )
        self.assertEqual(auth.status_code, 204)
        reused = self.client.get(ticket_url, follow_redirects=False)
        self.assertEqual(reused.status_code, 403)

    @staticmethod
    def _telegram_init_data(user_id: int, bot_token: str) -> str:
        values = {
            "auth_date": str(int(time.time())),
            "query_id": "test-query",
            "user": json.dumps(
                {"id": user_id, "username": "u"},
                separators=(",", ":"),
            ),
        }
        data_check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret = hmac.new(
            b"WebAppData",
            bot_token.encode(),
            hashlib.sha256,
        ).digest()
        values["hash"] = hmac.new(
            secret,
            data_check.encode(),
            hashlib.sha256,
        ).hexdigest()
        return urlencode(values)


if __name__ == "__main__":
    unittest.main()
