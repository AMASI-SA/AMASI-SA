"""Owner-confirmed Instagram binding from secret-safe Meta discovery assets."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo.errors import DuplicateKeyError

from customer_intelligence.foundation import CHANNELS_COLLECTION
from customer_intelligence.instagram_provisioning import (
    INSTAGRAM_PROVISION_CONFIRMATION,
    INSTAGRAM_REQUIRED_PERMISSIONS,
    META_ASSETS_COLLECTION,
    META_CREDENTIALS_COLLECTION,
    InstagramProvisionIn,
    InstagramProvisioningService,
)
from customer_intelligence.routes import make_customer_intelligence_router


NOW = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
OWNER_ID = "owner-instagram-setup"
RAW_INSTAGRAM_ID = "17841400000000001"


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
    service = InstagramProvisioningService(db, now=lambda: NOW)

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
    stored = db.collections[CHANNELS_COLLECTION].rows[0]
    assert stored["provider"] == "instagram"
    assert stored["status"] == "connected"
    assert stored["ingress_enabled"] is True
    assert stored["egress_mode"] == "disabled"
    assert stored["send_allowed"] is False
    assert stored["ai_auto_reply_allowed"] is False
    assert RAW_INSTAGRAM_ID not in repr(stored)


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

    async def current_user():
        return {"id": OWNER_ID, "role": "owner"}

    app = FastAPI()
    app.include_router(
        make_customer_intelligence_router(current_user, db=db),
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
