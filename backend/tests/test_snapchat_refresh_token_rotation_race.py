from datetime import datetime, timedelta, timezone

import pytest

from integrations_control_center import snapchat_native_data_common as common
from tests.test_snapchat_native_data_sync_v2 import FakeDB, FakeResponse

NOW = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)


class RotatingRefreshClient:
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        type(self).calls += 1
        refresh = (kwargs.get("data") or {}).get("refresh_token")

        # First token was already rotated by another worker.
        if refresh == "old-refresh":
            return FakeResponse(
                {
                    "error": "invalid_grant",
                    "error_description": "Refresh token already rotated",
                },
                status_code=400,
            )

        # Re-read of DB must pick up the token saved by the other worker.
        assert refresh == "new-refresh"
        return FakeResponse(
            {
                "access_token": "new-access",
                "refresh_token": "newer-refresh",
                "expires_in": 3600,
            }
        )


@pytest.mark.asyncio
async def test_invalid_grant_rereads_rotated_refresh_token(monkeypatch):
    monkeypatch.setenv("SNAPCHAT_MARKETING_CLIENT_ID", "client-id")
    monkeypatch.setenv("SNAPCHAT_MARKETING_CLIENT_SECRET", "client-secret")

    db = FakeDB(
        {
            common.SNAPCHAT_CREDENTIALS_COLLECTION: [
                {
                    "user_id": "owner-1",
                    "provider": common.SNAPCHAT_PROVIDER_ID,
                    "access_token_ciphertext": b"old-access-cipher",
                    "refresh_token_ciphertext": b"old-refresh-cipher",
                    "access_token_expires_at": NOW - timedelta(minutes=1),
                    "scope": ["snapchat-marketing-api"],
                }
            ]
        }
    )

    reads = {"count": 0}

    def decrypt(value):
        if value == b"old-access-cipher":
            return "old-access"
        if value == b"old-refresh-cipher":
            reads["count"] += 1

            # First read belongs to this worker.
            if reads["count"] == 1:
                # Simulate another worker rotating and persisting the refresh token
                # before our failed invalid_grant retry path re-reads Mongo.
                db.rows[common.SNAPCHAT_CREDENTIALS_COLLECTION][0][
                    "refresh_token_ciphertext"
                ] = b"new-refresh-cipher"
                return "old-refresh"

            return "old-refresh"

        if value == b"new-refresh-cipher":
            return "new-refresh"

        return ""

    monkeypatch.setattr(common, "decrypt_snapchat_token", decrypt)
    monkeypatch.setattr(
        common,
        "encrypt_snapchat_token",
        lambda value: value.encode() if value else None,
    )
    monkeypatch.setattr(common.httpx, "AsyncClient", RotatingRefreshClient)

    context = common.SnapchatSyncContext(
        db,
        "owner-1",
        now=lambda: NOW,
    )

    access = await context.access_token(force_refresh=True)

    assert access == "new-access"
    assert RotatingRefreshClient.calls == 2

    saved = db.rows[common.SNAPCHAT_CREDENTIALS_COLLECTION][0]
    assert saved["access_token_ciphertext"] == b"new-access"
    assert saved["refresh_token_ciphertext"] == b"newer-refresh"
    assert saved["last_refresh_success_at"] == NOW
