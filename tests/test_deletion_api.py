import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from cryptography.fernet import Fernet

from bot.crypto import CredentialsCrypto
from bot.db import Database
from bot.services.credentials import CredentialsService
from bot.web.app import create_app


class DeletionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.db"))
        await self.db.connect()
        await self.db.upsert_pending_user(10, "u", "User")
        self.token = await self.db.ensure_deletion_token(10)
        credentials = CredentialsService(
            self.db, CredentialsCrypto(Fernet.generate_key().decode())
        )
        self.app = create_app(
            self.db,
            "https://example.com",
            bot_token="123:test",
            credentials=credentials,
        )
        self.client = TestClient(self.app)

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_challenge(self):
        response = self.client.get(
            "/ebay/deletion/10",
            params={"challenge_code": "hello"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("challengeResponse", body)
        self.assertEqual(len(body["challengeResponse"]), 64)

    async def test_post_ok(self):
        response = self.client.post("/ebay/deletion/10", json={"notification": {}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    async def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
