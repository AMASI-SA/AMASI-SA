from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from scripts import production_release_guard as guard


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.headers = _Headers({"Content-Type": "application/json"})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class ProductionReleaseGuardTests(unittest.TestCase):
    def test_health_payload_uses_public_api_route_and_cloudflare_safe_user_agent(self):
        response = _Response({"ok": True, "release": {"git_sha": "a" * 40}})
        with patch.object(
            guard.urllib.request,
            "urlopen",
            return_value=response,
        ) as opened:
            self.assertTrue(
                guard._health_payload("https://mezansalla.com/")["ok"]
            )

        request = opened.call_args.args[0]
        self.assertTrue(
            request.full_url.startswith("https://mezansalla.com/api/health?")
        )
        self.assertTrue(
            request.get_header("User-agent", "").startswith("Mozilla/5.0")
        )
        self.assertEqual(request.get_header("Accept"), "application/json")


if __name__ == "__main__":
    unittest.main()
