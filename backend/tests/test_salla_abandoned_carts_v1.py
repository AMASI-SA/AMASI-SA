from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
import httpx
from cryptography.fernet import Fernet

import customer_identity
from salla_integration import abandoned_carts as module
from salla_integration import routes as routes_module
from salla_integration import webhook_event_capture as webhook_capture
from salla_integration.webhook_event_capture import _sanitize
from salla_integration.webhook_monitor_routes import APPROVED_EVENTS


def _get_path(document, path):
    value = document
    for part in str(path).split("."):
        if not isinstance(value, dict) or part not in value:
            return None, False
        value = value[part]
    return value, True


def _matches(document, selector):
    for key, expected in selector.items():
        if key == "$or":
            if not any(_matches(document, part) for part in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches(document, part) for part in expected):
                return False
            continue
        actual, exists = _get_path(document, key)
        if isinstance(expected, dict):
            if "$in" in expected:
                if isinstance(actual, list):
                    if not set(actual).intersection(expected["$in"]):
                        return False
                elif actual not in expected["$in"]:
                    return False
            if "$exists" in expected and bool(expected["$exists"]) != exists:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
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
        included = [
            key for key, enabled in projection.items() if key != "_id" and enabled
        ]
        if not included:
            return {
                key: deepcopy(value)
                for key, value in document.items()
                if key != "_id"
            }
        return {
            key: deepcopy(value)
            for key, value in document.items()
            if projection.get(key) and key != "_id"
        }

    async def update_one(self, selector, update, upsert=False):
        conflicting_paths = set(update.get("$set", {})).intersection(
            update.get("$setOnInsert", {})
        )
        assert not conflicting_paths, (
            "MongoDB rejects paths shared by $set and $setOnInsert: "
            f"{sorted(conflicting_paths)}"
        )
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
            candidates = value.get("$each", []) if isinstance(value, dict) else [value]
            for candidate in candidates:
                if candidate not in values:
                    values.append(deepcopy(candidate))
        return SimpleNamespace(upserted_id="created" if inserted else None)

    async def update_many(self, selector, update):
        modified = 0
        for document in self.documents:
            if not _matches(document, selector):
                continue
            before = deepcopy(document)
            for key, value in update.get("$set", {}).items():
                document[key] = deepcopy(value)
            for key, value in update.get("$addToSet", {}).items():
                values = document.setdefault(key, [])
                candidates = value.get("$each", []) if isinstance(value, dict) else [value]
                for candidate in candidates:
                    if candidate not in values:
                        values.append(deepcopy(candidate))
            if document != before:
                modified += 1
        return SimpleNamespace(modified_count=modified)

    def find(self, selector, projection=None):
        documents = [
            deepcopy(item) for item in self.documents if _matches(item, selector)
        ]
        if projection:
            included = [
                key for key, enabled in projection.items() if key != "_id" and enabled
            ]
            if included:
                documents = [
                    {
                        key: deepcopy(document[key])
                        for key in included
                        if key in document
                    }
                    for document in documents
                ]
        return FakeCursor(documents)

    async def create_index(self, *args, **kwargs):
        return kwargs.get("name")


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    async def to_list(self, length):
        return deepcopy(self.documents[:length])


class FakeDB:
    def __init__(self, integrations=None):
        self.collections = {
            "salla_integrations": FakeCollection(integrations),
            module.ABANDONED_CART_COLLECTION: FakeCollection(),
            module.ABANDONED_CART_EVENT_COLLECTION: FakeCollection(),
            customer_identity.CUSTOMER_IDENTITY_COLLECTION: FakeCollection(),
            "unified_orders": FakeCollection(),
        }

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture(autouse=True)
def _customer_encryption_key(monkeypatch):
    monkeypatch.setenv(
        "MEZAN_CUSTOMER_PII_ENC_KEY",
        Fernet.generate_key().decode("utf-8"),
    )
    monkeypatch.delenv("MEZAN_CUSTOMER_PII_ENC_KEY_OLD", raising=False)
    monkeypatch.delenv("MEZAN_CUSTOMER_IDENTITY_HMAC_KEY", raising=False)
    customer_identity._fernet = None
    yield
    customer_identity._fernet = None


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
                "ad_account_id": "account-1",
                "ad_squad_id": "ad-group-1",
                "ad_id": "ad-1",
                "creative_id": "creative-1",
                "sc_click_id": "click-1",
                "email": "nested@example.test",
            },
            "items": [
                {
                    "product_id": 88,
                    "sku": "SKU-88",
                    "name": "Product 88",
                    "image_url": "https://cdn.example.test/products/88.png",
                    "quantity": 2,
                    "price": {"amount": 62.75, "currency": "SAR"},
                    "options": [
                        {
                            "option_id": "size",
                            "name": "المقاس",
                            "value_id": "12",
                            "value": "12 سنة",
                        }
                    ],
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_live_only_mode_closes_running_historical_import_without_deleting_data():
    db = FakeDB()
    db.salla_sync_logs.documents.extend(
        [
            {
                "id": "stuck-run",
                "user_id": "owner-1",
                "kind": "abandoned_carts",
                "status": "running",
                "rows_saved": 1920,
            },
            {
                "id": "orders-run",
                "user_id": "owner-1",
                "kind": "orders",
                "status": "running",
            },
            {
                "id": "other-owner",
                "user_id": "owner-2",
                "kind": "abandoned_carts",
                "status": "running",
            },
        ]
    )
    db.salla_abandoned_carts_v1.documents.append(
        {"id": "saved-cart", "user_id": "owner-1"}
    )

    closed = await routes_module.close_running_historical_cart_imports(
        db, "owner-1"
    )

    assert closed == 1
    stuck = db.salla_sync_logs.documents[0]
    assert stuck["status"] == "cancelled"
    assert stuck["stopped_reason"] == (
        routes_module.HISTORICAL_ABANDONED_CART_STOP_REASON
    )
    assert db.salla_sync_logs.documents[1]["status"] == "running"
    assert db.salla_sync_logs.documents[2]["status"] == "running"
    assert db.salla_abandoned_carts_v1.documents == [
        {"id": "saved-cart", "user_id": "owner-1"}
    ]


def test_normalizer_keeps_analytics_fields_without_customer_pii():
    record = module.normalize_abandoned_cart_event(_cart_event())

    assert record is not None
    assert record["cart_id"] == "cart-1"
    assert record["total"] == 125.5
    assert record["items"][0]["product_id"] == "88"
    assert record["items"][0]["image_url"] == "https://cdn.example.test/products/88.png"
    assert record["attribution"] == {
        "platform": "snapchat",
        "account_id": "account-1",
        "utm_source": "snapchat",
        "campaign_id": "campaign-1",
        "ad_group_id": "ad-group-1",
        "ad_id": "ad-1",
        "creative_id": "creative-1",
        "click_id": "click-1",
    }
    assert record["schema_version"] == 2
    assert record["items"][0]["options"] == [
        {
            "option_id": "size",
            "name": "المقاس",
            "value_id": "12",
            "value": "12 سنة",
        }
    ]
    assert record["pii_stored"] is False
    serialized = repr(record)
    assert "Private Buyer" not in serialized
    assert "buyer@example.test" not in serialized
    assert "+966500000000" not in serialized
    assert "Private street" not in serialized
    assert "nested@example.test" not in serialized


def test_normalizer_accepts_official_salla_purchased_webhook_shape():
    payload = {
        "event": "abandoned.cart.purchased",
        "merchant": 935918575,
        "created_at": "Tue Mar 25 2025 11:59:37 GMT+0300",
        "data": {
            "id": 1305879817,
            "status": "purchased",
            "currency": "SAR",
            "total": 34.99,
            "subtotal": 30.43,
            "total_discount": 0,
        },
    }

    record = module.normalize_abandoned_cart_event(payload)

    assert record is not None
    assert record["cart_id"] == "1305879817"
    assert record["purchased"] is True
    assert record["status"] == "purchased"
    assert record["currency"] == "SAR"
    assert record["total"] == 34.99
    assert record["cart_updated_at"] == "2025-03-25T08:59:37+00:00"


def test_normalizer_accepts_unix_millisecond_cart_timestamps():
    payload = _cart_event(updated_at=1786626000000)
    payload["data"]["created_at"] = 1786622400

    record = module.normalize_abandoned_cart_event(payload)

    assert record is not None
    assert record["cart_created_at"] == "2026-08-13T12:00:00+00:00"
    assert record["cart_updated_at"] == "2026-08-13T13:00:00+00:00"


def test_cart_token_is_never_used_as_persisted_identity():
    payload = _cart_event()
    payload["data"].pop("id")
    payload["data"]["token"] = "private-cart-token"

    assert module.normalize_abandoned_cart_event(payload) is None


def test_customer_contact_aliases_can_link_future_channels_without_plaintext():
    salla_keys = customer_identity.build_identity_keys(
        user_id="owner-1",
        merchant_id="123",
        source_system="salla",
        external_customer_id="salla-customer-1",
        mobile="050 000 0000",
    )
    whatsapp_keys = customer_identity.build_identity_keys(
        user_id="owner-1",
        merchant_id="123",
        source_system="whatsapp",
        external_customer_id="whatsapp-contact-1",
        mobile="966500000000",
    )

    assert salla_keys[0] != whatsapp_keys[0]
    assert salla_keys[1] == whatsapp_keys[1]
    assert "966500000000" not in repr(salla_keys + whatsapp_keys)


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
async def test_v2_fails_closed_when_private_encryption_key_is_missing(monkeypatch):
    db = FakeDB()
    monkeypatch.delenv("MEZAN_CUSTOMER_PII_ENC_KEY", raising=False)
    monkeypatch.delenv("SALLA_TOKEN_ENC_KEY", raising=False)
    customer_identity._fernet = None

    with pytest.raises(RuntimeError, match="must be configured"):
        await module.persist_abandoned_cart_event(
            db,
            _cart_event(),
            user_id="owner-1",
        )

    assert db.collections[module.ABANDONED_CART_COLLECTION].documents == []
    assert db.collections[module.ABANDONED_CART_EVENT_COLLECTION].documents == []
    assert db.collections[
        customer_identity.CUSTOMER_IDENTITY_COLLECTION
    ].documents == []


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
        "identity_linked": 2,
        "attributed": 2,
        "order_linked": 0,
        "private_context_encrypted": 0,
        "customer_orders_linked": 0,
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
async def test_backfill_retries_transport_failure(monkeypatch):
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

    async def provider(*args, **kwargs):
        calls.append(kwargs["params"]["page"])
        if len(calls) == 1:
            raise httpx.ReadTimeout("temporary Salla read timeout")
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


@pytest.mark.asyncio
async def test_reconcile_purchased_cart_flags_repairs_legacy_snapshot_once():
    db = FakeDB()
    carts = db.collections[module.ABANDONED_CART_COLLECTION]
    carts.documents.extend(
        [
            {
                "user_id": "owner-1",
                "cart_id": "purchased-cart",
                "purchased": False,
                "status": "active",
                "source_events": ["abandoned.cart.purchased"],
            },
            {
                "user_id": "owner-1",
                "cart_id": "active-cart",
                "purchased": False,
                "status": "active",
                "source_events": ["abandoned.cart"],
            },
            {
                "user_id": "other-owner",
                "cart_id": "other-tenant-cart",
                "purchased": False,
                "status": "active",
                "source_events": ["abandoned.cart.purchased"],
            },
        ]
    )

    repaired = await module.reconcile_purchased_cart_flags(db, "owner-1")
    rerun = await module.reconcile_purchased_cart_flags(db, "owner-1")

    assert repaired == 1
    assert rerun == 0
    purchased = next(
        row for row in carts.documents if row["cart_id"] == "purchased-cart"
    )
    active = next(row for row in carts.documents if row["cart_id"] == "active-cart")
    other = next(
        row for row in carts.documents if row["cart_id"] == "other-tenant-cart"
    )
    assert purchased["purchased"] is True
    assert purchased["status"] == "purchased"
    assert purchased["purchase_state_source"] == "abandoned.cart.purchased"
    assert purchased["purchase_reconciled_at"] is not None
    assert active["purchased"] is False
    assert other["purchased"] is False


@pytest.mark.asyncio
async def test_reconcile_purchased_cart_flags_uses_verified_legacy_capture():
    db = FakeDB(integrations=[{"user_id": "owner-1", "store_id": 123}])
    carts = db.collections[module.ABANDONED_CART_COLLECTION]
    carts.documents.extend(
        [
            {
                "user_id": "owner-1",
                "merchant_id": "123",
                "cart_id": "captured-purchased",
                "purchased": False,
                "status": "active",
                "source_events": ["abandoned.cart"],
            },
            {
                "user_id": "owner-1",
                "merchant_id": "123",
                "cart_id": "unverified-capture",
                "purchased": False,
                "status": "active",
            },
            {
                "user_id": "other-owner",
                "merchant_id": "999",
                "cart_id": "other-merchant-capture",
                "purchased": False,
                "status": "active",
            },
        ]
    )
    captures = db.collections.setdefault(
        "salla_webhook_event_captures", FakeCollection()
    )
    captures.documents.extend(
        [
            {
                "merchant_id": "123",
                "event": "abandoned.cart.purchased",
                "verified_before_capture": True,
                "payload": {
                    "event": "abandoned.cart.purchased",
                    "merchant": 123,
                    "data": {"id": "captured-purchased", "status": "purchased"},
                },
            },
            {
                "merchant_id": "123",
                "event": "abandoned.cart.purchased",
                "verified_before_capture": False,
                "payload": {
                    "event": "abandoned.cart.purchased",
                    "merchant": 123,
                    "data": {"id": "unverified-capture", "status": "purchased"},
                },
            },
            {
                "merchant_id": "999",
                "event": "abandoned.cart.purchased",
                "verified_before_capture": True,
                "payload": {
                    "event": "abandoned.cart.purchased",
                    "merchant": 999,
                    "data": {
                        "id": "other-merchant-capture",
                        "status": "purchased",
                    },
                },
            },
        ]
    )

    repaired = await module.reconcile_purchased_cart_flags(db, "owner-1")
    rerun = await module.reconcile_purchased_cart_flags(db, "owner-1")

    assert repaired == 1
    assert rerun == 0
    captured = next(
        row for row in carts.documents if row["cart_id"] == "captured-purchased"
    )
    unverified = next(
        row for row in carts.documents if row["cart_id"] == "unverified-capture"
    )
    other = next(
        row for row in carts.documents if row["cart_id"] == "other-merchant-capture"
    )
    assert captured["purchased"] is True
    assert captured["status"] == "purchased"
    assert captured["source_events"] == [
        "abandoned.cart",
        "abandoned.cart.purchased",
    ]
    assert captured["purchase_state_source"] == (
        "verified_webhook_capture:abandoned.cart.purchased"
    )
    assert unverified["purchased"] is False
    assert other["purchased"] is False


@pytest.mark.asyncio
async def test_v2_encrypts_customer_and_cart_private_context_without_plaintext():
    db = FakeDB()
    event = _cart_event()
    event["data"]["customer"]["id"] = "salla-customer-1"
    event["data"]["customer"]["shipping_address"] = {
        "city": "Riyadh",
        "street": "Private street 22",
    }
    event["data"]["urls"] = {
        "checkout": "https://example.test/recover/private-token"
    }
    event["data"]["coupon"] = {"code": "WELCOME-PRIVATE"}

    result = await module.persist_abandoned_cart_event(
        db,
        event,
        user_id="owner-1",
    )

    assert result["customer_identity_linked"] is True
    assert result["private_context_encrypted"] is True
    snapshot = db.collections[module.ABANDONED_CART_COLLECTION].documents[0]
    identity = db.collections[
        customer_identity.CUSTOMER_IDENTITY_COLLECTION
    ].documents[0]
    rendered = repr({"snapshot": snapshot, "identity": identity})
    for private_value in (
        "Private Buyer",
        "buyer@example.test",
        "+966500000000",
        "Private street 22",
        "private-token",
        "WELCOME-PRIVATE",
    ):
        assert private_value not in rendered

    private_cart = customer_identity.decrypt_private_payload(
        snapshot["private_cart_ciphertext"]
    )
    private_profile = customer_identity.decrypt_private_payload(
        identity["private_profile_ciphertext"]
    )
    assert private_cart == {
        "coupon_code": "WELCOME-PRIVATE",
        "recovery_url": "https://example.test/recover/private-token",
    }
    assert private_profile["email"] == "buyer@example.test"
    assert private_profile["shipping_address"]["city"] == "Riyadh"
    assert snapshot["customer_identity_id"] == identity["customer_identity_id"]
    assert snapshot["plaintext_pii_stored"] is False


@pytest.mark.asyncio
async def test_v2_preserves_first_touch_and_updates_last_touch():
    db = FakeDB()
    await module.persist_abandoned_cart_event(db, _cart_event(), user_id="owner-1")

    later = _cart_event(updated_at="2026-08-09T11:00:00Z")
    later["data"]["metadata"].update(
        {
            "campaign_id": "campaign-2",
            "ad_squad_id": "ad-group-2",
            "ad_id": "ad-2",
            "creative_id": "creative-2",
        }
    )
    await module.persist_abandoned_cart_event(db, later, user_id="owner-1")

    snapshot = db.collections[module.ABANDONED_CART_COLLECTION].documents[0]
    assert snapshot["attribution_first_touch"]["campaign_id"] == "campaign-1"
    assert snapshot["attribution_last_touch"]["campaign_id"] == "campaign-2"
    assert snapshot["attribution"]["ad_group_id"] == "ad-group-2"
    assert snapshot["attribution"]["creative_id"] == "creative-2"


@pytest.mark.asyncio
async def test_v2_upgrades_v1_cart_without_conflicting_first_touch_operators():
    db = FakeDB()
    db.collections[module.ABANDONED_CART_COLLECTION].documents.append(
        {
            "merchant_id": "123",
            "cart_id": "cart-1",
            "schema_version": 1,
            "purchased": False,
            "cart_updated_at": "2026-08-09T09:00:00+00:00",
        }
    )

    result = await module.persist_abandoned_cart_event(
        db,
        _cart_event(),
        user_id="owner-1",
    )

    snapshot = db.collections[module.ABANDONED_CART_COLLECTION].documents[0]
    assert result["synced"] is True
    assert result["created"] is False
    assert snapshot["schema_version"] == 2
    assert snapshot["attribution_first_touch"]["campaign_id"] == "campaign-1"
    assert snapshot["attribution_last_touch"]["campaign_id"] == "campaign-1"


@pytest.mark.asyncio
async def test_v2_older_event_cannot_overwrite_newer_customer_profile():
    db = FakeDB()
    newer = _cart_event(updated_at="2026-08-09T11:00:00Z")
    newer["data"]["customer"]["name"] = "New Customer Name"
    older = _cart_event(updated_at="2026-08-09T10:00:00Z")
    older["data"]["customer"]["name"] = "Stale Customer Name"

    await module.persist_abandoned_cart_event(db, newer, user_id="owner-1")
    result = await module.persist_abandoned_cart_event(
        db,
        older,
        user_id="owner-1",
    )

    identity = db.collections[
        customer_identity.CUSTOMER_IDENTITY_COLLECTION
    ].documents[0]
    profile = customer_identity.decrypt_private_payload(
        identity["private_profile_ciphertext"]
    )
    assert result["ignored_out_of_order"] is True
    assert profile["name"] == "New Customer Name"
    assert "Stale Customer Name" not in repr(identity)


@pytest.mark.asyncio
async def test_v2_older_event_adds_missing_memory_without_replacing_newer_cart():
    db = FakeDB()
    db.collections[module.ABANDONED_CART_COLLECTION].documents.append(
        {
            "merchant_id": "123",
            "cart_id": "cart-1",
            "user_id": "owner-1",
            "schema_version": 1,
            "purchased": False,
            "status": "newer-status",
            "total": 999.0,
            "cart_updated_at": "2026-08-09T11:00:00+00:00",
        }
    )
    older = _cart_event(updated_at="2026-08-09T10:00:00Z")
    older["data"]["customer"]["id"] = "salla-customer-1"
    older["data"]["urls"] = {
        "checkout": "https://example.test/recover/private-token"
    }

    result = await module.persist_abandoned_cart_event(
        db,
        older,
        user_id="owner-1",
    )

    snapshot = db.collections[module.ABANDONED_CART_COLLECTION].documents[0]
    identity = db.collections[
        customer_identity.CUSTOMER_IDENTITY_COLLECTION
    ].documents[0]
    assert result["ignored_out_of_order"] is True
    assert snapshot["status"] == "newer-status"
    assert snapshot["total"] == 999.0
    assert snapshot["cart_updated_at"] == "2026-08-09T11:00:00+00:00"
    assert snapshot["schema_version"] == 2
    assert snapshot["customer_identity_id"] == identity["customer_identity_id"]
    assert snapshot["private_cart_context_encrypted"] is True
    assert snapshot["attribution"]["campaign_id"] == "campaign-1"
    assert identity["last_cart_id"] == "cart-1"
    assert "private-token" not in repr(snapshot)


@pytest.mark.asyncio
async def test_v2_links_the_customer_previous_orders_without_overwriting_history():
    db = FakeDB()
    for order_number in ("order-old-1", "order-old-2"):
        db.collections["unified_orders"].documents.append(
            {
                "user_id": "owner-1",
                "order_number": order_number,
                "raw_by_source": {
                    "salla_direct": {"customer": {"id": "salla-customer-1"}}
                },
            }
        )
    event = _cart_event()
    event["data"]["customer"]["id"] = "salla-customer-1"

    first = await module.persist_abandoned_cart_event(
        db,
        event,
        user_id="owner-1",
    )
    second = await module.persist_abandoned_cart_event(
        db,
        event,
        user_id="owner-1",
    )

    assert first["customer_orders_linked"] == 2
    assert second["customer_orders_linked"] == 0
    linked_ids = {
        row.get("customer_identity_id")
        for row in db.collections["unified_orders"].documents
    }
    customer_identity_id = db.collections[
        module.ABANDONED_CART_COLLECTION
    ].documents[0]["customer_identity_id"]
    assert linked_ids == {customer_identity_id}


@pytest.mark.asyncio
async def test_v2_recovers_attribution_from_the_linked_order():
    db = FakeDB()
    db.collections["unified_orders"].documents.append(
        {
            "user_id": "owner-1",
            "order_number": "order-900",
            "raw_by_source": {
                "salla_direct": {
                    "source_details": {
                        "utm_source": "snapchat",
                        "utm_campaign": "campaign-from-order",
                        "ad_squad_id": "group-from-order",
                        "ad_id": "ad-from-order",
                        "creative_id": "creative-from-order",
                    }
                }
            },
        }
    )
    event = _cart_event(event="abandoned.cart.purchased")
    event["data"].pop("metadata")
    event["data"]["order_number"] = "order-900"

    result = await module.persist_abandoned_cart_event(
        db,
        event,
        user_id="owner-1",
    )

    snapshot = db.collections[module.ABANDONED_CART_COLLECTION].documents[0]
    assert result["order_linked"] is True
    assert snapshot["attribution_method"] == "linked_order"
    assert snapshot["attribution"]["platform"] == "snapchat"
    assert snapshot["attribution"]["utm_campaign"] == "campaign-from-order"
    assert snapshot["attribution"]["ad_group_id"] == "group-from-order"
