"""Local label persistence must not revive superseded canonical shipments.

Mongo reads/updates are real mongomock operations. Only the owner transaction
adapter is replaced: mongomock does not provide replica-set transactions.
"""
from copy import deepcopy

import pytest
from mongomock_motor import AsyncMongoMockClient

import order_engine.shipping_label_service as shipping
from salla_shipping import CURRENT_SHIPPING, accept_shipping, extract_shipping


OWNER = "owner-label"
ORDER = "3001"


@pytest.fixture
async def db(monkeypatch):
    database = AsyncMongoMockClient().label_guard
    database.owner_calls = []

    async def local_owner(scoped, owner, callback, **_kwargs):
        assert scoped is database and owner == OWNER
        database.owner_calls.append(owner)
        return await callback(scoped)

    monkeypatch.setattr(shipping, "operational_owner", local_owner, raising=False)
    return database


async def seed(db, *, shipment_id="new-id", status="created", company="iMile للتوصيل", code="imile", superseded=None):
    record = {
        "user_id": OWNER, "order_number": ORDER,
        "shipping_company": company, "shipping_company_code": code,
        "salla_shipment_id": shipment_id, "shipping_status": status, "shipment_status": status,
        "tracking_number": "NEW-AWB", "shipping_number": "NEW-AWB", "shipping_label_url": "https://labels.test/new.pdf",
        CURRENT_SHIPPING: {
            "company_name": company, "company_code": code, "shipment_id": shipment_id,
            "status": status, "tracking_number": "NEW-AWB", "label_url": "https://labels.test/new.pdf",
            "carrier_updated_at": "2026-10-01T10:00:00+00:00",
            "shipment_updated_at": "2026-10-01T10:01:00+00:00",
            "superseded_shipment_ids": list(superseded or []),
        },
    }
    await db.unified_orders.insert_one(record)
    return await db.unified_orders.find_one({"user_id": OWNER, "order_number": ORDER})


def snapshot(shipment_id="new-id", company="iMile", code="imile"):
    return {
        "ready": True, "shipment_id": shipment_id, "status": "created",
        "courier_name": company, "courier_code": code,
        "tracking_number": "VERIFIED-AWB", "label_url": "https://labels.test/verified.pdf",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["superseded", "different_id", "cancelled", "different_carrier"])
async def test_stale_verified_snapshot_never_overwrites_current_shipping(db, reason):
    before = await seed(db, status="cancelled" if reason == "cancelled" else "created",
                        superseded=["old-id"] if reason == "superseded" else [])
    result = snapshot("old-id" if reason in {"superseded", "different_id"} else "new-id",
                      company="مندوب الرياض" if reason == "different_carrier" else "iMile",
                      code="store" if reason == "different_carrier" else "imile")
    with pytest.raises(shipping.ShippingLabelError) as rejected:
        await shipping._persist_verified_snapshot(db, OWNER, ORDER, result)
    assert rejected.value.code == "shipping_snapshot_changed"
    assert rejected.value.status_code == 409
    assert await db.unified_orders.find_one({"user_id": OWNER, "order_number": ORDER}) == before
    assert await db.general_ledger.count_documents({}) == 0


@pytest.mark.asyncio
async def test_matching_current_snapshot_uses_owner_scope_and_preserves_carrier_clocks(db):
    before = await seed(db)
    await shipping._persist_verified_snapshot(db, OWNER, ORDER, snapshot())
    after = await db.unified_orders.find_one({"user_id": OWNER, "order_number": ORDER})
    assert after["tracking_number"] == "VERIFIED-AWB"
    assert after["shipping_number"] == "VERIFIED-AWB"
    assert after[CURRENT_SHIPPING]["tracking_number"] == "VERIFIED-AWB"
    assert after[CURRENT_SHIPPING]["carrier_updated_at"] == before[CURRENT_SHIPPING]["carrier_updated_at"]
    assert after[CURRENT_SHIPPING]["shipment_updated_at"] == before[CURRENT_SHIPPING]["shipment_updated_at"]
    assert after[CURRENT_SHIPPING]["verified_at"] == after["shipping_verified_at"]
    assert db.owner_calls == [OWNER]
    assert await db.general_ledger.count_documents({}) == 0


@pytest.mark.asyncio
async def test_legacy_rows_keep_existing_persistence_behavior(db):
    await db.unified_orders.insert_one({"user_id": OWNER, "order_number": ORDER})
    await shipping._persist_verified_snapshot(db, OWNER, ORDER, snapshot())
    after = await db.unified_orders.find_one({"user_id": OWNER, "order_number": ORDER})
    assert after["salla_shipment_id"] == "new-id"
    assert after["shipping_label_url"] == "https://labels.test/verified.pdf"
    assert CURRENT_SHIPPING not in after


@pytest.mark.asyncio
async def test_clear_no_active_labels_keeps_canonical_cancellation(db):
    await seed(db, status="cancelled")
    await shipping._persist_verified_snapshot(db, OWNER, ORDER, {"ready": False}, clear_missing=True)
    after = await db.unified_orders.find_one({"user_id": OWNER, "order_number": ORDER})
    assert after["shipping_status"] == "cancelled"
    assert after[CURRENT_SHIPPING]["status"] == "cancelled"
    assert after[CURRENT_SHIPPING]["shipment_id"] == "new-id"
    assert after["salla_shipment_id"] == "new-id"
    assert not after.get("shipping_label_url")
    assert not after[CURRENT_SHIPPING].get("label_url")


@pytest.mark.asyncio
async def test_legacy_print_returns_provider_label_without_overwriting_new_local_identity(db, monkeypatch):
    await seed(db, shipment_id="old-id")

    async def resolve(*_args):
        changed = snapshot("new-id")
        current = deepcopy((await db.unified_orders.find_one({"user_id": OWNER}))[CURRENT_SHIPPING])
        current.update(shipment_id="new-id", superseded_shipment_ids=["old-id"])
        await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
            CURRENT_SHIPPING: current, "salla_shipment_id": changed["shipment_id"],
        }})
        return "9001", {"shipments": []}

    async def rows(*_args):
        return [{"id": "old-id", "status": "created", "courier_name": "iMile", "courier_id": "imile",
                 "tracking_number": "OLD-AWB", "label_url": "https://labels.test/old.pdf"}]

    async def no_resync(*_args):
        return None

    monkeypatch.setattr(shipping, "_resolve_order", resolve)
    monkeypatch.setattr(shipping, "_shipment_rows", rows)
    monkeypatch.setattr(shipping, "_best_effort_resync", no_resync)
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["ready"] and result["label_url"] == "https://labels.test/old.pdf"
    after = await db.unified_orders.find_one({"user_id": OWNER, "order_number": ORDER})
    assert after["salla_shipment_id"] == "new-id"
    assert after["tracking_number"] == "NEW-AWB"


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_status", ["draft", "cancelled"])
async def test_confirmed_creation_can_replace_same_carrier_id_before_its_webhook(db, previous_status):
    previous_ids = [f"superseded-{index}" for index in range(25)]
    await seed(db, shipment_id="old-id", status=previous_status, superseded=previous_ids)
    baseline = await shipping._label_baseline(db, OWNER, ORDER)
    await shipping._persist_verified_snapshot(db, OWNER, ORDER, snapshot("created-id"),
                                             baseline=baseline, created_replacement=True)
    after = await db.unified_orders.find_one({"user_id": OWNER, "order_number": ORDER})
    assert after["salla_shipment_id"] == "created-id"
    assert after[CURRENT_SHIPPING]["shipment_id"] == "created-id"
    assert after[CURRENT_SHIPPING]["superseded_shipment_ids"] == [*previous_ids, "old-id"]
    assert after[CURRENT_SHIPPING]["carrier_updated_at"] == baseline[CURRENT_SHIPPING]["carrier_updated_at"]
    assert after[CURRENT_SHIPPING]["shipment_updated_at"] == baseline[CURRENT_SHIPPING]["shipment_updated_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_current_label", [True, False])
async def test_provider_post_confirmation_reaches_guard_without_extra_provider_calls(db, monkeypatch, has_current_label):
    await seed(db, shipment_id="old-id", status="draft")
    if not has_current_label:
        await db.unified_orders.update_one({"user_id": OWNER}, {"$unset": {
            "shipping_label_url": "", "tracking_number": "", "shipping_number": "",
            f"{CURRENT_SHIPPING}.label_url": "", f"{CURRENT_SHIPPING}.tracking_number": "",
        }})
    source = {
        "id": "old-id", "status": "draft", "courier_id": "imile", "courier_name": "iMile",
        "packages": [{"name": "قطعة", "quantity": 1}],
        "ship_to": {key: "test" for key in (
            "name", "email", "phone", "country", "city", "address_line", "street_number", "block",
            "short_address", "building_number", "additional_number", "postal_code", "geo_coordinates",
        )},
    }
    created = {"id": "created-id", "status": "created", "courier_id": "imile", "courier_name": "iMile",
               "tracking_number": "CREATED-AWB", "label_url": "https://labels.test/created.pdf"}
    provider_calls = []

    async def resolve(*_args):
        return "9001", {"status": "completed", "shipments": [source]}

    async def rows(*_args):
        return [source]

    async def post(_db, _owner, method, path, **_kwargs):
        provider_calls.append((method, path))
        return {"data": created}

    async def no_resync(*_args):
        return None

    monkeypatch.setattr(shipping, "_resolve_order", resolve)
    monkeypatch.setattr(shipping, "_shipment_rows", rows)
    monkeypatch.setattr(shipping, "call_salla", post)
    monkeypatch.setattr(shipping, "_best_effort_resync", no_resync)
    result = await shipping.issue_shipping_label(db, OWNER, ORDER)
    assert result["ready"] is True and result["shipment_id"] == "created-id"
    assert provider_calls == [("POST", "/shipments")]
    assert (await db.unified_orders.find_one({"user_id": OWNER}))[CURRENT_SHIPPING]["shipment_id"] == "created-id"


@pytest.mark.asyncio
async def test_creation_cannot_restore_an_id_already_marked_superseded(db):
    before = await seed(db, shipment_id="new-id", superseded=["old-id"])
    baseline = await shipping._label_baseline(db, OWNER, ORDER)
    with pytest.raises(shipping.ShippingLabelError):
        await shipping._persist_verified_snapshot(db, OWNER, ORDER, snapshot("old-id"),
                                                 baseline=baseline, created_replacement=True)
    assert await db.unified_orders.find_one({"user_id": OWNER}) == before


@pytest.mark.asyncio
async def test_compare_and_swap_rejects_metadata_changed_after_validation(db, monkeypatch):
    await seed(db)
    collection = db.unified_orders
    collection_type = type(collection)
    original_update = collection_type.update_one
    raced = False

    async def race(scoped_collection, selector, update, **kwargs):
        nonlocal raced
        if not raced and scoped_collection.name == "unified_orders" and CURRENT_SHIPPING in selector:
            raced = True
            await original_update(scoped_collection, {"user_id": OWNER}, {"$set": {
                f"{CURRENT_SHIPPING}.shipment_id": "replacement", "salla_shipment_id": "replacement",
            }})
        return await original_update(scoped_collection, selector, update, **kwargs)

    monkeypatch.setattr(collection_type, "update_one", race)
    with pytest.raises(shipping.ShippingLabelError) as rejected:
        await shipping._persist_verified_snapshot(db, OWNER, ORDER, snapshot())
    assert rejected.value.code == "shipping_snapshot_changed"
    assert raced is True
    after = await collection.find_one({"user_id": OWNER})
    assert after["salla_shipment_id"] == "replacement"
    assert after["tracking_number"] == "NEW-AWB"


@pytest.mark.asyncio
async def test_carrier_change_since_request_blocks_even_a_matching_returned_id(db):
    await seed(db)
    baseline = await shipping._label_baseline(db, OWNER, ORDER)
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        f"{CURRENT_SHIPPING}.company_name": "مندوب الرياض", f"{CURRENT_SHIPPING}.company_code": "store",
        "shipping_company": "مندوب الرياض", "shipping_company_code": "store",
    }})
    before = await db.unified_orders.find_one({"user_id": OWNER})
    with pytest.raises(shipping.ShippingLabelError):
        await shipping._persist_verified_snapshot(db, OWNER, ORDER, snapshot(company="مندوب الرياض", code="store"), baseline=baseline)
    assert await db.unified_orders.find_one({"user_id": OWNER}) == before


@pytest.mark.asyncio
async def test_cancelled_provider_snapshot_clears_operational_group(db):
    await seed(db)
    cancelled = {**snapshot(), "status": "cancelled", "ready": False}
    await shipping._persist_verified_snapshot(db, OWNER, ORDER, cancelled)
    after = await db.unified_orders.find_one({"user_id": OWNER})
    assert after["shipping_status"] == "cancelled"
    assert after[CURRENT_SHIPPING]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_confirmed_replacement_without_awb_cannot_inherit_previous_label(db):
    before = await seed(db)
    fresh = snapshot("fresh-id")
    fresh.update(ready=False, status="creating", tracking_number=None, label_url=None)
    await shipping._persist_verified_snapshot(db, OWNER, ORDER, fresh, baseline=before, created_replacement=True)
    after = await db.unified_orders.find_one({"user_id": OWNER, "order_number": ORDER})
    assert after["salla_shipment_id"] == "fresh-id"
    for root, key in (("tracking_number", "tracking_number"), ("shipping_label_url", "label_url"), ("tracking_url", "tracking_url")):
        assert after.get(root) is None
        assert after[CURRENT_SHIPPING].get(key) is None
    assert after.get("shipping_number") is None
    assert after[CURRENT_SHIPPING]["label_url"] is None
    assert not after.get("tracking_number")


def test_snapshot_preserves_zero_as_a_known_store_courier_code():
    assert shipping._snapshot({"id": "store-id", "courier_id": 0, "courier_name": "مندوب المتجر"})["courier_code"] == "0"


@pytest.mark.asyncio
async def test_generic_local_courier_names_do_not_establish_same_carrier(db):
    before = await seed(db, company="مندوب المتجر", code=None)
    with pytest.raises(shipping.ShippingLabelError):
        await shipping._persist_verified_snapshot(db, OWNER, ORDER, snapshot(company="مندوب جدة", code=None))
    assert await db.unified_orders.find_one({"user_id": OWNER}) == before


def test_unresolved_canonical_name_does_not_fall_back_to_an_old_root_name():
    row = {"shipping_company": "iMile", CURRENT_SHIPPING: {"company_code": "store", "company_name": None}}
    assert shipping._label_carrier(row) == {"code": "store", "name": ""}


@pytest.mark.parametrize("structured", [
    {"company": {"name": "iMile", "id": "imile"}},
    {"shipping_company": {"name": "iMile", "id": "imile"}},
    {"courier": {"name": "iMile", "id": "imile"}},
])
def test_snapshot_extracts_structured_carrier_identity(structured):
    result = shipping._snapshot({"id": "new-id", **structured})
    assert result["courier_name"] == "iMile"
    assert result["courier_code"] == "imile"


@pytest.mark.asyncio
async def test_verified_provider_clock_rejects_older_verification_and_delayed_webhook(db):
    await seed(db)
    provider_row = {
        "id": "new-id", "courier_name": "iMile", "courier_id": "imile", "status": "created",
        "tracking_number": "VERIFIED-AWB", "label_url": "https://labels.test/verified.pdf",
        "updated_at": {"date": "2026-10-01 15:00:00", "timezone": "UTC"},
    }
    result = shipping._snapshot(provider_row)
    assert result["shipment_updated_at"] == "2026-10-01T15:00:00+00:00"
    await shipping._persist_verified_snapshot(db, OWNER, ORDER, result)
    verified = await db.unified_orders.find_one({"user_id": OWNER})
    assert verified[CURRENT_SHIPPING]["shipment_updated_at"] == "2026-10-01T15:00:00+00:00"
    assert verified[CURRENT_SHIPPING]["carrier_updated_at"] == "2026-10-01T10:00:00+00:00"
    earlier = {**result, "shipment_updated_at": "2026-10-01T11:00:00+00:00", "tracking_number": "OLD-AWB"}
    with pytest.raises(shipping.ShippingLabelError) as rejected:
        await shipping._persist_verified_snapshot(db, OWNER, ORDER, earlier)
    assert rejected.value.code == "shipping_snapshot_changed"
    assert await db.unified_orders.find_one({"user_id": OWNER}) == verified
    delayed = extract_shipping({**provider_row, "updated_at": "2026-10-01T11:00:00Z", "status": "cancelled"},
                               event_name="shipment.cancelled")
    assert accept_shipping(verified, delayed) is None


def test_snapshot_uses_actual_created_clock_when_updated_clock_is_absent():
    result = shipping._snapshot({"id": "new-id", "created_at": "2026-10-01T12:00:00Z"})
    assert result["shipment_updated_at"] == "2026-10-01T12:00:00+00:00"


@pytest.mark.asyncio
async def test_same_current_printed_label_remains_ready_on_repeated_refresh(db, monkeypatch):
    """A printable, identical carrier/shipment/AWB must not become stale."""
    import fulfillment_carrier_label as workflow_shipping

    await seed(db)
    await db.order_review_workflows.insert_one({
        "user_id": OWNER, "order_number": ORDER, "stage": "completed",
        "assembly_status": "completed", "salla_order_status": "completed",
        "carrier_label_print_confirmed": True,
    })
    provider = {"id": "new-id", "status": "created", "courier_id": "imile",
                "courier_name": "iMile", "tracking_number": "NEW-AWB",
                "label_url": "https://labels.test/new.pdf"}

    async def resolve(*_args):
        return "9001", {"status": "completed", "shipments": [provider]}

    async def rows(*_args):
        return [provider]

    async def no_resync(*_args):
        return None

    monkeypatch.setattr(shipping, "_resolve_order", resolve)
    monkeypatch.setattr(shipping, "_shipment_rows", rows)
    monkeypatch.setattr(shipping, "_best_effort_resync", no_resync)
    for _ in range(2):
        result = await workflow_shipping.sync_completed_carrier_label(
            db, user_id=OWNER, order_number=ORDER, actor_id="synthetic-actor",
            actor_name="synthetic", action="refresh",
        )
        assert result["ready"] is True
        assert result["shipment_id"] == "new-id"
        assert result["tracking_number"] == "NEW-AWB"
    workflow = await db.order_review_workflows.find_one({"user_id": OWNER})
    assert workflow["carrier_label_ready"] is True
    assert workflow["carrier_label_print_confirmed"] is True
    assert workflow["carrier_label_error_code"] is None
    assert await db.general_ledger.count_documents({}) == 0


@pytest.mark.asyncio
async def test_changed_carrier_rejects_old_snapshot_but_accepts_new_current_label(db):
    before = await seed(db, shipment_id="replacement", company="SMSA", code="smsa",
                        superseded=["old-id"])
    with pytest.raises(shipping.ShippingLabelError) as rejected:
        await shipping._persist_verified_snapshot(db, OWNER, ORDER, snapshot("old-id"))
    assert rejected.value.code == "shipping_snapshot_changed"
    assert await db.unified_orders.find_one({"user_id": OWNER}) == before
    fresh = snapshot("replacement", company="SMSA", code="smsa")
    await shipping._persist_verified_snapshot(db, OWNER, ORDER, fresh,
                                             baseline=await shipping._label_baseline(db, OWNER, ORDER))
    after = await db.unified_orders.find_one({"user_id": OWNER})
    assert after[CURRENT_SHIPPING]["company_code"] == "smsa"
    assert after[CURRENT_SHIPPING]["shipment_id"] == "replacement"
    assert after["tracking_number"] == fresh["tracking_number"]


@pytest.mark.asyncio
async def test_same_identity_parallel_baselines_and_unrelated_update_are_not_stale(db):
    """Two in-flight reads can finish safely when shipping identity is unchanged.

    This covers interleaved requests before persistence, not Mongo transaction
    conflicts; the owner adapter is mocked and existing CAS tests cover races.
    """
    await seed(db)
    baseline_a = await shipping._label_baseline(db, OWNER, ORDER)
    baseline_b = deepcopy(baseline_a)
    same = {**snapshot(), "tracking_number": "NEW-AWB", "label_url": "https://labels.test/new.pdf"}
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {"synthetic_note": "unrelated"}})
    await shipping._persist_verified_snapshot(db, OWNER, ORDER, same, baseline=baseline_a)
    await shipping._persist_verified_snapshot(db, OWNER, ORDER, same, baseline=baseline_b)
    after = await db.unified_orders.find_one({"user_id": OWNER})
    assert after["synthetic_note"] == "unrelated"
    assert after["tracking_number"] == "NEW-AWB"
    assert after[CURRENT_SHIPPING]["shipment_id"] == "new-id"


@pytest.mark.asyncio
async def test_old_awb_response_cannot_replace_concurrently_updated_awb(db):
    await seed(db)
    baseline = await shipping._label_baseline(db, OWNER, ORDER)
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        "tracking_number": "CURRENT-AWB", "shipping_number": "CURRENT-AWB",
        f"{CURRENT_SHIPPING}.tracking_number": "CURRENT-AWB",
    }})
    before = await db.unified_orders.find_one({"user_id": OWNER})
    old = {**snapshot(), "tracking_number": "NEW-AWB"}
    with pytest.raises(shipping.ShippingLabelError) as rejected:
        await shipping._persist_verified_snapshot(db, OWNER, ORDER, old, baseline=baseline)
    assert rejected.value.code == "shipping_snapshot_changed"
    assert await db.unified_orders.find_one({"user_id": OWNER}) == before
