"""Regression tests for the fail-closed Preview password-only policy."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import jwt
from starlette.responses import JSONResponse

from auth_preview_policy import preview_password_only_enabled
from mfa_security import MfaSecurityMiddleware


def _scope(
    host: str,
    *,
    forwarded_host: str | None = None,
    client: tuple[str, int] = ("127.0.0.1", 12345),
) -> dict:
    headers = [(b"host", host.encode("ascii"))]
    if forwarded_host:
        headers.append((b"x-forwarded-host", forwarded_host.encode("ascii")))
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": headers,
        "client": client,
    }


class PreviewPasswordOnlyPolicyTests(unittest.TestCase):
    def test_requires_all_three_guards(self):
        preview_scope = _scope("salla-analytics.preview.emergentagent.com")

        with patch.dict(os.environ, {"MEZAN_ENVIRONMENT": "preview"}, clear=True):
            self.assertFalse(preview_password_only_enabled(preview_scope))

        with patch.dict(
            os.environ,
            {"AUTH_PREVIEW_PASSWORD_ONLY": "true", "MEZAN_ENVIRONMENT": "production"},
            clear=True,
        ):
            self.assertFalse(preview_password_only_enabled(preview_scope))

        with patch.dict(
            os.environ,
            {"AUTH_PREVIEW_PASSWORD_ONLY": "true", "MEZAN_ENVIRONMENT": "preview"},
            clear=True,
        ):
            self.assertFalse(
                preview_password_only_enabled(_scope("salla-analytics.emergent.host"))
            )
            self.assertTrue(preview_password_only_enabled(preview_scope))

    def test_never_accepts_a_suffix_or_production_host(self):
        env = {"AUTH_PREVIEW_PASSWORD_ONLY": "1", "MEZAN_ENVIRONMENT": "preview"}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(
                preview_password_only_enabled(
                    _scope("salla-analytics.preview.emergentagent.com.evil.example")
                )
            )
            self.assertFalse(
                preview_password_only_enabled(_scope("salla-analytics.emergent.host"))
            )

    def test_allowed_host_is_exact_and_configurable(self):
        env = {
            "AUTH_PREVIEW_PASSWORD_ONLY": "yes",
            "MEZAN_ENVIRONMENT": "preview",
            "AUTH_PREVIEW_ALLOWED_HOSTS": "preview.example.test",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(preview_password_only_enabled(_scope("preview.example.test:443")))
            self.assertFalse(
                preview_password_only_enabled(
                    _scope("salla-analytics.preview.emergentagent.com")
                )
            )

    def test_private_preview_proxy_requires_separate_opt_in(self):
        proxied_scope = _scope(
            "127.0.0.1:8001",
            forwarded_host="salla-analytics.preview.emergentagent.com",
            client=("10.79.142.69", 57916),
        )
        base_env = {
            "AUTH_PREVIEW_PASSWORD_ONLY": "true",
            "MEZAN_ENVIRONMENT": "preview",
        }
        with patch.dict(os.environ, base_env, clear=True):
            self.assertFalse(preview_password_only_enabled(proxied_scope))

        with patch.dict(
            os.environ,
            {**base_env, "AUTH_PREVIEW_TRUST_PROXY": "true"},
            clear=True,
        ):
            self.assertTrue(preview_password_only_enabled(proxied_scope))

    def test_forwarded_host_is_rejected_from_public_peer(self):
        env = {
            "AUTH_PREVIEW_PASSWORD_ONLY": "true",
            "AUTH_PREVIEW_TRUST_PROXY": "true",
            "MEZAN_ENVIRONMENT": "preview",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(
                preview_password_only_enabled(
                    _scope(
                        "127.0.0.1:8001",
                        forwarded_host="salla-analytics.preview.emergentagent.com",
                        client=("8.8.8.8", 57916),
                    )
                )
            )

    def test_forwarded_production_host_never_enables_preview_mode(self):
        env = {
            "AUTH_PREVIEW_PASSWORD_ONLY": "true",
            "AUTH_PREVIEW_TRUST_PROXY": "true",
            "MEZAN_ENVIRONMENT": "preview",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(
                preview_password_only_enabled(
                    _scope(
                        "127.0.0.1:8001",
                        forwarded_host="salla-analytics.emergent.host",
                        client=("10.79.142.69", 57916),
                    )
                )
            )


class _Collection:
    def __init__(self):
        self.rows = []

    async def insert_one(self, document):
        self.rows.append(dict(document))


class _Users:
    def __init__(self, user):
        self.user = dict(user)

    async def find_one(self, query):
        if query.get("email") == self.user.get("email"):
            return dict(self.user)
        return None


class _Db:
    def __init__(self, user):
        self.users = _Users(user)
        self.auth_mfa_challenges = _Collection()
        self.auth_security_events = _Collection()


class PreviewPasswordOnlyMfaTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_gets_verified_preview_session_without_bootstrap(self):
        user = {
            "id": "owner-preview-1",
            "email": "owner@example.com",
            "name": "Preview Owner",
            "role": "owner",
            "mfa_enabled": False,
        }
        db = _Db(user)

        async def canonical_password_app(scope, receive, send):
            await receive()
            await JSONResponse({"ok": True}, status_code=200)(scope, receive, send)

        middleware = MfaSecurityMiddleware(canonical_password_app, db=db)
        request_body = json.dumps({"email": user["email"], "password": "correct"}).encode()
        request_messages = [
            {"type": "http.request", "body": request_body, "more_body": False}
        ]
        response_messages = []

        async def receive():
            return request_messages.pop(0)

        async def send(message):
            response_messages.append(dict(message))

        env = {
            "AUTH_PREVIEW_PASSWORD_ONLY": "true",
            "MEZAN_ENVIRONMENT": "preview",
            "JWT_SECRET": "preview-auth-policy-test-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            await middleware(
                _scope("salla-analytics.preview.emergentagent.com"),
                receive,
                send,
            )

        start = next(item for item in response_messages if item["type"] == "http.response.start")
        body = next(item for item in response_messages if item["type"] == "http.response.body")
        payload = json.loads(body["body"])
        headers = {name.lower(): value for name, value in start["headers"]}

        self.assertEqual(start["status"], 200)
        self.assertEqual(headers[b"x-mezan-auth-mode"], b"preview-password-only")
        self.assertTrue(payload["mfa_verified"])
        token_payload = jwt.decode(
            payload["access_token"],
            env["JWT_SECRET"],
            algorithms=["HS256"],
        )
        self.assertTrue(token_payload["mfa"])
        self.assertFalse(user["mfa_enabled"])
        self.assertEqual(
            db.auth_security_events.rows[0]["event_type"],
            "mfa_preview_password_only_login",
        )


if __name__ == "__main__":
    unittest.main()
