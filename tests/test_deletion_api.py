import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from cryptography.fernet import Fernet

from bot.crypto import CredentialsCrypto
from bot.db import Database
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


if __name__ == "__main__":
    unittest.main()
