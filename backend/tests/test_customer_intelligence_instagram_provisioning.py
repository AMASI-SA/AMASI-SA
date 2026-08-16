"""Owner-confirmed Instagram binding from secret-safe Meta discovery assets."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from pymongo.errors import DuplicateKeyError

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
from meta_instagram_webhooks import (
    MetaInstagramWebhookError,
    subscribe_instagram_webhooks,
)


NOW = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
OWNER_ID = "owner-instagram-setup"
RAW_INSTAGRAM_ID = "17841400000000001"
PAGE_ID = "104200000000001"


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
        if any(
            row.get("external_account_key") == document.get("external_account_key")
            and row.get("provider") == document.get("provider")
            for row in self.rows
        ):
            raise DuplicateKeyError("duplicate binding")
        self.rows.append(deepcopy(document))
        return SimpleNamespace(inserted_id="created")

    async def update_one(self, selector, update):
        row = next((row for row in self.rows if _matches(row, selector)), None)
        if row is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        row.update(deepcopy(update.get("$set") or {}))
        return SimpleNamespace(matched_count=1, modified_count=1)


class RecordingSubscriber:
    def __init__(self, *, error_code=None):
        self.calls = []
        self.error_code = error_code

    async def __call__(self, db, **kwargs):
        del db
        self.calls.append(deepcopy(kwargs))
        if self.error_code:
            raise MetaInstagramWebhookError(self.error_code)
        return ("comments", "messages")


class FakeDB:
    def __init__(self, *, scopes=None):
        self.collections = {
            META_CREDENTIALS_COLLECTION: FakeCollection(
                [{
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "scope": list(scopes if scopes is not None else INSTAGRAM_REQUIRED_PERMISSIONS),
                }]
            ),
            META_ASSETS_COLLECTION: FakeCollection(
                [{
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "asset_type": "instagram_account",
                    "connection_status": "connected",
                    "external_asset_id": RAW_INSTAGRAM_ID,
                    "display_name": "amasi_store",
                    "page_id": PAGE_ID,
                }]
            ),
            "salla_integrations": FakeCollection(
                [{"user_id": OWNER_ID, "status": "connected", "store_id": "1014726301562776"}]
            ),
            CHANNELS_COLLECTION: FakeCollection(),
        }

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture(autouse=True)
def _binding_key(monkeypatch):
    monkeypatch.setenv("MEZAN_CHANNEL_BINDING_HMAC_KEY", "k" * 64)
    monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", Fernet.generate_key().decode())


@pytest.mark.asyncio
async def test_setup_exposes_opaque_candidate_and_provisions_receive_only_binding():
    db = FakeDB()
    subscriber = RecordingSubscriber()
    service = InstagramProvisioningService(
        db, now=lambda: NOW, webhook_subscriber=subscriber
    )

    setup = await service.setup(owner_user_id=OWNER_ID)
    result = await service.provision(
        owner_user_id=OWNER_ID,
        request=InstagramProvisionIn(
            candidate_ref=setup.candidates[0].candidate_ref,
            confirmation=INSTAGRAM_PROVISION_CONFIRMATION,
        ),
    )

    assert setup.state == "ready"
    assert setup.required_permissions_ready is True
    assert RAW_INSTAGRAM_ID not in setup.model_dump_json()
    assert result.status == "connected"
    assert result.send_allowed is False
    assert result.comment_reply_allowed is False
    assert subscriber.calls == [{
        "owner_user_id": OWNER_ID,
        "instagram_account_id": RAW_INSTAGRAM_ID,
        "page_id": PAGE_ID,
    }]
    stored = db.collections[CHANNELS_COLLECTION].rows[0]
    assert stored["provider"] == "instagram"
    assert stored["status"] == "connected"
    assert stored["ingress_enabled"] is True
    assert stored["egress_mode"] == "disabled"
    assert stored["send_allowed"] is False
    assert stored["ai_auto_reply_allowed"] is False
    assert stored["webhook_subscription_status"] == "confirmed"
    assert stored["webhook_subscription_checked_at"] == NOW
    assert RAW_INSTAGRAM_ID not in repr(stored)

    connected = await service.setup(owner_user_id=OWNER_ID)
    assert connected.state == "connected"


@pytest.mark.asyncio
async def test_existing_local_binding_is_not_connected_until_meta_subscription_is_repaired():
    db = FakeDB()
    initial_subscriber = RecordingSubscriber()
    service = InstagramProvisioningService(
        db, now=lambda: NOW, webhook_subscriber=initial_subscriber
    )
    setup = await service.setup(owner_user_id=OWNER_ID)
    await service.provision(
        owner_user_id=OWNER_ID,
        request=InstagramProvisionIn(
            candidate_ref=setup.candidates[0].candidate_ref,
            confirmation=INSTAGRAM_PROVISION_CONFIRMATION,
        ),
    )
    stored = db.collections[CHANNELS_COLLECTION].rows[0]
    stored.pop("webhook_subscription_status")
    stored.pop("webhook_subscription_checked_at")

    repair_subscriber = RecordingSubscriber()
    repair_service = InstagramProvisioningService(
        db, now=lambda: NOW, webhook_subscriber=repair_subscriber
    )
    before = await repair_service.setup(owner_user_id=OWNER_ID)
    repaired = await repair_service.provision(
        owner_user_id=OWNER_ID,
        request=InstagramProvisionIn(
            candidate_ref=before.candidates[0].candidate_ref,
            confirmation=INSTAGRAM_PROVISION_CONFIRMATION,
        ),
    )

    assert before.state == "ready"
    assert repaired.status == "connected"
    assert repair_subscriber.calls[0]["page_id"] == PAGE_ID
    assert stored["webhook_subscription_status"] == "confirmed"
    assert (await repair_service.setup(owner_user_id=OWNER_ID)).state == "connected"


@pytest.mark.asyncio
async def test_provider_subscription_failure_does_not_create_local_connected_binding():
    db = FakeDB()
    service = InstagramProvisioningService(
        db,
        now=lambda: NOW,
        webhook_subscriber=RecordingSubscriber(
            error_code="instagram_webhook_subscription_failed"
        ),
    )
    setup = await service.setup(owner_user_id=OWNER_ID)

    with pytest.raises(InstagramProvisioningError) as failure:
        await service.provision(
            owner_user_id=OWNER_ID,
            request=InstagramProvisionIn(
                candidate_ref=setup.candidates[0].candidate_ref,
                confirmation=INSTAGRAM_PROVISION_CONFIRMATION,
            ),
        )

    assert str(failure.value) == "instagram_webhook_subscription_failed"
    assert db.collections[CHANNELS_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_meta_subscription_uses_transient_page_token_and_verifies_app(monkeypatch):
    db = FakeDB()
    token_key = Fernet.generate_key()
    monkeypatch.setenv("META_TOKEN_ENC_KEY", token_key.decode())
    monkeypatch.setenv("META_BUSINESS_APP_ID", "953625110827548")
    monkeypatch.setenv("META_BUSINESS_APP_SECRET", "provider-secret")
    user_token = "encrypted-user-token"
    page_token = "transient-page-token"
    db.collections[META_CREDENTIALS_COLLECTION].rows[0][
        "access_token_ciphertext"
    ] = Fernet(token_key).encrypt(user_token.encode())
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
                        "instagram_business_account": {"id": RAW_INSTAGRAM_ID},
                    }]
                },
            )
        if request.method == "POST":
            assert request.url.params["subscribed_fields"] == "messages"
            assert request.url.params["access_token"] == page_token
            return Response(200, json={"success": True})
        return Response(
            200,
            json={
                "data": [{
                    "id": "953625110827548",
                    "subscribed_fields": ["messages"],
                }]
            },
        )

    async with AsyncClient(transport=MockTransport(handler)) as client:
        fields = await subscribe_instagram_webhooks(
            db,
            owner_user_id=OWNER_ID,
            instagram_account_id=RAW_INSTAGRAM_ID,
            page_id=PAGE_ID,
            client=client,
        )

    assert fields == ("comments", "messages")
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    assert requests[1].url.path.endswith(
        f"/{PAGE_ID}/subscribed_apps"
    )


@pytest.mark.asyncio
async def test_missing_new_meta_permissions_requires_reauthorization_without_write():
    db = FakeDB(scopes={"instagram_basic"})
    service = InstagramProvisioningService(db, now=lambda: NOW)

    setup = await service.setup(owner_user_id=OWNER_ID)

    assert setup.state == "meta_reauthorization_required"
    assert setup.required_permissions_ready is False
    assert db.collections[CHANNELS_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_owner_route_is_confirmed_and_has_no_instagram_send_operation():
    db = FakeDB()
    service = InstagramProvisioningService(
        db, now=lambda: NOW, webhook_subscriber=RecordingSubscriber()
    )

    async def current_user():
        return {"id": OWNER_ID, "role": "owner"}

    app = FastAPI()
    app.include_router(
        make_customer_intelligence_router(
            current_user,
            db=db,
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
        invalid = await client.post(
            "/api/customer-intelligence/v1/channels/instagram/setup",
            json={"candidate_ref": candidate_ref, "confirmation": "CONNECT"},
        )
        connected = await client.post(
            "/api/customer-intelligence/v1/channels/instagram/setup",
            json={
                "candidate_ref": candidate_ref,
                "confirmation": INSTAGRAM_PROVISION_CONFIRMATION,
            },
        )
        send = await client.post(
            "/api/customer-intelligence/v1/channels/instagram/send",
            json={"text": "must never leave Mezan"},
        )

    assert setup.status_code == 200
    assert RAW_INSTAGRAM_ID not in setup.text
    assert invalid.status_code == 422
    assert connected.status_code == 201
    assert connected.json()["send_allowed"] is False
    assert send.status_code in {404, 405}
