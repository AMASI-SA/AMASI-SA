from __future__ import annotations

from types import SimpleNamespace

import pytest

from salla_integration import easy_mode_webhook
from salla_integration.easy_mode_webhook import (
    _handle_app_uninstalled,
    _handle_store_authorize,
)
from salla_integration.shipment_webhook_sync import (
    sync_shipment_from_verified_webhook,
)
from salla_integration.store_scope import is_attribution_pilot_store
from salla_integration.webhook_order_sync import (
    _resolve_user_id,
    sync_order_from_verified_webhook,
    sync_shipment_payload_from_verified_webhook,
)


PILOT_STORE_ID = "748155538"


@pytest.fixture(autouse=True)
def pilot_store_env(monkeypatch):
    monkeypatch.setenv("SALLA_ATTRIBUTION_PILOT_STORE_ID", PILOT_STORE_ID)


def test_verified_demo_store_is_pilot_without_deployment_configuration(monkeypatch):
    monkeypatch.delenv("SALLA_ATTRIBUTION_PILOT_STORE_ID", raising=False)
    assert is_attribution_pilot_store("748155538")


def test_explicit_additional_pilot_cannot_unprotect_the_verified_demo(monkeypatch):
    monkeypatch.setenv("SALLA_ATTRIBUTION_PILOT_STORE_ID", "9999000111")
    assert is_attribution_pilot_store("9999000111")
    assert is_attribution_pilot_store(PILOT_STORE_ID)


class NeverTouchedDB:
    def __getattr__(self, name):
        raise AssertionError(f"database must not be touched: {name}")


class IntegrationCollection:
    def __init__(self, rows):
        self.rows = rows

    async def find_one(self, query, projection=None, **kwargs):
        values = query.get("store_id", {}).get("$in", [])
        for row in self.rows:
            if row.get("store_id") in values:
                return {"user_id": row.get("user_id")}
        return None


class BoundIntegrationCollection:
    def __init__(self, store_id):
        self.store_id = store_id
        self.updated = False

    async def find_one(self, query, projection=None, **kwargs):
        return {"store_id": self.store_id}

    async def update_one(self, query, update):
        self.updated = True
        return SimpleNamespace(matched_count=0)


@pytest.mark.asyncio
async def test_unknown_store_never_falls_back_to_amasi_owner():
    db = SimpleNamespace(
        salla_integrations=IntegrationCollection([
            {"store_id": "amasi-store", "user_id": "amasi-owner"},
        ])
    )
    assert await _resolve_user_id(db, "unknown-store") is None


@pytest.mark.asyncio
async def test_foreign_easy_mode_authorization_cannot_replace_connected_store(
    monkeypatch,
):
    monkeypatch.setenv("SALLA_ATTRIBUTION_PILOT_STORE_ID", "8888000111")
    monkeypatch.setattr(
        easy_mode_webhook,
        "resolve_owner_user_id",
        lambda db: _owner("amasi-owner", "owner@example.test"),
    )
    collection = BoundIntegrationCollection("amasi-production-store")
    db = SimpleNamespace(salla_integrations=collection)
    result = await _handle_store_authorize(
        db,
        {
            "event": "app.store.authorize",
            "merchant": "9999000111",
            "data": {"access_token": "foreign-token"},
        },
    )
    assert result["stored"] is False
    assert result["reason"] == "different_store_authorization_ignored"
    assert result["current_store_id"] == "amasi-production-store"


async def _owner(user_id, email):
    return user_id, email


@pytest.mark.asyncio
async def test_pilot_authorize_and_uninstall_never_mutate_amasi_integration():
    authorize = await _handle_store_authorize(
        NeverTouchedDB(),
        {
            "event": "app.store.authorize",
            "merchant": PILOT_STORE_ID,
            "data": {"access_token": "pilot-access-token"},
        },
    )
    uninstall = await _handle_app_uninstalled(
        NeverTouchedDB(),
        {"event": "app.uninstalled", "merchant": PILOT_STORE_ID},
    )
    assert authorize == {
        "ok": True,
        "stored": False,
        "reason": "attribution_pilot_store_authorization_ignored",
        "merchant_id": PILOT_STORE_ID,
    }
    assert uninstall == {
        "ok": True,
        "stored": False,
        "reason": "attribution_pilot_store_uninstall_ignored",
        "merchant_id": PILOT_STORE_ID,
    }


@pytest.mark.asyncio
async def test_pilot_webhooks_cannot_create_or_enrich_orders_or_shipments():
    order_event = {
        "event": "order.created",
        "merchant": PILOT_STORE_ID,
        "data": {"reference_id": "DEMO-ORDER-1"},
    }
    shipment_event = {
        "event": "shipment.created",
        "merchant": PILOT_STORE_ID,
        "data": {"id": "123", "order_reference_id": "DEMO-ORDER-1"},
    }

    order = await sync_order_from_verified_webhook(NeverTouchedDB(), order_event)
    payload_shipment = await sync_shipment_payload_from_verified_webhook(
        NeverTouchedDB(), shipment_event
    )
    api_shipment = await sync_shipment_from_verified_webhook(
        NeverTouchedDB(), shipment_event
    )
    assert order["reason"] == "attribution_pilot_store_orders_blocked"
    assert payload_shipment["reason"] == "attribution_pilot_store_shipments_blocked"
    assert api_shipment["reason"] == "attribution_pilot_store_shipments_blocked"
