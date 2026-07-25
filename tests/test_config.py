from urllib.parse import parse_qs, urlparse

from bot.config import Settings


def test_etsy_novnc_url_contains_connection_settings():
    settings = Settings(
        TELEGRAM_BOT_TOKEN="test-token",
        CREDENTIALS_ENCRYPTION_KEY="test-key",
        PUBLIC_BASE_URL="https://example.com:8443/",
        ETSY_NOVNC_TOKEN="private path",
        ETSY_NOVNC_PASSWORD="secret&password",
    )

    url = urlparse(settings.etsy_novnc_url)
    query = parse_qs(url.query)

    assert url.path == "/private%20path/vnc.html"
    assert query["autoconnect"] == ["1"]
    assert query["resize"] == ["scale"]
    assert query["path"] == ["private%20path/websockify"]
    assert query["password"] == ["secret&password"]


def test_etsy_novnc_url_requires_token_and_password():
    settings = Settings(
        TELEGRAM_BOT_TOKEN="test-token",
        CREDENTIALS_ENCRYPTION_KEY="test-key",
    )

    assert settings.etsy_novnc_url == ""
