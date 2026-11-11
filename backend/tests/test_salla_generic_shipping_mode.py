"""A transport mode must not replace the provider's actual carrier or AWB."""
from copy import deepcopy

import pytest

from salla_shipping import CURRENT_SHIPPING, accept_shipping, extract_shipping


def shipment(carrier, shipment_id, at, **fields):
    return {
        "id": shipment_id,
        "courier_name": carrier,
        "courier_id": f"carrier-{carrier}",
        "type": "shipment",
        "status": "created",
        "updated_at": at,
        **fields,
    }


def order(at, **fields):
    return {"id": "synthetic-order", "reference_id": "synthetic-reference",
            "updated_at": at, **fields}


def current_order(carrier):
    row = shipment(carrier, "old-awb", "2026-11-11T09:00:00Z",
                   tracking_number="OLD", label_url="https://carrier.invalid/old.pdf")
    current = accept_shipping({}, extract_shipping(order(
        "2026-11-11T09:00:00Z", shipping={"company_name": carrier}, shipments=[row],
    )))
    return {CURRENT_SHIPPING: current, "shipping_company": carrier,
            "salla_shipment_id": "old-awb"}


@pytest.mark.parametrize("mode", ["shipping", " SHIPPING ", {"name": "shipping"}])
@pytest.mark.parametrize("container", ["shipments", "shipment"])
def test_generic_mode_does_not_hide_named_outgoing_store_courier(mode, container):
    outgoing = shipment("مندوب الرياض", "courier-order", "2026-11-11T10:00:00Z",
                        meta={"app_id": 0})
    payload = order("2026-11-11T10:00:00Z", delivery_method=mode,
                    **{container: [outgoing] if container == "shipments" else outgoing})
    original = deepcopy(payload)

    observed = extract_shipping(payload)

    assert observed["company_name"] == "مندوب الرياض"
    assert observed["shipment_id"] == "courier-order"
    assert observed.get("label_url") is None
    assert observed.get("tracking_number") is None
    assert payload == original


@pytest.mark.parametrize("carrier", ["مندوب الرياض", "iMile", "SMSA"])
def test_newer_sparse_mode_preserves_known_carrier_and_existing_label(carrier):
    existing = current_order(carrier)
    original = deepcopy(existing)

    incoming = extract_shipping(order("2026-11-11T12:00:00Z", delivery_method="shipping"))

    assert accept_shipping(existing, incoming) is None
    assert existing == original


@pytest.mark.parametrize("rows", [[], [{"id": "cancelled", "courier_name": "مندوب الرياض",
                                       "status": "cancelled"}],
                                 [{"id": "return", "courier_name": "مندوب الرياض",
                                   "type": "return", "status": "created"}]])
def test_generic_mode_without_a_current_carrier_remains_unknown(rows):
    observed = extract_shipping(order("2026-11-11T12:00:00Z",
                                      delivery_method="shipping", shipments=rows))

    assert observed is None
    assert accept_shipping({}, observed) is None


@pytest.mark.parametrize("carrier", ["iMile", "SMSA"])
def test_explicit_external_carrier_keeps_its_official_label(carrier):
    outgoing = shipment(carrier, "official", "2026-11-11T10:00:00Z",
                        tracking_number="OFFICIAL", label_url="https://carrier.invalid/official.pdf")
    unrelated = shipment("مندوب الرياض", "other", "2026-11-11T11:00:00Z", meta={"app_id": 0})

    observed = extract_shipping(order(
        "2026-11-11T12:00:00Z", delivery_method="shipping",
        shipping={"company_name": carrier}, shipments=[unrelated, outgoing],
    ))

    assert observed["company_name"] == carrier
    assert observed["shipment_id"] == "official"
    assert observed["tracking_number"] == "OFFICIAL"
    assert observed["label_url"] == "https://carrier.invalid/official.pdf"


@pytest.mark.parametrize("carrier", ["iMile", "SMSA"])
def test_generic_mode_uses_latest_active_outgoing_label_by_provider_time(carrier):
    older = shipment(carrier, "99999", "2026-11-11T09:00:00Z",
                     tracking_number="OLD", label_url="https://carrier.invalid/old.pdf")
    newest = shipment(carrier, "1", "2026-11-11T10:00:00Z",
                      tracking_number="NEW", label_url="https://carrier.invalid/new.pdf")
    cancelled = shipment(carrier, "cancelled", "2026-11-11T12:00:00Z", status="cancelled")
    returned = shipment(carrier, "return", "2026-11-11T13:00:00Z", type="return")

    observed = extract_shipping(order(
        "2026-11-11T14:00:00Z", delivery_method="shipping",
        shipments=[older, cancelled, returned, newest],
    ))

    assert observed["company_name"] == carrier
    assert observed["shipment_id"] == "1"
    assert observed["tracking_number"] == "NEW"
    assert observed["label_url"] == "https://carrier.invalid/new.pdf"


@pytest.mark.parametrize("previous,current", [("iMile", "SMSA"), ("SMSA", "iMile")])
def test_explicit_carrier_change_cannot_reuse_previous_official_label(previous, current):
    existing = current_order(previous)
    old = shipment(previous, "old-awb", "2026-11-11T09:00:00Z",
                   tracking_number="OLD", label_url="https://carrier.invalid/old.pdf")

    incoming = extract_shipping(order(
        "2026-11-11T12:00:00Z", delivery_method="shipping",
        shipping={"company_name": current}, shipments=[old],
    ))
    accepted = accept_shipping(existing, incoming)

    assert accepted["company_name"] == current
    assert accepted["shipment_id"] is None
    assert accepted["tracking_number"] is None
    assert accepted["label_url"] is None
    assert "old-awb" in accepted["superseded_shipment_ids"]


@pytest.mark.parametrize("carrier", ["iMile", "SMSA"])
def test_newer_generic_order_envelope_cannot_restore_superseded_label(carrier):
    existing = current_order(carrier)
    newer = shipment(carrier, "new-awb", "2026-11-11T10:00:00Z",
                     tracking_number="NEW", label_url="https://carrier.invalid/new.pdf")
    current = accept_shipping(existing, extract_shipping(newer, event_name="order.shipment.created"))
    old = shipment(carrier, "old-awb", "2026-11-11T09:00:00Z",
                   tracking_number="OLD", label_url="https://carrier.invalid/old.pdf")

    incoming = extract_shipping(order("2026-11-11T12:00:00Z",
                                      delivery_method="shipping", shipments=[old]))
    accepted = accept_shipping({CURRENT_SHIPPING: current}, incoming)

    assert accepted["company_name"] == carrier
    assert accepted["shipment_id"] == "new-awb"
    assert accepted["tracking_number"] == "NEW"
    assert accepted["label_url"] == "https://carrier.invalid/new.pdf"
    assert "old-awb" in accepted["superseded_shipment_ids"]


def test_named_custom_delivery_method_keeps_its_identity():
    observed = extract_shipping(order("2026-11-11T12:00:00Z",
                                      delivery_method={"name": "مندوب الرياض"}))

    assert observed["company_name"] == "مندوب الرياض"
    assert observed["shipment_id"] is None
    assert observed.get("label_url") is None
