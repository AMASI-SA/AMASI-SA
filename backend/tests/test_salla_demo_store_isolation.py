from __future__ import annotations

from types import SimpleNamespace

import pytest

from salla_integration.easy_mode_webhook import (
    _handle_app_uninstalled,
    _handle_store_authorize,
)
from salla_integration.shipment_webhook_sync import (
    sync_shipment_from_verified_webhook,
)
from salla_integration.webhook_order_sync import (
    _resolve_user_id,
    sync_order_from_verified_webhook,
    sync_shipment_payload_from_verified_webhook,
)


PILOT_STORE_ID = "748155538"


@pytest.fixture(autouse=True)
def pilot_store_env(monkeypatch):
    monkeypatch.setenv("SALLA_ATTRIBUTION_PILOT_STORE_ID", PILOT_STORE_ID)


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


@pytest.mark.asyncio
async def test_unknown_store_never_falls_back_to_amasi_owner():
    db = SimpleNamespace(
        salla_integrations=IntegrationCollection([
            {"store_id": "amasi-store", "user_id": "amasi-owner"},
        ])
    )
    assert await _resolve_user_id(db, "unknown-store") is None


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
