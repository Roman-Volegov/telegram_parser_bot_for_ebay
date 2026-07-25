from urllib.parse import parse_qs, urlparse

import pytest

from bot.config import Settings
from bot.services.etsy_access import EtsyVncAccess


def test_etsy_novnc_token_requires_32_characters():
    with pytest.raises(ValueError):
        Settings(
            TELEGRAM_BOT_TOKEN="test-token",
            CREDENTIALS_ENCRYPTION_KEY="test-key",
            ETSY_NOVNC_TOKEN="too-short",
        )


def test_etsy_access_uses_short_lived_ticket_without_password():
    settings = Settings(
        TELEGRAM_BOT_TOKEN="test-token",
        CREDENTIALS_ENCRYPTION_KEY="test-key",
        PUBLIC_BASE_URL="https://example.com:8443/",
        ETSY_NOVNC_TOKEN="a" * 32,
    )
    assert settings.etsy_novnc_ttl_sec == 43_200
    access = EtsyVncAccess(
        settings.public_base_url,
        settings.etsy_novnc_token,
        ttl_sec=600,
    )

    ticket_url = urlparse(access.create_ticket_url())
    ticket = parse_qs(ticket_url.query)["ticket"][0]
    assert access.validate_ticket(ticket)
    assert access.consume_ticket(ticket)
    assert not access.consume_ticket(ticket)

    url = urlparse(access.viewer_url())
    query = parse_qs(url.query)

    assert url.path == f"/{'a' * 32}/vnc.html"
    assert query["autoconnect"] == ["1"]
    assert query["resize"] == ["scale"]
    assert query["path"] == [f"{'a' * 32}/websockify"]
    assert "password" not in query
