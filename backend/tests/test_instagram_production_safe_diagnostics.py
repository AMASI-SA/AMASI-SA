"""Owner-safe diagnostics for the production Instagram subscription boundary."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from customer_intelligence.foundation import CHANNELS_COLLECTION
from customer_intelligence.instagram_provisioning import (
    INSTAGRAM_PROVISION_CONFIRMATION,
    INSTAGRAM_REQUIRED_PERMISSIONS,
    META_ASSETS_COLLECTION,
    META_CREDENTIALS_COLLECTION,
    InstagramProvisionIn,
    InstagramProvisioningError,
    InstagramProvisioningService,
)
from customer_intelligence.routes import make_customer_intelligence_router
from meta_instagram_webhooks import MetaInstagramWebhookError


OWNER_ID = "owner-instagram-safe-diagnostics"
RAW_INSTAGRAM_ID = "17841400000000999"
PAGE_ID = "104200000000999"
STORE_ID = "1014726301562999"


def _matches(document, selector):
    return all(document.get(key) == value for key, value in selector.items())


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, *, length):
        return deepcopy(self.rows[:length])


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or [])

    def find(self, selector, projection=None):
        del projection
        return FakeCursor([row for row in self.rows if _matches(row, selector)])

    async def find_one(self, selector, projection=None):
        del projection
        row = next((row for row in self.rows if _matches(row, selector)), None)
        return deepcopy(row) if row is not None else None

    async def insert_one(self, document):
        self.rows.append(deepcopy(document))
        return SimpleNamespace(inserted_id="created")

    async def update_one(self, selector, update):
        row = next((row for row in self.rows if _matches(row, selector)), None)
        if row is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        row.update(deepcopy(update.get("$set") or {}))
        return SimpleNamespace(matched_count=1, modified_count=1)


class FakeDB:
    def __init__(self):
        # Base Instagram readiness intentionally excludes pages_messaging.
        self.collections = {
            META_CREDENTIALS_COLLECTION: FakeCollection(
                [{
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "scope": sorted(INSTAGRAM_REQUIRED_PERMISSIONS),
                }]
            ),
            META_ASSETS_COLLECTION: FakeCollection(
                [{
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "asset_type": "instagram_account",
                    "connection_status": "connected",
                    "external_asset_id": RAW_INSTAGRAM_ID,
                    "display_name": "amasi.sa",
                    "page_id": PAGE_ID,
                }]
            ),
            "salla_integrations": FakeCollection(
                [{
                    "user_id": OWNER_ID,
                    "status": "connected",
                    "store_id": STORE_ID,
                }]
            ),
            CHANNELS_COLLECTION: FakeCollection(),
        }

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class FailingSubscriber:
    async def __call__(self, db, **kwargs):
        del db, kwargs
        raise MetaInstagramWebhookError(
            "instagram_webhook_subscription_failed",
            operation="subscribe_linked_page_for_instagram",
            http_status=400,
            meta_error_code=200,
            error_subcode=2018065,
            trace_id="TraceABC_123",
        )


@pytest.fixture(autouse=True)
def _channel_secrets(monkeypatch):
    monkeypatch.setenv("MEZAN_CHANNEL_BINDING_HMAC_KEY", "k" * 64)
    monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", Fernet.generate_key().decode())


@pytest.mark.asyncio
async def test_service_propagates_safe_provider_stage_and_missing_page_scope():
    db = FakeDB()
    service = InstagramProvisioningService(
        db,
        webhook_subscriber=FailingSubscriber(),
    )
    setup = await service.setup(owner_user_id=OWNER_ID)

    assert setup.state == "ready"
    assert setup.required_permissions_ready is True
    assert "pages_messaging" not in INSTAGRAM_REQUIRED_PERMISSIONS

    with pytest.raises(InstagramProvisioningError) as failure:
        await service.provision(
            owner_user_id=OWNER_ID,
            request=InstagramProvisionIn(
                candidate_ref=setup.candidates[0].candidate_ref,
                confirmation=INSTAGRAM_PROVISION_CONFIRMATION,
            ),
        )

    error = failure.value
    assert error.code == "instagram_webhook_subscription_failed"
    assert error.operation == "subscribe_linked_page_for_instagram"
    assert error.http_status == 400
    assert error.meta_error_code == 200
    assert error.error_subcode == 2018065
    assert error.trace_id == "TraceABC_123"
    assert error.page_subscription_permission_ready is False
    assert error.missing_page_permissions == ("pages_messaging",)
    assert db.collections[CHANNELS_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_owner_route_returns_only_safe_subscription_diagnostics():
    db = FakeDB()
    service = InstagramProvisioningService(
        db,
        webhook_subscriber=FailingSubscriber(),
    )

    async def current_user():
        return {"id": OWNER_ID, "role": "owner"}

    app = FastAPI()
    app.include_router(
        make_customer_intelligence_router(
            current_user,
            db=None,
            instagram_provisioning_service=service,
        ),
        prefix="/api",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        setup = await client.get(
            "/api/customer-intelligence/v1/channels/instagram/setup"
        )
        candidate_ref = setup.json()["candidates"][0]["candidate_ref"]
        response = await client.post(
            "/api/customer-intelligence/v1/channels/instagram/setup",
            json={
                "candidate_ref": candidate_ref,
                "confirmation": INSTAGRAM_PROVISION_CONFIRMATION,
            },
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail == {
        "code": "instagram_webhook_subscription_failed",
        "operation": "subscribe_linked_page_for_instagram",
        "http_status": 400,
        "meta_error_code": 200,
        "error_subcode": 2018065,
        "trace_id": "TraceABC_123",
        "page_subscription_permission_ready": False,
        "missing_page_permissions": ["pages_messaging"],
    }
    assert RAW_INSTAGRAM_ID not in response.text
    assert PAGE_ID not in response.text
    assert "access_token" not in response.text.casefold()
    assert "sensitive" not in response.text.casefold()
    assert db.collections[CHANNELS_COLLECTION].rows == []
