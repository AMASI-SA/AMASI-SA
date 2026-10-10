"""Forward-only snapshot initialization; no repair of archived production rows."""
from copy import deepcopy
import asyncio

import pytest
if __package__:
    from .shipping_pdf_fixture import install_pdf_download
else:  # Existing acceptance suites also import fixture modules directly.
    from shipping_pdf_fixture import install_pdf_download
from mongomock_motor import AsyncMongoMockClient

import order_engine.shipping_label_service as labels
from salla_shipping import CURRENT_SHIPPING, accept_shipping, shipping_root_fields


def observation(shipment="new", carrier="imile", tick=1):
    return dict(shipment_id=shipment, company_name=carrier, company_code=carrier,
                source_kind="order", event_name="order.updated", status="created",
                provider_updated_at=f"2026-10-05T10:00:0{tick}+00:00",
                shipment_updated_at=f"2026-10-05T10:00:0{tick}+00:00",
                tracking_number=f"AWB-{shipment}", label_url=f"https://labels.test/{shipment}.pdf")


def sync(row, incoming):
    accepted = accept_shipping(row, incoming)
    assert accepted is not None
    return {**row, **shipping_root_fields(accepted), CURRENT_SHIPPING: accepted}


@pytest.mark.parametrize("legacy", [False, True])
def test_new_snapshot_and_repeated_sync_never_archive_current(legacy):
    incoming = observation()
    row = shipping_root_fields(incoming) if legacy else {}
    original = deepcopy(row)
    for tick in (1, 1, 2, 3):
        row = sync(row, observation(tick=tick))
        current = row[CURRENT_SHIPPING]
        assert current["shipment_id"] == "new"
        assert current["superseded_shipment_ids"] == []
        assert current["label_url"] == incoming["label_url"]
    assert CURRENT_SHIPPING not in original


@pytest.mark.parametrize("carrier", ["imile", "dhl"])
def test_real_replacement_archives_only_old_id(carrier):
    row = sync(shipping_root_fields(observation("old")), observation("old"))
    for tick in (2, 2, 3):
        row = sync(row, observation("new", carrier, tick))
        assert row[CURRENT_SHIPPING]["shipment_id"] == "new"
        assert row[CURRENT_SHIPPING]["superseded_shipment_ids"] == ["old"]
        assert row["tracking_number"] == "AWB-new"
    assert accept_shipping(row, {**observation("old", tick=4), "source_kind": "shipment"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_carrier", [None, "imile", "dhl"])
async def test_refresh_current_passes_and_archived_print_stays_409(monkeypatch, replacement_carrier):
    install_pdf_download(monkeypatch, lambda url: "AWB-" + url.rsplit("/", 1)[-1].removesuffix(".pdf"))
    db = AsyncMongoMockClient().snapshot_preview
    lock = asyncio.Lock()

    async def owner(scoped, user, callback, **kwargs):
        async with lock:
            return await callback(scoped)

    monkeypatch.setattr(labels, "operational_owner", owner, raising=False)
    row = sync(shipping_root_fields(observation("old")), observation("old"))
    shipment, carrier = "old", "imile"
    if replacement_carrier:
        shipment, carrier = "new", replacement_carrier
        row = sync(row, observation(shipment, carrier, 2))
    row.update(user_id="preview", order_number="synthetic-order",
               order_status="completed", order_status_slug="completed",
               raw_by_source={"salla_direct": {"status": {"slug": "completed"}}})
    await db.order_review_workflows.insert_one({"user_id": "preview", "order_number": "synthetic-order",
                                               "stage": "completed", "assembly_status": "completed"})
    await db.unified_orders.insert_one(deepcopy(row))
    response = dict(ready=True, shipment_id=shipment, courier_code=carrier,
                    courier_name=carrier, status="created", tracking_number=f"AWB-{shipment}",
                    label_url=f"https://labels.test/{shipment}.pdf")
    # Provider HTTP is isolated; the real refresh and persistence guards run.
    async def resolve(*args):
        return "synthetic-order", {"id": "synthetic-order", "reference_id": "synthetic-order", "status": "completed"}
    provider_calls = 0
    both_refreshes_read = asyncio.Event()
    async def rows(*args):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 2:
            both_refreshes_read.set()
        await asyncio.wait_for(both_refreshes_read.wait(), timeout=5)
        return [dict(id=shipment, courier_id=carrier, courier_name=carrier,
                     status="created", tracking_number=response["tracking_number"],
                     label_url=response["label_url"])]
    async def resync(*args):
        before = await db.unified_orders.find_one({"user_id": "preview"})
        after = sync(before, observation(shipment, carrier, 3))
        await db.unified_orders.replace_one({"_id": before["_id"]}, after)
    monkeypatch.setattr(labels, "_resolve_order", resolve)
    monkeypatch.setattr(labels, "_print_shipment_rows", rows)
    monkeypatch.setattr(labels, "_best_effort_resync", resync)
    results = await asyncio.gather(*(labels.refresh_shipping_label(db, "preview", "synthetic-order") for _ in range(2)))
    assert all(result["ready"] and result["shipment_id"] == shipment for result in results)
    after = await db.unified_orders.find_one({"user_id": "preview"})
    assert shipment not in after[CURRENT_SHIPPING]["superseded_shipment_ids"]
    assert await db.general_ledger.count_documents({}) == 0
    if replacement_carrier:
        with pytest.raises(labels.ShippingLabelError) as error:
            await labels._persist_verified_snapshot(db, "preview", "synthetic-order", {**response, "shipment_id": "old"})
        assert error.value.status_code == 409
        assert error.value.code == "shipping_snapshot_changed"
        assert await db.unified_orders.find_one({"user_id": "preview"}) == after


def test_existing_self_superseded_record_is_not_repaired():
    row = sync({}, observation())
    row[CURRENT_SHIPPING]["superseded_shipment_ids"] = ["new"]
    original = deepcopy(row)
    result = accept_shipping(row, observation(tick=2))
    assert result["superseded_shipment_ids"] == ["new"]
    assert row == original


def test_first_snapshot_without_previous_carrier_is_not_replacement():
    row = sync({"salla_shipment_id": "new"}, observation())
    assert row[CURRENT_SHIPPING]["shipment_id"] == "new"
    assert row[CURRENT_SHIPPING]["superseded_shipment_ids"] == []


@pytest.mark.parametrize("carrier", ["imile", "dhl"])
def test_first_snapshot_with_genuine_replacement_preserves_archive(carrier):
    row = sync(shipping_root_fields(observation("old")), observation("new", carrier, 2))
    assert row[CURRENT_SHIPPING]["shipment_id"] == "new"
    assert row[CURRENT_SHIPPING]["superseded_shipment_ids"] == ["old"]


@pytest.mark.parametrize("canonical", [False, True])
def test_changed_carrier_embedding_old_id_waits_for_distinct_replacement(canonical):
    row = shipping_root_fields(observation("old"))
    if canonical:
        row = sync(row, observation("old"))
    for tick in (2, 3):
        row = sync(row, observation("old", "dhl", tick))
        assert row[CURRENT_SHIPPING]["shipment_id"] is None
        assert row[CURRENT_SHIPPING]["superseded_shipment_ids"] == ["old"]
        assert row["tracking_number"] is None
        assert row["shipping_label_url"] is None
    row = sync(row, observation("new", "dhl", 4))
    assert row[CURRENT_SHIPPING]["shipment_id"] == "new"
    assert row[CURRENT_SHIPPING]["superseded_shipment_ids"] == ["old"]
