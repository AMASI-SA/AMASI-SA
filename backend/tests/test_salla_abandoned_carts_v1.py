from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from salla_integration import abandoned_carts as module
from salla_integration import webhook_event_capture as webhook_capture
from salla_integration.webhook_event_capture import _sanitize
from salla_integration.webhook_monitor_routes import APPROVED_EVENTS


def _matches(document, selector):
    for key, expected in selector.items():
        actual = document.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = deepcopy(documents or [])

    async def find_one(self, selector, projection=None):
        document = next(
            (item for item in self.documents if _matches(item, selector)),
            None,
        )
        if document is None:
            return None
        if not projection:
            return deepcopy(document)
        return {
            key: deepcopy(value)
            for key, value in document.items()
            if projection.get(key) and key != "_id"
        }

    async def update_one(self, selector, update, upsert=False):
        document = next(
            (item for item in self.documents if _matches(item, selector)),
            None,
        )
        inserted = document is None
        if inserted:
            if not upsert:
                return SimpleNamespace(upserted_id=None)
            document = deepcopy(selector)
            self.documents.append(document)
            for key, value in update.get("$setOnInsert", {}).items():
                document[key] = deepcopy(value)
        for key, value in update.get("$set", {}).items():
            document[key] = deepcopy(value)
        for key, value in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value
        for key, value in update.get("$addToSet", {}).items():
            values = document.setdefault(key, [])
            if value not in values:
                values.append(deepcopy(value))
        return SimpleNamespace(upserted_id="created" if inserted else None)

    async def create_index(self, *args, **kwargs):
        return kwargs.get("name")


class FakeDB:
    def __init__(self, integrations=None):
        self.collections = {
            "salla_integrations": FakeCollection(integrations),
            module.ABANDONED_CART_COLLECTION: FakeCollection(),
            module.ABANDONED_CART_EVENT_COLLECTION: FakeCollection(),
        }

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


def _cart_event(*, event="abandoned.cart", updated_at="2026-08-09T10:00:00Z"):
    return {
        "event": event,
        "merchant": 123,
        "data": {
            "id": "cart-1",
            "status": "active",
            "created_at": "2026-08-09T09:00:00Z",
            "updated_at": updated_at,
            "total": {"amount": 125.5, "currency": "SAR"},
            "customer": {
                "name": "Private Buyer",
                "email": "buyer@example.test",
                "phone": "+966500000000",
                "address": "Private street",
            },
            "metadata": {
                "utm_source": "snapchat",
                "campaign_id": "campaign-1",
                "sc_click_id": "click-1",
                "email": "nested@example.test",
            },
            "items": [
                {
                    "product_id": 88,
                    "sku": "SKU-88",
                    "name": "Product 88",
                    "quantity": 2,
                    "price": {"amount": 62.75, "currency": "SAR"},
                }
            ],
        },
    }


def test_normalizer_keeps_analytics_fields_without_customer_pii():
    record = module.normalize_abandoned_cart_event(_cart_event())

    assert record is not None
    assert record["cart_id"] == "cart-1"
    assert record["total"] == 125.5
    assert record["items"][0]["product_id"] == "88"
    assert record["attribution"] == {
        "utm_source": "snapchat",
        "campaign_id": "campaign-1",
        "click_id": "click-1",
    }
    assert record["pii_stored"] is False
    serialized = repr(record)
    assert "Private Buyer" not in serialized
    assert "buyer@example.test" not in serialized
    assert "+966500000000" not in serialized
    assert "Private street" not in serialized
    assert "nested@example.test" not in serialized


def test_cart_token_is_never_used_as_persisted_identity():
    payload = _cart_event()
    payload["data"].pop("id")
    payload["data"]["token"] = "private-cart-token"

    assert module.normalize_abandoned_cart_event(payload) is None


def test_all_four_salla_cart_events_are_ingested_and_monitored():
    expected = {
        "abandoned.cart",
        "abandoned.cart.updated",
        "abandoned.cart.status.changed",
        "abandoned.cart.purchased",
    }
    monitored = {
        name
        for name, _label, group in APPROVED_EVENTS
        if group == "abandoned_carts"
    }

    assert module.ABANDONED_CART_EVENTS == expected
    assert monitored == expected
    assert module.MAX_BACKFILL_PAGES >= 300


def test_raw_cart_audit_redacts_customer_and_loose_pii_fields():
    payload = _cart_event()
    payload["data"]["checkout"] = {
        "name": "Loose Buyer Name",
        "city": "Riyadh",
        "ip_address": "192.0.2.1",
    }
    payload["data"]["urls"] = {
        "checkout": "https://example.test/private-recovery-token",
    }

    sanitized = _sanitize(payload, redact_pii=True)
    serialized = repr(sanitized)
    assert "Private Buyer" not in serialized
    assert "Loose Buyer Name" not in serialized
    assert "buyer@example.test" not in serialized
    assert "192.0.2.1" not in serialized
    assert "private-recovery-token" not in serialized
    assert sanitized["data"]["customer"] == "[REDACTED_PII]"
    assert sanitized["data"]["urls"] == "[REDACTED_PII]"


@pytest.mark.asyncio
async def test_cart_without_merchant_identity_is_not_persisted():
    db = FakeDB()
    event = _cart_event()
    event.pop("merchant")

    result = await module.persist_abandoned_cart_event(
        db,
        event,
        user_id="owner-1",
    )

    assert result["reason"] == "merchant_id_missing"
    assert db.collections[module.ABANDONED_CART_COLLECTION].documents == []
    assert db.collections[module.ABANDONED_CART_EVENT_COLLECTION].documents == []


@pytest.mark.asyncio
async def test_cart_without_tenant_owner_is_not_persisted():
    db = FakeDB()

    result = await module.persist_abandoned_cart_event(db, _cart_event())

    assert result["reason"] == "owner_not_found"
    assert db.collections[module.ABANDONED_CART_COLLECTION].documents == []
    assert db.collections[module.ABANDONED_CART_EVENT_COLLECTION].documents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("event_name", sorted(module.ABANDONED_CART_EVENTS))
async def test_cart_events_never_enter_order_or_shipment_paths(
    monkeypatch,
    event_name,
):
    db = FakeDB(
        [
            {
                "user_id": "owner-1",
                "store_id": 123,
                "scope": "orders.read_write carts.read",
            }
        ]
    )
    forbidden_calls = []

    async def forbidden(*args, **kwargs):
        forbidden_calls.append((args, kwargs))
        raise AssertionError("cart event reached an order/shipment path")

    monkeypatch.setattr(
        webhook_capture,
        "sync_order_from_verified_webhook",
        forbidden,
    )
    monkeypatch.setattr(
        webhook_capture,
        "sync_shipment_payload_from_verified_webhook",
        forbidden,
    )

    result = await webhook_capture.capture_unknown_event(
        db,
        _cart_event(event=event_name),
        known_events=(),
    )

    assert forbidden_calls == []
    assert result["abandoned_cart_isolated"] is True
    assert result["abandoned_cart_sync"]["synced"] is True
    assert result["order_sync"]["reason"] == "isolated_abandoned_cart_event"
    assert result["shipment_sync"]["reason"] == "isolated_abandoned_cart_event"
    assert result["snapchat_capi"]["reason"] == "isolated_abandoned_cart_event"
    assert result["order_mutation_scope"] == "none"


@pytest.mark.asyncio
async def test_backfill_stops_before_provider_call_without_carts_scope():
    db = FakeDB([{"user_id": "owner-1", "store_id": 123, "scope": "orders.read_write"}])
    calls = []

    async def provider(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    with pytest.raises(module.AbandonedCartScopeError):
        await module.backfill_abandoned_carts(
            db,
            "owner-1",
            call_provider=provider,
        )

    assert calls == []


@pytest.mark.asyncio
async def test_backfill_reads_pages_sequentially_and_marks_api_provenance():
    db = FakeDB(
        [
            {
                "user_id": "owner-1",
                "store_id": 123,
                "scope": "orders.read_write carts.read",
            }
        ]
    )
    pages = []

    async def provider(db_arg, user_id, method, path, *, params):
        assert db_arg is db
        assert user_id == "owner-1"
        assert method == "GET"
        assert path == "/carts/abandoned"
        pages.append(params["page"])
        rows = [
            {
                **_cart_event(updated_at=f"2026-08-09T10:0{params['page']}:00Z")[
                    "data"
                ],
                "id": f"cart-{params['page']}",
            }
        ]
        return {
            "data": rows,
            "pagination": {"totalPages": 2},
        }

    progress = []

    async def capture_progress(value):
        progress.append(deepcopy(value))

    result = await module.backfill_abandoned_carts(
        db,
        "owner-1",
        call_provider=provider,
        per_page=1,
        progress_hook=capture_progress,
    )

    assert pages == [1, 2]
    assert result["rows_saved"] == 2
    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["errors_count"] == 0
    assert progress[-1] == {
        "pages_fetched": 2,
        "rows_seen": 2,
        "rows_saved": 2,
        "created": 2,
        "updated": 0,
        "errors_count": 0,
    }
    assert result["provider_write_reached"] is False
    snapshots = db.collections[module.ABANDONED_CART_COLLECTION].documents
    assert {row["source"] for row in snapshots} == {"salla_abandoned_carts_api"}
    assert all(row["pii_stored"] is False for row in snapshots)

    rerun = await module.backfill_abandoned_carts(
        db,
        "owner-1",
        call_provider=provider,
        per_page=1,
    )
    assert rerun["created"] == 0
    assert rerun["updated"] == 2
    assert rerun["rows_saved"] == 2


@pytest.mark.asyncio
async def test_backfill_retries_transient_provider_throttling(monkeypatch):
    db = FakeDB(
        [
            {
                "user_id": "owner-1",
                "store_id": 123,
                "scope": "orders.read_write carts.read",
            }
        ]
    )
    calls = []
    delays = []

    class Throttled(RuntimeError):
        status_code = 429

    async def provider(*args, **kwargs):
        calls.append(kwargs["params"]["page"])
        if len(calls) == 1:
            raise Throttled("retry later")
        return {"data": [], "pagination": {"totalPages": 1}}

    async def no_wait(seconds):
        delays.append(seconds)

    monkeypatch.setattr(module.asyncio, "sleep", no_wait)

    result = await module.backfill_abandoned_carts(
        db,
        "owner-1",
        call_provider=provider,
    )

    assert calls == [1, 1]
    assert delays == [1]
    assert result["pages_fetched"] == 1
    assert result["stopped_reason"] == "pagination_complete"


@pytest.mark.asyncio
async def test_purchased_state_cannot_be_downgraded_by_later_or_duplicate_events():
    db = FakeDB()
    purchased = _cart_event(
        event="abandoned.cart.purchased",
        updated_at="2026-08-09T10:00:00Z",
    )
    purchased["data"]["order_id"] = "order-1"
    await module.persist_abandoned_cart_event(db, purchased, user_id="owner-1")

    later_abandoned = _cart_event(updated_at="2026-08-09T11:00:00Z")
    result = await module.persist_abandoned_cart_event(
        db,
        later_abandoned,
        user_id="owner-1",
    )

    snapshot = db.collections[module.ABANDONED_CART_COLLECTION].documents[0]
    assert snapshot["purchased"] is True
    assert snapshot["status"] == "purchased"
    assert snapshot["order_id"] == "order-1"
    assert result["ignored_after_purchase"] is True
    assert result["ignored_out_of_order"] is False
