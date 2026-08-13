import pytest

import order_engine.shipping_label_service as shipping
from fulfillment_carrier_label import _workflow_patch


@pytest.mark.asyncio
async def test_completed_status_waits_for_delayed_carrier_shipment(monkeypatch):
    calls = []
    responses = [
        [],
        [],
        [{"id": "991", "status": "creating", "courier_name": "iMile"}],
    ]

    async def fake_rows(db, user_id, internal_order_id, embedded):
        calls.append((user_id, internal_order_id))
        return responses.pop(0)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(shipping, "_shipment_rows", fake_rows)
    monkeypatch.setattr(shipping.asyncio, "sleep", no_sleep)

    result = await shipping._wait_for_active_outbound_shipments(
        None,
        "owner-1",
        "salla-order-1",
        None,
    )

    assert result[0]["id"] == "991"
    assert len(calls) == 3


def test_store_courier_qr_contains_order_number_only(monkeypatch):
    qr_values = []
    monkeypatch.setattr(
        shipping,
        "_qr_data_uri",
        lambda value: qr_values.append(value) or "data:image/svg+xml;base64,QR",
    )

    result = shipping._store_courier_print_data(
        "276628330",
        {
            "reference_id": "276628330",
            "customer": {"name": "عميل", "mobile": "0500000000"},
            "amounts": {"total": {"amount": 100, "currency": "SAR"}},
        },
        {
            "courier_name": "مندوب المتجر",
            "ship_to": {
                "name": "عميل",
                "phone": "0500000000",
                "city": "الرياض",
                "address_line": "حي العود",
            },
            "packages": [{"name": "منتج", "quantity": 1}],
        },
        {"name": "متجر ميزان"},
    )

    assert qr_values == ["276628330"]
    assert result["barcode_value"] == "276628330"
    assert result["order_number"] == "276628330"


def test_workflow_snapshot_distinguishes_store_courier_label():
    patch = _workflow_patch(
        {
            "ready": True,
            "order_status_completed": True,
            "label_type": "store_courier",
            "shipment_id": "store-1",
        },
        now="2026-08-13T10:00:00+00:00",
    )

    assert patch["salla_order_status"] == "completed"
    assert patch["carrier_label_status"] == "ready"
    assert patch["carrier_label_type"] == "store_courier"
    assert patch["carrier_label_url"] is None
