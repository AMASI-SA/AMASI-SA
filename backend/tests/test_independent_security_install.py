"""Installation boundary tests; request-level security is tested separately.

Run only in the isolated Linux dependency image with --noconftest. No server
module is imported, and the database boundary rejects every attempted I/O.
"""
import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from auth import install_runtime_security
from login_security import install_login_security


class DatabaseIOForbidden:
    def __getattr__(self, name):
        return CollectionIOForbidden()


class CollectionIOForbidden:
    def __getattr__(self, name):
        raise AssertionError(f"Unexpected database I/O during installation: {name}")


class IndependentSecurityInstallationTests(unittest.TestCase):
    def test_two_apps_have_same_real_chain_without_database_io(self):
        async def verify():
            for _ in range(2):
                app = FastAPI()
                app.add_middleware(CORSMiddleware, allow_origins=["https://synthetic.invalid"])
                await install_runtime_security(app, DatabaseIOForbidden())
                self.assertEqual(
                    [entry.cls.__name__ for entry in app.user_middleware],
                    ["CORSMiddleware", "MobileSessionSecurityMiddleware",
                     "ProgressiveLoginSecurityMiddleware", "LoginSecurityMiddleware",
                     "PasskeySecurityMiddleware", "MfaSecurityMiddleware",
                     "EmailOtpSecurityMiddleware"],
                )
                routes = [route.path for route in app.routes]
                self.assertEqual(routes.count("/api/auth/meta-reviewer-bootstrap"), 1)
                stack = app.middleware_stack
                await install_runtime_security(app, DatabaseIOForbidden())
                self.assertIs(app.middleware_stack, stack)
                self.assertEqual([route.path for route in app.routes], routes)
        with patch.dict(os.environ, {"JWT_SECRET": "synthetic-test-key",
                                     "EMAIL_OTP_SMTP_HOST": "127.0.0.1",
                                     "EMAIL_OTP_FROM_EMAIL": "otp@synthetic.invalid"}, clear=True):
            asyncio.run(verify())

    def test_legacy_installer_still_attempts_indexes(self):
        app = FastAPI()
        with self.assertRaisesRegex(AssertionError, "create_index"):
            asyncio.run(install_login_security(app, DatabaseIOForbidden()))
        self.assertFalse(getattr(app.state, "mezan_login_security_installed", False))

    def test_explicit_target_is_required(self):
        with self.assertRaisesRegex(ValueError, "explicit FastAPI app"):
            asyncio.run(install_runtime_security(None, DatabaseIOForbidden()))

    def test_enabled_otp_configuration_failure_is_not_suppressed(self):
        app = FastAPI()
        with patch.dict(os.environ, {"EMAIL_OTP_ENABLED": "true", "JWT_SECRET": "synthetic-test-key"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "EMAIL_OTP_SMTP_HOST is required"):
                asyncio.run(install_runtime_security(app, DatabaseIOForbidden()))
        self.assertFalse(getattr(app.state, "mezan_email_otp_security_installed", False))


if __name__ == "__main__":
    unittest.main()
