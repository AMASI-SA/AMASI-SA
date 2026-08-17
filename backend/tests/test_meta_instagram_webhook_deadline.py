"""Regression coverage for the Instagram activation origin-timeout boundary."""
from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient, MockTransport, Request, Response

import meta_instagram_webhooks as instagram_webhooks
from meta_instagram_webhooks import (
    META_CREDENTIALS_COLLECTION,
    MetaInstagramWebhookError,
    subscribe_instagram_webhooks,
)


OWNER_ID = "owner-instagram-timeout"
INSTAGRAM_ID = "17841400000000001"
PAGE_ID = "104200000000001"


class FakeCollection:
    def __init__(self, row):
        self.row = row

    async def find_one(self, selector, projection=None):
        del selector, projection
        return dict(self.row)


class FakeDB:
    def __init__(self, credential):
        self.collections = {
            META_CREDENTIALS_COLLECTION: FakeCollection(credential),
        }

    def __getattr__(self, name):
        return self.collections[name]


@pytest.mark.asyncio
async def test_slow_meta_call_returns_controlled_subscription_error(
    monkeypatch,
    caplog,
):
    token_key = Fernet.generate_key()
    user_token = "sensitive-user-token"
    credential = {
        "access_token_ciphertext": Fernet(token_key).encrypt(user_token.encode()),
    }
    db = FakeDB(credential)

    monkeypatch.setenv("META_TOKEN_ENC_KEY", token_key.decode())
    monkeypatch.setenv("META_BUSINESS_APP_ID", "953625110827548")
    monkeypatch.setenv("META_BUSINESS_APP_SECRET", "provider-secret")
    monkeypatch.setattr(
        instagram_webhooks,
        "META_REQUEST_DEADLINE_SECONDS",
        0.01,
    )

    async def slow_handler(request: Request) -> Response:
        del request
        await asyncio.sleep(0.05)
        return Response(200, json={"data": []})

    async with AsyncClient(transport=MockTransport(slow_handler)) as client:
        with pytest.raises(MetaInstagramWebhookError) as failure:
            await subscribe_instagram_webhooks(
                db,
                owner_user_id=OWNER_ID,
                instagram_account_id=INSTAGRAM_ID,
                page_id=PAGE_ID,
                client=client,
            )

    assert failure.value.code == "instagram_webhook_subscription_failed"
    assert "instagram_webhook_meta_timeout" in caplog.text
    assert "operation=resolve_linked_instagram_account" in caplog.text
    assert user_token not in caplog.text
