import unittest

from cryptography.fernet import Fernet

from bot.crypto import CredentialsCrypto


class CryptoTests(unittest.TestCase):
    def setUp(self):
        self.crypto = CredentialsCrypto(Fernet.generate_key().decode())

    def test_roundtrip(self):
        token = self.crypto.encrypt("super-secret", 42)
        self.assertEqual(self.crypto.decrypt(token, 42), "super-secret")

    def test_aad_mismatch(self):
        token = self.crypto.encrypt("super-secret", 42)
        with self.assertRaises(ValueError):
            self.crypto.decrypt(token, 99)

    def test_ciphertext_not_plaintext(self):
        token = self.crypto.encrypt("super-secret", 1)
        self.assertNotIn("super-secret", token)


if __name__ == "__main__":
    unittest.main()
