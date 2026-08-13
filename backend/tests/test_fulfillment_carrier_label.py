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

    async def order_without_shipments(*_args, **_kwargs):
        return {"data": {"shipments": []}}

    monkeypatch.setattr(shipping, "_shipment_rows", fake_rows)
    monkeypatch.setattr(shipping.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(shipping, "call_salla", order_without_shipments)

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


@pytest.mark.asyncio
async def test_shipment_list_falls_back_to_full_order_embedded_label(monkeypatch):
    async def missing_scope(*_args, **_kwargs):
        raise shipping.SallaError("shipping.read missing", status_code=401)

    monkeypatch.setattr(shipping, "call_salla", missing_scope)
    rows = await shipping._shipment_rows(
        None,
        "owner-1",
        "order-1",
        [{
            "id": "ship-1",
            "status": "created",
            "tracking_number": "IM123",
            "label": {"url": "https://carrier.example/label.pdf"},
        }],
    )

    assert shipping._snapshot(rows[0])["ready"] is True
    assert shipping._snapshot(rows[0])["label_url"].endswith("label.pdf")


@pytest.mark.asyncio
async def test_poll_reads_delayed_label_from_order_details_without_shipping_scope(monkeypatch):
    calls = 0

    async def salla(_db, _user, _method, path, **_kwargs):
        nonlocal calls
        if path.startswith("/shipments/"):
            raise shipping.SallaError("shipping.read missing", status_code=401)
        assert path == "/orders/order-1"
        calls += 1
        return {"data": {"shipments": [{
            "id": "ship-1",
            "status": "created",
            "tracking_number": "IM123",
            "label": {"url": "https://carrier.example/imile.pdf"},
        }]}}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(shipping, "call_salla", salla)
    monkeypatch.setattr(shipping.asyncio, "sleep", no_sleep)
    result = await shipping._poll_shipment(
        None,
        "owner-1",
        "ship-1",
        {"id": "ship-1", "status": "creating"},
        attempts=2,
        internal_order_id="order-1",
    )

    assert calls == 1
    assert shipping._snapshot(result)["ready"] is True
    assert shipping._snapshot(result)["label_url"].endswith("imile.pdf")
