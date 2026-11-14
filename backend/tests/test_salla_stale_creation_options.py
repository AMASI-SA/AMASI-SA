"""Delayed verified creation enriches only proven, absent customer choices.

These focused tests use the real webhook, mapper and option patch/writer with an
in-memory Mongo adapter. The source-fence/owner transaction boundary is isolated
here; the companion source-snapshot suite exercises the real Mongo transaction.
"""
from copy import deepcopy
from datetime import datetime, timezone
import sys
import types
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

from salla_integration import cost_option_recovery as recovery
from salla_integration import webhook_order_sync as webhook
from salla_integration.sync import _salla_order_to_doc


OWNER = "synthetic-owner"
NUMBER = "synthetic-order"
OLDER = "2026-01-01T00:00:00+00:00"
NEWER = "2026-01-01T00:00:01+00:00"


def money(value):
    return {"amount": value, "currency": "SAR"}


def fixture():
    def line(identity, price):
        return {"id": identity, "product_id": "same-product", "quantity": 1,
                "sku": "SYNTHETIC-SAME-SKU", "name": "Synthetic product",
                "currency": "SAR", "amounts": {
                    "price_without_tax": money(price), "total_discount": money(0),
                    "tax": {"percent": 8, "amount": money(price * 0.08)},
                    "total": money(price * 1.08)}}

    rows = [line("native-line-a", 25), line("native-line-b", 50)]
    raw = {"id": "native-order", "reference_id": NUMBER,
           "updated_at": NEWER, "created_at": OLDER,
           "status": {"slug": "processing", "name": "Processing"},
           "currency": "SAR", "items": rows,
           "amounts": {"total": money(81)},
           "shipping": {"company": "synthetic-carrier"}}
    order = {**_salla_order_to_doc(raw), "user_id": OWNER,
             "raw_by_source": {"salla_direct": deepcopy(raw)},
             "g47_salla_snapshot": {"source_updated_at": NEWER,
                                    "revision": 4, "component_pending": True},
             "last_salla_direct_status_source_updated_at": NEWER,
             "cost_snapshot": {"historical": "preserve"},
             "preparation": {"state": "preserve"}}
    incoming = deepcopy(raw)
    incoming.update(updated_at=OLDER, status={"slug": "pending_review"})
    for index, row in enumerate(incoming["items"]):
        row["product"] = {"id": row.pop("product_id")}
        row["product_sku_id"] = None
        row["amounts"]["original_price"] = money(100)
        row["options"] = [{"product_option_id": "size-option", "name": "Size",
                           "value": {"id": f"choice-{index}", "name": f"size-{index}"}}]
    return order, incoming


def source_time(value):
    if isinstance(value, dict):
        value = value.get("date") or value.get("updated_at")
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return result.astimezone(timezone.utc).isoformat(timespec="microseconds") if result.tzinfo else None


def provider_version(payload, *, created_event=False):
    return source_time(payload.get("updated_at") or (payload.get("created_at") if created_event else None))


def install_fence(monkeypatch):
    async def local_owner(db, owner, callback):
        assert owner == OWNER
        return await callback(db)

    monkeypatch.setattr("operational_atomic.operational_owner", local_owner)
    async def fenced(db, **kwargs):
        selector = {"user_id": kwargs["user_id"], "order_number": kwargs["order_number"]}
        current = await db.unified_orders.find_one(selector)
        previous = source_time(current["g47_salla_snapshot"]["source_updated_at"])
        version = provider_version(kwargs["payload"], created_event=kwargs.get("created_event", False))
        if version is None or version < previous:
            return {"stale": True, "created": False}
        assert kwargs.get("allow_delivery_observation") is False
        assert not kwargs.get("authoritative_refresh")
        await kwargs["before_persist"](db)
        result = await kwargs["persist"](db)
        await db.unified_orders.update_one(selector, {"$set": {
            "g47_salla_snapshot.revision": current["g47_salla_snapshot"]["revision"] + 1,
            "g47_salla_snapshot.component_pending": True}})
        return result

    module = types.ModuleType("fulfillment_v2_routes")
    module.persist_component_source_snapshot = fenced
    module.component_provider_version = provider_version
    module.component_source_time = source_time
    module.auto_route_instant_order = AsyncMock(side_effect=AssertionError("No fulfillment execution"))
    monkeypatch.setitem(sys.modules, "fulfillment_v2_routes", module)
    provider = AsyncMock(side_effect=AssertionError("No provider request"))
    monkeypatch.setattr("salla_integration.sync.call_salla", provider)
    full_upsert = AsyncMock(side_effect=AssertionError("No stale full snapshot write"))
    monkeypatch.setattr(webhook, "upsert_order", full_upsert)
    return module, provider, full_upsert


async def seeded():
    db = AsyncMongoMockClient().db
    order, payload = fixture()
    await db.unified_orders.insert_one(deepcopy(order))
    await db.salla_integrations.insert_one({"user_id": OWNER, "store_id": "synthetic-store"})
    return db, order, payload


async def read_order(db):
    return await db.unified_orders.find_one({"user_id": OWNER, "order_number": NUMBER}, {"_id": 0})


@pytest.mark.asyncio
async def test_delayed_creation_fills_each_native_line_without_accepting_stale_snapshot(monkeypatch):
    db, before, payload = await seeded()
    module, provider, full_upsert = install_fence(monkeypatch)
    result = await webhook.sync_order_from_verified_webhook(db, {
        "event": "order.created", "merchant": "synthetic-store", "data": payload})

    assert result["synced"] is False
    assert result["reason"] == "stale_salla_snapshot"
    assert result["customer_options_enrichment"] == {
        "attempted": True, "applied": True, "line_count": 2, "reason": "customer_options_enriched"}
    after = await read_order(db)
    for index, row in enumerate(after["products"]):
        assert row["options_raw"] == payload["items"][index]["options"]
        assert row["order_item_id"] == before["products"][index]["order_item_id"]
    expected = deepcopy(before)
    for index in range(2):
        expected["products"][index]["options"] = [{"name": "Size", "value": f"size-{index}"}]
        expected["products"][index]["options_raw"] = payload["items"][index]["options"]
        expected["raw_by_source"]["salla_direct"]["items"][index]["options"] = payload["items"][index]["options"]
    expected["g47_salla_snapshot"]["revision"] += 1
    assert after == expected
    provider.assert_not_awaited()
    full_upsert.assert_not_awaited()
    module.auto_route_instant_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_updated_event_does_not_enrich(monkeypatch):
    db, before, payload = await seeded()
    install_fence(monkeypatch)
    result = await webhook.sync_order_from_verified_webhook(db, {
        "event": "order.updated", "merchant": "synthetic-store", "data": payload})
    assert result["synced"] is False
    assert "customer_options_enrichment" not in result
    assert await read_order(db) == before


async def supplement(db, payload):
    from salla_integration.stale_creation_options import enrich_stale_creation_options
    return await enrich_stale_creation_options(db, user_id=OWNER, order_number=NUMBER, payload=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "order_alias", "reference_alias", "root_reference_alias", "root_native_identity",
    "line_item_alias", "line_order_item_alias", "duplicate_native_line",
    "duplicate_root_line", "missing_line", "added_line", "conflicting_product",
    "root_product", "quantity", "invalid_quantity", "variant", "missing_money",
    "price", "tax", "line_currency", "order_currency", "order_total",
    "shared_original_price", "shared_price", "root_cancelled", "raw_cancelled",
    "watermark_cancelled", "root_cancelled_flag", "raw_cancelled_flag",
    "authoritative_refresh", "unproven_watermark", "invalid_revision",
    "newer_status_clock", "not_older_creation", "root_choices", "raw_attribute",
    "raw_variant_name", "root_variant_name", "raw_variant_label", "nested_variant_label",
    "root_variant_label", "malformed_options",
])
async def test_refuses_unproven_or_protected_source_without_business_writes(monkeypatch, case):
    order, payload = fixture()
    raw = order["raw_by_source"]["salla_direct"]
    current, old, product = raw["items"][0], payload["items"][0], order["products"][0]
    if case == "order_alias":
        payload["order_id"] = "conflicting-native-order"
    elif case == "reference_alias":
        payload["order_number"] = "conflicting-reference"
    elif case == "root_reference_alias":
        order["reference_id"] = "conflicting-reference"
    elif case == "root_native_identity":
        order["order_id"] = "conflicting-native-order"
    elif case == "line_item_alias":
        old["item_id"] = "conflicting-line"
    elif case == "line_order_item_alias":
        current["order_item_id"] = "conflicting-line"
    elif case == "duplicate_native_line":
        payload["items"][1]["id"] = old["id"]
    elif case == "duplicate_root_line":
        order["products"][1]["order_item_id"] = product["order_item_id"]
    elif case == "missing_line":
        payload["items"].pop()
    elif case == "added_line":
        payload["items"].append({**deepcopy(old), "id": "extra-native-line"})
    elif case == "conflicting_product":
        old["product_id"] = "conflicting-product"
    elif case == "root_product":
        product["product_id"] = "different-product"
    elif case == "quantity":
        old["quantity"] = 2
    elif case == "invalid_quantity":
        current["quantity"] = True
    elif case == "variant":
        old["variant_id"] = "different-variant"
    elif case == "missing_money":
        old["amounts"].pop("total_discount")
    elif case == "price":
        old["amounts"]["price_without_tax"] = money(999)
    elif case == "tax":
        old["amounts"]["tax"]["percent"] = 9
    elif case == "line_currency":
        old["amounts"]["total"]["currency"] = "USD"
    elif case == "order_currency":
        payload["currency"] = "USD"
    elif case == "order_total":
        payload["amounts"]["total"] = money(999)
    elif case == "shared_original_price":
        current["amounts"]["original_price"] = money(999)
    elif case == "shared_price":
        current["amounts"]["price"], old["amounts"]["price"] = money(25), money(999)
    elif case == "root_cancelled":
        order["order_status"] = "ملغي"
    elif case == "raw_cancelled":
        raw["status"]["slug"] = "canceled"
    elif case == "watermark_cancelled":
        order["g47_salla_snapshot"]["cancelled"] = True
    elif case == "root_cancelled_flag":
        order["cancelled"] = True
    elif case == "raw_cancelled_flag":
        raw["is_cancelled"] = True
    elif case == "authoritative_refresh":
        order["g47_salla_snapshot"]["requires_authoritative_refresh"] = True
    elif case == "unproven_watermark":
        order["g47_salla_snapshot"]["source_updated_at"] = "invalid"
    elif case == "invalid_revision":
        order["g47_salla_snapshot"]["revision"] = True
    elif case == "newer_status_clock":
        order["last_salla_direct_status_source_updated_at"] = "2026-01-01T00:00:02+00:00"
    elif case == "not_older_creation":
        payload["updated_at"] = NEWER
    elif case == "root_choices":
        product["customer_options"] = [{"name": "Keep", "value": "current"}]
    elif case == "raw_attribute":
        current["attribute"] = {"Size": "current-extra-large"}
    elif case == "raw_variant_name":
        current["variant_name"] = "current-extra-large"
    elif case == "root_variant_name":
        product["variant_name"] = "current-extra-large"
    elif case == "raw_variant_label":
        current["variant"] = {"name": "current-extra-large"}
    elif case == "nested_variant_label":
        current["product"] = {"id": current["product_id"], "variant": {"name": "current-extra-large"}}
    elif case == "root_variant_label":
        product["variant"] = {"name": "current-extra-large"}
    elif case == "malformed_options":
        old["options"] = ["not-an-option-object"]
    db = AsyncMongoMockClient().db
    await db.unified_orders.insert_one(deepcopy(order))
    install_fence(monkeypatch)
    result = await supplement(db, payload)
    assert result["attempted"] is True
    assert result["applied"] is False
    assert result["line_count"] == 0
    assert await read_order(db) == order


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "options", "options_raw", "options_normalized", "choices", "attributes", "product_options",
    "selected_options", "customer_options", "custom_fields", "customizations", "personalization",
    "fields", "questions", "attachments", "files", "option", "variant_options", "option_values",
    "size", "color", "material", "attribute", "variant_name",
])
@pytest.mark.parametrize("value", [None, []])
async def test_explicit_empty_or_null_native_choice_fields_are_not_omissions(monkeypatch, field, value):
    db, order, payload = await seeded()
    order["raw_by_source"]["salla_direct"]["items"][0][field] = value
    await db.unified_orders.replace_one({"user_id": OWNER}, deepcopy(order))
    install_fence(monkeypatch)
    result = await supplement(db, payload)
    assert result["applied"] is False
    assert await read_order(db) == order


@pytest.mark.asyncio
async def test_does_not_partially_apply_when_existing_writer_cannot_express_every_proven_line(monkeypatch):
    db, order, payload = await seeded()
    order["raw_by_source"]["salla_direct"]["items"][0]["id"] = " native-line-a "
    await db.unified_orders.replace_one({"user_id": OWNER}, deepcopy(order))
    install_fence(monkeypatch)
    result = await supplement(db, payload)
    assert result["applied"] is False
    assert result["line_count"] == 0
    assert await read_order(db) == order


@pytest.mark.asyncio
async def test_reordered_same_sku_lines_stay_separate_and_replay_is_noop(monkeypatch):
    db, _, payload = await seeded()
    payload["items"].reverse()
    install_fence(monkeypatch)
    result = await supplement(db, payload)
    assert result["applied"] is True
    after = await read_order(db)
    by_id = {row["order_item_id"]: row["options_raw"] for row in after["products"]}
    assert by_id == {row["id"]: row["options"] for row in payload["items"]}
    replay = await supplement(db, payload)
    assert replay["applied"] is False
    assert replay["line_count"] == 0
    assert await read_order(db) == after


@pytest.mark.asyncio
async def test_matching_native_variant_object_identity_remains_eligible(monkeypatch):
    db, order, payload = await seeded()
    for current, product, incoming in zip(order["raw_by_source"]["salla_direct"]["items"],
                                          order["products"], payload["items"]):
        current["variant"] = {"id": "same-variant", "name": "Synthetic variant"}
        product["variant_id"] = "same-variant"
        incoming["product_sku_id"] = "same-variant"
    await db.unified_orders.replace_one({"user_id": OWNER}, deepcopy(order))
    install_fence(monkeypatch)
    result = await supplement(db, payload)
    assert result["applied"] is True
    assert result["line_count"] == 2


@pytest.mark.asyncio
async def test_source_change_before_writer_refuses_fill(monkeypatch):
    db, _, payload = await seeded()
    install_fence(monkeypatch)
    original = recovery.apply_options
    changed = None

    async def concurrent_change(scoped, order, items):
        nonlocal changed
        await scoped.unified_orders.update_one({"user_id": OWNER}, {"$set": {
            "raw_by_source.salla_direct.items.0.options": []}})
        changed = await read_order(scoped)
        return await original(scoped, order, items)

    monkeypatch.setattr(recovery, "apply_options", concurrent_change)
    result = await supplement(db, payload)
    assert result == {"attempted": True, "applied": False, "line_count": 0, "reason": "source_changed"}
    assert await read_order(db) == changed


@pytest.mark.asyncio
async def test_unexpected_failure_never_leaks_payload_or_claims_success(monkeypatch):
    db, before, payload = await seeded()
    install_fence(monkeypatch)
    monkeypatch.setattr(recovery, "apply_options", AsyncMock(side_effect=RuntimeError("private synthetic customer value")))
    result = await supplement(db, payload)
    assert result == {"attempted": True, "applied": False, "line_count": 0, "reason": "customer_options_unavailable"}
    assert "private" not in str(result)
    assert await read_order(db) == before
