import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from bot.etsy_browser_service import (
    _watch_resources,
    pid_threshold_reached,
)
from bot.providers.etsy_browser import _remote_websocket_url


class EtsyBrowserServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_pid_threshold_uses_configured_percentage(self):
        self.assertFalse(
            pid_threshold_reached(409, 512, threshold_percent=80)
        )
        self.assertTrue(
            pid_threshold_reached(410, 512, threshold_percent=80)
        )
        self.assertFalse(
            pid_threshold_reached(None, 512, threshold_percent=80)
        )

    async def test_watchdog_requires_consecutive_high_readings(self):
        restart_event = asyncio.Event()
        values = iter([410, 512, 300, 512, 410, 512, 411, 512, 412, 512])

        with (
            patch.dict(
                os.environ,
                {
                    "ETSY_BROWSER_PID_THRESHOLD_PERCENT": "80",
                    "ETSY_BROWSER_PID_THRESHOLD_CHECKS": "3",
                },
            ),
            patch(
                "bot.etsy_browser_service._read_int",
                side_effect=lambda _: next(values),
            ),
            patch(
                "bot.etsy_browser_service._oom_kill_count",
                return_value=0,
            ),
            patch(
                "bot.etsy_browser_service.asyncio.sleep",
                new=AsyncMock(),
            ),
        ):
            await _watch_resources(restart_event)

        self.assertTrue(restart_event.is_set())

    async def test_remote_debugger_url_uses_browser_service_host(self):
        payload = (
            b'{"webSocketDebuggerUrl":'
            b'"ws://localhost:9224/devtools/browser/browser-id"}'
        )
        with patch(
            "bot.providers.etsy_browser._internal_request",
            return_value=payload,
        ):
            result = await _remote_websocket_url(
                "http://etsy-browser:9222"
            )

        self.assertEqual(
            result,
            "ws://etsy-browser:9222/devtools/browser/browser-id",
        )


if __name__ == "__main__":
    unittest.main()
