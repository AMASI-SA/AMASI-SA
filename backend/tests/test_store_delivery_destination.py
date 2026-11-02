"""Destination coverage at the existing handover seam, using synthetic data only."""

import asyncio
import socket
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from salla_shipping import CURRENT_SHIPPING
from store_courier_domain import WORKFLOWS
from store_delivery_domain import normalize_city
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import (
    ASSIGNMENTS,
    EVENTS,
    ORDERS,
    SESSIONS,
    _order_city,
    make_store_delivery_handover_router,
)


CARRIER_NAME = "مندوب الرياض"
CARRIER_CODE = "synthetic-local-courier"
CURRENT_TIME = "2026-10-28T10:00:00+00:00"
OLDER_TIME = "2026-10-28T09:00:00+00:00"
NEWER_TIME = "2026-10-28T11:00:00+00:00"


def shipment(city="Riyadh", *, shipment_id="shipment-current", **changes):
    return {
        "id": shipment_id,
        "courier_name": CARRIER_NAME,
        "courier_id": CARRIER_CODE,
        "type": "shipment",
        "status": "created",
        "updated_at": CURRENT_TIME,
        "ship_to": {"city": city},
        **changes,
    }


def order_with_shipments(*rows, **changes):
    return {
        "user_id": "merchant-destination-test",
        "order_id": "salla-destination-test",
        "order_number": "9001001",
        "shipping_barcode": "SYNTHETIC-LABEL-1",
        "shipping_city": "جدة",
        "customer_city": "جدة",
        "shipping_company": CARRIER_NAME,
        "shipping_company_code": CARRIER_CODE,
        "salla_shipment_id": "shipment-current",
        CURRENT_SHIPPING: {
            "company_name": CARRIER_NAME,
            "company_code": CARRIER_CODE,
            "shipment_id": "shipment-current",
            "shipment_updated_at": CURRENT_TIME,
        },
        "raw_by_source": {"salla_direct": {"shipments": list(rows)}},
        **changes,
    }


def test_current_outbound_destination_overrides_stale_flat_and_customer_city():
    order = order_with_shipments(shipment())
    before = deepcopy(order)

    assert _order_city(order) == "Riyadh"
    assert order == before


@pytest.mark.parametrize(
    ("canonical_id", "row_id"),
    [("shipment-current", "shipment-current"), (501, "501"), ("501", 501)],
)
def test_current_shipment_id_pins_the_exact_row_not_a_newer_foreign_id(canonical_id, row_id):
    order = order_with_shipments(
        shipment("Dammam", shipment_id="different-shipment", updated_at=NEWER_TIME),
        shipment(shipment_id=row_id),
        salla_shipment_id="stale-root-id",
    )
    order[CURRENT_SHIPPING]["shipment_id"] = canonical_id

    assert _order_city(order) == "Riyadh"


def test_opaque_shipment_ids_are_not_coerced_to_equivalent_numbers():
    order = order_with_shipments(shipment(shipment_id=501))
    order[CURRENT_SHIPPING]["shipment_id"] = "0501"

    assert _order_city(order) == "جدة"


@pytest.mark.parametrize(
    "row_changes",
    [
        pytest.param({"status": "cancelled"}, id="cancelled"),
        pytest.param({"status": "canceled"}, id="canceled"),
        pytest.param({"status": "void"}, id="void"),
        pytest.param({"status": "deleted"}, id="deleted"),
        pytest.param({"status": "archived"}, id="archived"),
        pytest.param({"type": "return"}, id="return"),
        pytest.param({"type": "return_shipment"}, id="return-shipment"),
        pytest.param({"type": "reverse"}, id="reverse"),
        pytest.param({"courier_id": "different-carrier"}, id="wrong-carrier"),
        pytest.param({"updated_at": OLDER_TIME}, id="older-shipment-clock"),
    ],
)
def test_ineligible_shipment_cannot_override_the_legacy_city_even_with_matching_id(row_changes):
    assert _order_city(order_with_shipments(shipment(**row_changes))) == "جدة"


def test_superseded_shipment_is_not_reactivated_by_a_matching_current_id():
    order = order_with_shipments(shipment())
    order[CURRENT_SHIPPING]["superseded_shipment_ids"] = ["shipment-current"]

    assert _order_city(order) == "جدة"


def test_order_carrier_clock_does_not_age_a_current_shipment_destination():
    order = order_with_shipments(shipment())
    order[CURRENT_SHIPPING].update(
        carrier_updated_at=NEWER_TIME,
        provider_updated_at=NEWER_TIME,
        source_kind="order",
    )

    assert _order_city(order) == "Riyadh"


def without_shipment_identity(order):
    order.pop("salla_shipment_id", None)
    order[CURRENT_SHIPPING].pop("shipment_id", None)
    order[CURRENT_SHIPPING].pop("shipment_updated_at", None)
    return order


@pytest.mark.parametrize("carrier_on_row", [True, False], ids=["row-carrier", "enclosing-carrier"])
def test_one_address_only_row_with_current_carrier_context_can_supply_destination(carrier_on_row):
    row = {"ship_to": {"city": "Riyadh"}}
    if carrier_on_row:
        row.update(courier_name=CARRIER_NAME, courier_id=CARRIER_CODE)
    order = without_shipment_identity(order_with_shipments(row))
    if not carrier_on_row:
        order["raw_by_source"]["salla_direct"]["shipping"] = {
            "company": {"name": CARRIER_NAME, "id": CARRIER_CODE}
        }

    assert _order_city(order) == "Riyadh"


def test_explicit_current_null_does_not_revive_legacy_root_id():
    row = {"courier_name": CARRIER_NAME, "courier_id": CARRIER_CODE, "ship_to": {"city": "Riyadh"}}
    order = without_shipment_identity(order_with_shipments(row))
    order["salla_shipment_id"] = "obsolete-root-id"
    order[CURRENT_SHIPPING]["shipment_id"] = None

    assert _order_city(order) == "Riyadh"


def test_superseded_history_blocks_unpinned_address_only_fallback():
    row = {"courier_name": CARRIER_NAME, "courier_id": CARRIER_CODE, "ship_to": {"city": "Riyadh"}}
    order = without_shipment_identity(order_with_shipments(row))
    order[CURRENT_SHIPPING]["shipment_id"] = None
    order[CURRENT_SHIPPING]["superseded_shipment_ids"] = ["old-shipment"]

    assert _order_city(order) == "جدة"


@pytest.mark.parametrize("raw_carrier", [None, {"name": "Other carrier", "id": "other-carrier"}])
def test_address_only_row_requires_matching_current_carrier_context(raw_carrier):
    order = without_shipment_identity(order_with_shipments({"ship_to": {"city": "Riyadh"}}))
    if raw_carrier:
        order["raw_by_source"]["salla_direct"]["shipping"] = {"company": raw_carrier}

    assert _order_city(order) == "جدة"


@pytest.mark.parametrize("reverse_rows", [False, True])
def test_newest_matching_row_is_selected_before_reading_city(reverse_rows):
    rows = [shipment(updated_at=CURRENT_TIME), shipment(updated_at=NEWER_TIME, ship_to={})]
    if reverse_rows:
        rows.reverse()

    assert _order_city(order_with_shipments(*rows)) == "جدة"


@pytest.mark.parametrize("reverse_rows", [False, True])
def test_conflicting_current_rows_at_same_time_cannot_supply_stronger_city(reverse_rows):
    rows = [shipment("Riyadh"), shipment("Dammam")]
    if reverse_rows:
        rows.reverse()

    assert _order_city(order_with_shipments(*rows)) == "جدة"


@pytest.mark.parametrize(
    "city",
    [{"id": 123, "name": "Riyadh"}, {"label": "Riyadh"}, {"name": "", "label": "Riyadh"}],
)
def test_destination_city_object_uses_human_name_or_label(city):
    assert _order_city(order_with_shipments(shipment(city))) == "Riyadh"


@pytest.mark.parametrize(
    "city",
    [123, 123.5, "123", "123.5", "١٢٣", None, [], {"id": 123}, {"name": {"unexpected": "Riyadh"}}],
)
def test_destination_city_identifiers_and_malformed_objects_are_not_names(city):
    assert _order_city(order_with_shipments(shipment(city))) == "جدة"


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        ({"shipping_city": "الرياض", "shipping_address": {"city": "الدمام"}, "customer_city": "جدة"}, "الرياض"),
        ({"shipping_address": {"city": "الرياض"}, "customer_city": "جدة"}, "الرياض"),
        ({"shipping_city": "  ", "shipping_address": {"city": "الرياض"}, "customer_city": "جدة"}, "الرياض"),
        ({"shipping_city": 123, "shipping_address": {"city": {"label": "الرياض"}}, "customer_city": "جدة"}, "الرياض"),
        ({"customer_city": "جدة"}, "جدة"),
        ({}, ""),
    ],
)
def test_legacy_city_precedence_preserves_flat_shipping_then_address_then_customer(order, expected):
    assert _order_city(order) == expected


@pytest.mark.parametrize("name", ["Riyadh", " RIYADH ", "الرياض", "رياض"])
def test_existing_riyadh_aliases_are_unchanged(name):
    assert normalize_city(name) == "الرياض"


@pytest.fixture
def handover(monkeypatch):
    """Use memory-only collections and block network, preserving local IPC."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def reject_network(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            raise AssertionError("Destination tests must not contact any live provider")
        return original_connect(sock, address)

    def reject_network_ex(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            raise AssertionError("Destination tests must not contact any live provider")
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network_ex)
    db = AsyncMongoMockClient().destination_regression
    actor = {"id": "merchant-destination-test", "role": "owner", "name": "Synthetic owner"}
    driver = {
        "user_id": actor["id"],
        "id": "driver-destination-test",
        "name": "Synthetic driver",
        "city": "الرياض",
        "coverage_mode": "city",
        "status": "active",
        "delivery_fee": 23.50,
    }
    history = {
        "user_id": actor["id"],
        "id": "historical-assignment",
        "order_id": "historical-order",
        "order_number": "9001000",
        "driver_id": "historical-driver",
        "shipping_city_snapshot": "جدة",
        "delivery_fee_snapshot": 15.25,
        "status": "delivered",
        "active": False,
        "assigned_at": OLDER_TIME,
        "delivered_at": CURRENT_TIME,
    }

    async def seed(order):
        await db[STORE_DRIVERS].insert_one(deepcopy(driver))
        await db[ORDERS].insert_one(deepcopy(order))
        await db[WORKFLOWS].insert_one({
            "user_id": actor["id"],
            "order_number": order["order_number"],
            "carrier_label_type": "store_courier",
            "carrier_label_ready": True,
            "stage": "completed",
            "assembly_status": "completed",
        })
        await db[ASSIGNMENTS].insert_one(deepcopy(history))

    app = FastAPI()
    app.include_router(make_store_delivery_handover_router(db, lambda: actor))
    with TestClient(app) as client:
        def start(order):
            asyncio.run(seed(order))
            response = client.post("/store-delivery/handover/sessions", json={"driver_id": driver["id"]})
            assert response.status_code == 201, response.text
            return response.json()["id"]

        yield db, client, start, driver, history


@pytest.mark.parametrize("barcode", ["9001001", "SYNTHETIC-LABEL-1"], ids=["order-number", "barcode"])
def test_handover_accepts_current_destination_and_confirms_fee_and_history(handover, barcode):
    db, client, start, driver, history = handover
    order = order_with_shipments(shipment())
    session_id = start(order)
    base = f"/store-delivery/handover/sessions/{session_id}"

    scanned = client.post(f"{base}/scan", json={"barcode": barcode})
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["accepted"] is True, scanned.text
    assert scanned.json()["shipment"]["shipping_city_snapshot"] == "Riyadh"
    assert asyncio.run(db[ASSIGNMENTS].count_documents({"active": True})) == 0

    confirmed = client.post(f"{base}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["assigned_count"] == 1
    assignment = confirmed.json()["assignments"][0]
    assert assignment["driver_id"] == driver["id"]
    assert assignment["shipping_city_snapshot"] == "Riyadh"
    assert assignment["delivery_fee_snapshot"] == 23.50

    async def verify():
        assert await db[ASSIGNMENTS].count_documents({}) == 2
        assert await db[ASSIGNMENTS].find_one({"id": history["id"]}, {"_id": 0}) == history
        stored_order = await db[ORDERS].find_one({"order_id": order["order_id"]})
        assert stored_order["store_delivery_assignment_id"] == assignment["id"]
        assert stored_order["shipping_city"] == "جدة"
        assert stored_order["customer_city"] == "جدة"
        assert stored_order["raw_by_source"] == order["raw_by_source"]
        workflow = await db[WORKFLOWS].find_one({"order_number": order["order_number"]})
        assert workflow["store_delivery_assignment_id"] == assignment["id"]
        assert workflow["carrier_label_print_confirmed"] is True
        assert await db[EVENTS].count_documents({"event_type": "store_delivery_handover_confirmed"}) == 1
        await db[STORE_DRIVERS].update_one({"id": driver["id"]}, {"$set": {"delivery_fee": 99}})
        saved = await db[ASSIGNMENTS].find_one({"id": assignment["id"]})
        assert saved["delivery_fee_snapshot"] == 23.50

    asyncio.run(verify())


@pytest.mark.parametrize("barcode", ["9001001", "SYNTHETIC-LABEL-1"], ids=["order-number", "barcode"])
def test_handover_rejects_true_current_other_city_without_assignment(handover, barcode):
    db, client, start, driver, history = handover
    order = order_with_shipments(shipment("جدة"), shipping_city="الرياض", customer_city="الرياض")
    session_id = start(order)
    base = f"/store-delivery/handover/sessions/{session_id}"

    scanned = client.post(f"{base}/scan", json={"barcode": barcode})
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["accepted"] is False, scanned.text
    assert scanned.json()["code"] == "driver_city_mismatch"
    assert scanned.json()["shipping_city"] == "جدة"
    confirmed = client.post(f"{base}/confirm")
    assert confirmed.status_code == 409
    assert confirmed.json()["detail"]["code"] == "handover_session_empty"

    async def verify():
        assert await db[ASSIGNMENTS].count_documents({}) == 1
        assert await db[ASSIGNMENTS].find_one({"id": history["id"]}, {"_id": 0}) == history
        assert await db[EVENTS].count_documents({}) == 0
        stored_order = await db[ORDERS].find_one({"order_id": order["order_id"]}, {"_id": 0})
        assert stored_order == order
        workflow = await db[WORKFLOWS].find_one({"order_number": order["order_number"]})
        assert "store_delivery_assignment_id" not in workflow
        session = await db[SESSIONS].find_one({"id": session_id})
        assert session["accepted"] == []
        assert session["rejected"][0]["code"] == "driver_city_mismatch"

    asyncio.run(verify())
