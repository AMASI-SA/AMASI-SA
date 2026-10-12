"""Phase-one shipping policy: real service, synthetic GETs/document boundary.

The document verifier has its own byte/transport tests. Here a named boundary
stub proves verification precedes the final provider and local-state fences.
Mongomock's owner adapter is test-only; no network/provider writes are allowed.
"""
from copy import deepcopy

import pytest
from mongomock_motor import AsyncMongoMockClient

from order_engine import shipping_label_service as shipping
from salla_shipping import CURRENT_SHIPPING

OWNER = "phase1-owner"
NUMBER = "710001"


@pytest.fixture
async def scenario(monkeypatch):
    import shipping_print_document as documents
    db = AsyncMongoMockClient().shipping_phase1
    await db.unified_orders.insert_one({
        "user_id": OWNER, "order_number": NUMBER,
        "order_status": "completed", "order_status_slug": "completed",
        "raw_by_source": {"salla_direct": {"status": {"slug": "completed"}}},
        CURRENT_SHIPPING: {"company_name": "SMSA", "company_code": "smsa",
            "shipment_id": "shipment-1", "tracking_number": "AWB-1", "status": "created"},
    })
    await db.order_review_workflows.insert_one({
        "user_id": OWNER, "order_number": NUMBER, "assembly_status": "completed",
        "stage": "completed", "carrier_label_ready": True,
        "carrier_label_url": "https://old.test/stale.pdf",
    })
    state = {"order": {"id": "internal-1", "reference_id": NUMBER, "status": {"slug": "completed"}},
        "shipment": {"id": "shipment-1", "order_id": "internal-1", "status": "created",
            "courier_id": "smsa", "courier_name": "SMSA", "tracking_number": "AWB-1",
            "label_url": "https://labels.test/current.pdf"},
        "calls": [], "verified": [], "after_verify": None}

    async def owner(scoped, merchant, callback, **kwargs):
        assert scoped is db and merchant == OWNER
        return await callback(scoped)

    async def provider(scoped, merchant, method, path, **kwargs):
        assert scoped is db and merchant == OWNER
        assert method == "GET", "Phase one must never dispatch provider writes"
        state["calls"].append((method, path))
        if path == "/orders":
            return {"data": [{"id": "internal-1", "reference_id": NUMBER}]}
        if path == "/orders/internal-1":
            return {"data": deepcopy(state["order"])}
        if path == "/shipments":
            return {"data": deepcopy(state.get("rows", [state["shipment"]]))}
        if path == "/shipments/shipment-1":
            return {"data": deepcopy(state["shipment"])}
        if path.endswith("/tracking"):
            return {"data": {}}
        if path == "/store/info":
            return {"data": {"name": "Synthetic store"}}
        raise AssertionError(f"Unexpected provider read: {path}")

    async def document(scoped, merchant, number, snapshot):
        assert scoped is db and merchant == OWNER and number == NUMBER
        assert snapshot["ready"] is True
        state["verified"].append(deepcopy(snapshot))
        if state["after_verify"]:
            await state["after_verify"]()
        return {**snapshot, "label_url": "https://mezan.test/verified/capability.pdf",
                "document_sha256": "a" * 64}

    monkeypatch.setattr(shipping, "operational_owner", owner)
    monkeypatch.setattr(shipping, "call_salla", provider)
    monkeypatch.setattr(documents, "verify_and_store", document)
    yield db, state


@pytest.mark.parametrize("status", ["created", "draft", "pending", "creating", "processing",
    "shipped", "delivered", "cancelled", "unknown", "", None])
def test_only_positive_created_status_is_printable(status):
    row = {"id": "shipment-1", "status": status, "tracking_number": "AWB-1",
           "label_url": "https://labels.test/current.pdf"}
    assert shipping._snapshot(row)["ready"] is (status == "created")


@pytest.mark.asyncio
@pytest.mark.parametrize("carrier,code", [("SMSA", "smsa"), ("iMile", "imile")])
async def test_verified_document_is_published_after_final_reads(scenario, carrier, code):
    db, state = scenario
    state["shipment"].update(courier_name=carrier, courier_id=code)
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        f"{CURRENT_SHIPPING}.company_name": carrier, f"{CURRENT_SHIPPING}.company_code": code}})
    result = await shipping.refresh_shipping_label(db, OWNER, NUMBER)
    assert result["ready"] is True
    assert result["label_url"] == "https://mezan.test/verified/capability.pdf"
    assert result["document_sha256"] == "a" * 64
    assert len(state["verified"]) == 1
    assert state["verified"][0]["label_url"] == "https://labels.test/current.pdf"
    assert sum(path == "/shipments" for _, path in state["calls"]) == 2
    assert sum(path == "/orders/internal-1" for _, path in state["calls"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "pending", "creating", "processing"])
async def test_delayed_shipment_never_verifies_or_returns_ready(scenario, status):
    db, state = scenario
    state["shipment"]["status"] = status
    result = await shipping.refresh_shipping_label(db, OWNER, NUMBER)
    assert result["ready"] is False
    assert state["verified"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["shipped", "delivered", "unknown", "", None])
async def test_terminal_or_unknown_shipment_fails_closed_with_completed_order(scenario, status):
    db, state = scenario
    state["shipment"]["status"] = status
    with pytest.raises(shipping.ShippingLabelError) as error:
        await shipping.refresh_shipping_label(db, OWNER, NUMBER)
    assert error.value.code == "shipment_status_not_printable"
    assert state["verified"] == []
    workflow = await db.order_review_workflows.find_one({"user_id": OWNER})
    assert workflow["carrier_label_ready"] is False
    assert workflow["carrier_label_url"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["carrier", "shipment", "awb", "superseded", "multiple"])
async def test_readback_cannot_implicitly_adopt_different_canonical_identity(scenario, change):
    db, state = scenario
    updates = {
        "carrier": {f"{CURRENT_SHIPPING}.company_code": "imile"},
        "shipment": {f"{CURRENT_SHIPPING}.shipment_id": "newer-shipment"},
        "awb": {f"{CURRENT_SHIPPING}.tracking_number": "NEWER-AWB"},
        "superseded": {f"{CURRENT_SHIPPING}.superseded_shipment_ids": ["shipment-1"]},
        "multiple": {},
    }[change]
    if updates:
        await db.unified_orders.update_one({"user_id": OWNER}, {"$set": updates})
    if change == "multiple":
        state["rows"] = [deepcopy(state["shipment"]), {**state["shipment"], "id": "shipment-2"}]
        result = await shipping.refresh_shipping_label(db, OWNER, NUMBER)
        assert result["ready"] is False
    else:
        with pytest.raises(shipping.ShippingLabelError) as error:
            await shipping.refresh_shipping_label(db, OWNER, NUMBER)
        assert error.value.code == "shipping_snapshot_changed"
    assert state["verified"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["local_terminal", "provider_terminal", "label_url", "awb"])
async def test_document_completion_cannot_publish_after_newer_evidence(scenario, change):
    db, state = scenario
    async def race():
        if change == "local_terminal":
            await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {"order_status": "delivered"}})
        elif change == "provider_terminal":
            state["order"]["status"] = {"slug": "delivered"}
        elif change == "label_url":
            state["shipment"]["label_url"] = "https://labels.test/replacement.pdf"
        else:
            state["shipment"]["tracking_number"] = "REPLACEMENT-AWB"
    state["after_verify"] = race
    with pytest.raises(shipping.ShippingLabelError):
        await shipping.refresh_shipping_label(db, OWNER, NUMBER)
    assert len(state["verified"]) == 1
    workflow = await db.order_review_workflows.find_one({"user_id": OWNER})
    assert workflow["carrier_label_ready"] is False
    assert workflow["carrier_label_url"] is None


@pytest.mark.asyncio
async def test_artifact_route_readback_does_not_download_again(scenario):
    db, state = scenario
    result = await shipping.refresh_shipping_label(db, OWNER, NUMBER, verify_document=False)
    assert result["ready"] is True
    assert result["label_url"] == "https://labels.test/current.pdf"
    assert state["verified"] == []


@pytest.mark.asyncio
async def test_store_courier_document_requires_current_order_and_canonical_carrier(scenario):
    db, state = scenario
    state["order"]["shipping"] = {"company_name": "مندوب المتجر", "company_code": "0"}
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        CURRENT_SHIPPING: {"company_name": "مندوب المتجر", "company_code": "0"}}})
    result = await shipping.refresh_shipping_label(db, OWNER, NUMBER)
    assert result["ready"] is True and result["label_type"] == "store_courier"
    assert result["print_data"]["order_number"] == NUMBER
    assert result["print_data"]["barcode_value"] == NUMBER
    assert result["print_data"]["qr_code"].startswith("data:image/")
    assert state["verified"] == []
    assert not any(path.startswith("/shipments") for _, path in state["calls"])


@pytest.mark.asyncio
async def test_provider_store_courier_cannot_replace_canonical_external_carrier(scenario):
    db, state = scenario
    state["order"]["shipping"] = {"company_name": "مندوب المتجر", "company_code": "0"}
    with pytest.raises(shipping.ShippingLabelError) as error:
        await shipping.refresh_shipping_label(db, OWNER, NUMBER)
    assert error.value.code == "shipping_snapshot_changed"
    assert state["verified"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "shipped", "delivered"])
async def test_terminal_local_shipment_cannot_be_revived_by_store_document(scenario, status):
    db, state = scenario
    state["order"]["shipping"] = {"company_name": "مندوب المتجر", "company_code": "0"}
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        CURRENT_SHIPPING: {"company_name": "مندوب المتجر", "company_code": "0", "status": status}}})
    with pytest.raises(shipping.ShippingLabelError) as error:
        await shipping.refresh_shipping_label(db, OWNER, NUMBER)
    assert error.value.code == "shipment_status_not_printable"
    assert state["calls"] == [] and state["verified"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "shipped", "delivered", "in_progress"])
async def test_provider_order_must_be_completed_even_with_ready_shipment(scenario, status):
    db, state = scenario
    state["order"]["status"] = {"slug": status}
    with pytest.raises(shipping.ShippingLabelError) as error:
        await shipping.refresh_shipping_label(db, OWNER, NUMBER)
    assert error.value.code == "order_status_not_completed"
    assert state["verified"] == []
    assert not any(path.startswith("/shipments") for _, path in state["calls"])


@pytest.mark.asyncio
async def test_older_same_shipment_readback_cannot_authorize_document(scenario):
    db, state = scenario
    state["shipment"]["updated_at"] = "2026-10-01T09:00:00Z"
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        f"{CURRENT_SHIPPING}.shipment_updated_at": "2026-10-01T10:00:00Z"}})
    with pytest.raises(shipping.ShippingLabelError) as error:
        await shipping.refresh_shipping_label(db, OWNER, NUMBER)
    assert error.value.code == "shipping_snapshot_changed"
    assert state["verified"] == []


@pytest.mark.asyncio
async def test_invalid_generated_courier_identity_is_rejected(scenario, monkeypatch):
    db, state = scenario
    state["order"]["shipping"] = {"company_name": "مندوب المتجر", "company_code": "0"}
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        CURRENT_SHIPPING: {"company_name": "مندوب المتجر", "company_code": "0"}}})
    monkeypatch.setattr(shipping, "_store_courier_print_data", lambda *args: {
        "order_number": "another-order", "barcode_value": NUMBER, "qr_code": "data:image/svg+xml;base64,test"})
    with pytest.raises(shipping.ShippingLabelError) as error:
        await shipping.refresh_shipping_label(db, OWNER, NUMBER)
    assert error.value.code == "store_courier_document_invalid"
