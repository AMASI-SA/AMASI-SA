"""Regression coverage for Instagram activation provider boundaries."""
from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient, MockTransport, Request, Response

import meta_instagram_webhooks as instagram_webhooks
from meta_instagram_webhooks import (
    META_CREDENTIALS_COLLECTION,
    PAGE_WEBHOOK_INSTALL_FIELDS,
    MetaInstagramWebhookError,
    subscribe_instagram_webhooks,
)


OWNER_ID = "owner-instagram-timeout"
INSTAGRAM_ID = "17841400000000001"
PAGE_ID = "104200000000001"
APP_ID = "953625110827548"


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


def _configured_db(monkeypatch, *, user_token: str = "sensitive-user-token"):
    token_key = Fernet.generate_key()
    credential = {
        "access_token_ciphertext": Fernet(token_key).encrypt(user_token.encode()),
    }
    monkeypatch.setenv("META_TOKEN_ENC_KEY", token_key.decode())
    monkeypatch.setenv("META_BUSINESS_APP_ID", APP_ID)
    monkeypatch.setenv("META_BUSINESS_APP_SECRET", "provider-secret")
    return FakeDB(credential), user_token


@pytest.mark.asyncio
async def test_slow_meta_call_returns_controlled_subscription_error(
    monkeypatch,
    caplog,
):
    db, user_token = _configured_db(monkeypatch)
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
    assert failure.value.operation == "resolve_linked_instagram_account"
    assert "instagram_webhook_meta_timeout" in caplog.text
    assert "operation=resolve_linked_instagram_account" in caplog.text
    assert user_token not in caplog.text


@pytest.mark.asyncio
async def test_account_install_rejection_falls_back_to_linked_page_without_token_leak(
    monkeypatch,
    caplog,
):
    db, user_token = _configured_db(monkeypatch)
    page_token = "sensitive-page-token"
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.url.path.endswith("/me/accounts"):
            return Response(
                200,
                json={
                    "data": [{
                        "id": PAGE_ID,
                        "access_token": page_token,
                        "instagram_business_account": {"id": INSTAGRAM_ID},
                    }]
                },
            )
        if request.url.path.endswith(f"/{INSTAGRAM_ID}/subscribed_apps"):
            assert request.method == "POST"
            assert request.url.params["subscribed_fields"] == "messages,comments"
            return Response(
                400,
                json={
                    "error": {
                        "code": 100,
                        "error_subcode": 33,
                        "fbtrace_id": "FallbackTrace_123",
                    }
                },
            )
        if request.url.path.endswith(f"/{PAGE_ID}/subscribed_apps"):
            if request.method == "POST":
                assert request.url.params["subscribed_fields"] == ",".join(
                    PAGE_WEBHOOK_INSTALL_FIELDS
                )
                assert request.url.params["access_token"] == page_token
                return Response(200, json={"success": True})
            assert request.method == "GET"
            return Response(
                200,
                json={
                    "data": [{
                        "id": APP_ID,
                        "subscribed_fields": ["messages"],
                    }]
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    async with AsyncClient(transport=MockTransport(handler)) as client:
        fields = await subscribe_instagram_webhooks(
            db,
            owner_user_id=OWNER_ID,
            instagram_account_id=INSTAGRAM_ID,
            page_id=PAGE_ID,
            client=client,
        )

    assert fields == ("messages", "comments")
    assert [request.method for request in requests] == ["GET", "POST", "POST", "GET"]
    assert "instagram_webhook_subscription_fallback" in caplog.text
    assert "operation=subscribe_instagram_account" in caplog.text
    assert "meta_error_code=100" in caplog.text
    assert "error_subcode=33" in caplog.text
    assert "trace_id=FallbackTrace_123" in caplog.text
    assert user_token not in caplog.text
    assert page_token not in caplog.text
