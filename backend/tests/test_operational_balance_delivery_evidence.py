"""Post-start delivery requires its own immutable, owner-scoped event evidence."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from operational_delivery_evidence import resolve_delivery_event


START = "2026-11-01T00:00:00+03:00"
NOW = "2026-11-01T02:00:00+03:00"
DELIVERED = "2026-11-01T00:15:00+03:00"


def evidence(*, driver=True):
    raw = {"id": 100, "reference_id": 500, "date": "2026-10-31T10:00:00+03:00",
           "updated_at": "2026-11-01T01:30:00+03:00", "status": {"slug": "delivered"},
           "currency": "SAR", "amounts": {"total": {"amount": "300.00", "currency": "SAR"}},
           "payment": {"method": "cod"}}
    source = {"user_id": "owner", "raw_by_source": {"salla_direct": raw},
              "g47_salla_snapshot": {"requires_authoritative_refresh": False}}
    assignment = {"user_id": "owner", "id": "assignment-one", "order_id": "100", "order_number": "500",
                  "driver_id": "driver-one", "active": True, "status": "delivered", "delivered_at": DELIVERED}
    setup = {"user_id": "owner", "couriers": [{"courier_key": "carrier-one", "name": "Test carrier",
              "status": "active", "salla_carrier_keys": ["carrier-source", "Test carrier"],
              "confirmed_by": "owner", "confirmed_at": "2026-10-01T00:00:00+03:00"}]}
    if not driver:
        raw['status_changed_at'] = DELIVERED
        source["salla_shipping_current"] = {
            "source_kind": "shipment", "company_code": "carrier-source", "company_name": "Test carrier",
            "shipment_id": "shipment-one", "tracking_number": "TEST-AWB", "status": "delivered",
            "superseded_shipment_ids": [], "provider_updated_at": raw["updated_at"],
        }
        raw["shipping"] = {"company_code": "carrier-source", "company": "Test carrier",
                           "shipment_id": "shipment-one", "tracking_number": "TEST-AWB",
                           "status": "delivered", "delivered_at": DELIVERED}
        raw["shipments"] = [{"id": "shipment-one", "company": {"code": "carrier-source", "name": "Test carrier"},
                             "tracking_number": "TEST-AWB", "status": "delivered", "delivered_at": DELIVERED}]
    return raw, source, [assignment] if driver else [], setup


def resolve(values, **extra):
    raw, source, assignments, setup = values
    return resolve_delivery_event(raw, source=source, owner="owner", assignments=assignments, setup=setup,
                                  started_at=START, as_of=NOW, **extra)


def test_driver_event_uses_assignment_delivery_not_customer_order_age_or_update_clock():
    values = evidence()
    before = deepcopy(values)
    event = resolve(values)
    assert event == {
        "schema": "operational_delivery_event_v1", "id": "store-delivery:assignment-one",
        "order_id": "100", "order_number": "500", "order_created_at": "2026-10-31T07:00:00+00:00",
        "delivered_at": "2026-10-31T21:15:00+00:00", "party_type": "store_driver", "party_id": "driver-one",
        "currency": "SAR", "assignment_id": "assignment-one",
    }
    assert values == before
    assert not {"items", "payment", "bank_credit", "amount", "delivery_fee", "cod_amount"}.intersection(event)


def test_courier_event_uses_order_transition_and_exact_current_shipment_identity():
    values = evidence(driver=False)
    event = resolve(values)
    assert event["party_type"] == "courier" and event["party_id"] == "carrier-one"
    assert event["shipment_id"] == "shipment-one" and event["waybill"] == "TEST-AWB"
    assert event["source_carrier_key"] == "carrier-source"
    assert event["delivered_at"] == "2026-10-31T21:15:00+00:00"
    assert event["currency"] == "SAR"
    assert resolve(values) == event


@pytest.mark.parametrize("at", ["2026-10-31T23:59:59+03:00", "2026-10-31T23:59:59.999999+03:00"])
@pytest.mark.parametrize("driver", [True, False])
def test_prestart_events_belong_to_opening_boundary(at, driver):
    values = evidence(driver=driver)
    if driver:
        values[2][0]["delivered_at"] = at
    else:
        values[0]['status_changed_at'] = at
        values[0]["shipping"]["delivered_at"] = at
        values[0]["shipments"][0]["delivered_at"] = at
    assert resolve(values) is None


@pytest.mark.parametrize("driver", [True, False])
def test_exact_start_delivery_is_eligible_in_utc_and_riyadh(driver):
    values = evidence(driver=driver)
    at = "2026-10-31T21:00:00+00:00"
    if driver:
        values[2][0]["delivered_at"] = at
    else:
        values[0]['status_changed_at'] = at
        values[0]["shipping"]["delivered_at"] = at
        values[0]["shipments"][0]["delivered_at"] = at
    assert resolve(values)["delivered_at"] == at


@pytest.mark.parametrize("driver", [True, False])
def test_future_delivery_is_a_visible_issue(driver):
    values = evidence(driver=driver)
    if driver:
        values[2][0]["delivered_at"] = "2026-11-02T00:00:00+03:00"
    else:
        values[0]['status_changed_at'] = '2026-11-02T00:00:00+03:00'
        values[0]["shipping"]["delivered_at"] = "2026-11-02T00:00:00+03:00"
        values[0]["shipments"][0]["delivered_at"] = "2026-11-02T00:00:00+03:00"
    with pytest.raises(ValueError, match="^operational_delivery_event_in_future$"):
        resolve(values)


def test_missing_driver_event_time_never_uses_order_update_time():
    values = evidence()
    values[2][0].pop("delivered_at")
    with pytest.raises(ValueError, match="^operational_delivery_timestamp_missing$"):
        resolve(values)


def test_courier_update_clocks_and_current_status_cannot_replace_delivery_timestamp():
    values = evidence(driver=False)
    values[0].pop('status_changed_at')
    values[0]["shipping"].pop("delivered_at")
    values[0]["shipments"][0].pop("delivered_at")
    with pytest.raises(ValueError, match="^operational_delivery_timestamp_missing$"):
        resolve(values)


@pytest.mark.parametrize("at", ["2026-11-01T00:15:00", {"date": "2026-11-01T00:15:00"}])
def test_text_delivery_time_requires_a_timezone(at):
    values = evidence()
    values[2][0]["delivered_at"] = at
    with pytest.raises(ValueError, match="^operational_delivery_timestamp_timezone_missing$"):
        resolve(values)


@pytest.mark.parametrize("at", [
    "2026-10-31T21:15:00Z",
    {"date": "2026-11-01T00:15:00", "timezone": "Asia/Riyadh"},
    datetime(2026, 10, 31, 21, 15, tzinfo=timezone.utc),
    datetime(2026, 10, 31, 21, 15),  # Mongo BSON datetime is UTC.
])
def test_equivalent_native_timestamp_representations_have_one_event_identity(at):
    values = evidence()
    expected = resolve(values)
    values[2][0]["delivered_at"] = at
    assert resolve(values) == expected


def test_update_and_payment_review_changes_do_not_change_frozen_event_identity():
    values = evidence()
    event = resolve(values)
    values[0]["updated_at"] = "2026-11-01T01:59:00+03:00"
    values[2][0].update(payment_review_status="approved", updated_at=NOW)
    assert resolve(values) == event


@pytest.mark.parametrize("target", ["source", "assignment", "setup"])
def test_owner_scope_is_required_for_every_authoritative_record(target):
    values = evidence(driver=target != "setup")
    record = {"source": values[1], "assignment": values[2][0] if values[2] else {}, "setup": values[3]}[target]
    record["user_id"] = "another-owner"
    reason = "shipping_setup_owner_mismatch" if target == "setup" else "owner_mismatch"
    with pytest.raises(ValueError, match="^operational_delivery_" + reason + "$"):
        resolve(values)


@pytest.mark.parametrize("field", ["id", "reference_id"])
def test_raw_event_must_match_both_canonical_order_identifiers(field):
    values = evidence()
    values[1]["raw_by_source"]["salla_direct"] = deepcopy(values[0])
    values[1]["raw_by_source"]["salla_direct"][field] = 999
    with pytest.raises(ValueError, match="^operational_delivery_order_identity_conflict$"):
        resolve(values)


@pytest.mark.parametrize("raw_sources", [None, "invalid", {"salla_direct": []}])
def test_malformed_canonical_source_is_a_visible_issue(raw_sources):
    values = evidence()
    values[1]["raw_by_source"] = raw_sources
    with pytest.raises(ValueError, match="^operational_delivery_canonical_source_missing$"):
        resolve(values)


def test_stale_canonical_snapshot_cannot_supply_a_new_delivery():
    values = evidence()
    values[1]["g47_salla_snapshot"]["requires_authoritative_refresh"] = True
    with pytest.raises(ValueError, match="^operational_delivery_canonical_source_stale$"):
        resolve(values)


@pytest.mark.parametrize("field", ["order_id", "order_number"])
def test_assignment_cannot_match_only_one_order_alias(field):
    values = evidence()
    values[2][0][field] = "999"
    with pytest.raises(ValueError, match="^operational_delivery_assignment_order_conflict$"):
        resolve(values)


def test_two_active_assignments_do_not_select_an_arbitrary_executor():
    values = evidence()
    values[2].append({**values[2][0], "id": "assignment-two", "driver_id": "driver-two"})
    with pytest.raises(ValueError, match="^operational_delivery_assignment_ambiguous$"):
        resolve(values)


def test_assignment_id_reused_for_another_order_is_ambiguous():
    values = evidence()
    values[2].append({**values[2][0], "order_id": "200", "order_number": "600"})
    with pytest.raises(ValueError, match="^operational_delivery_assignment_ambiguous$"):
        resolve(values)


@pytest.mark.parametrize("field", ["id", "driver_id"])
def test_delivery_without_immutable_assignment_and_executor_identity_is_rejected(field):
    values = evidence()
    values[2][0].pop(field)
    with pytest.raises(ValueError, match="^operational_delivery_assignment_identity_missing$"):
        resolve(values)


def test_inactive_delivered_assignment_is_not_a_new_current_event():
    values = evidence()
    values[2][0]["active"] = False
    with pytest.raises(ValueError, match="^operational_delivery_assignment_inactive$"):
        resolve(values)


def test_pending_assignment_does_not_invent_a_delivery_from_an_order_update():
    values = evidence()
    values[0]["status"] = {"slug": "in_progress"}
    values[2][0].update(status="out_for_delivery", delivered_at=None)
    assert resolve(values) is None


def test_driver_assignment_is_executor_authority_over_courier_display_fields():
    values = evidence()
    values[0]["shipping"] = {"company": "An older carrier", "delivered_at": DELIVERED}
    values[1]["salla_shipping_current"] = {"company_code": "another-carrier", "status": "delivered"}
    assert resolve(values)["party_id"] == "driver-one"


@pytest.mark.parametrize("driver", [True, False])
def test_foreign_or_conflicting_customer_currency_is_explicitly_unsupported(driver):
    values = evidence(driver=driver)
    values[0]["amounts"]["total"]["currency"] = "QAR"
    with pytest.raises(ValueError, match="^operational_delivery_currency_unsupported$"):
        resolve(values)


def test_missing_currency_is_not_assumed_from_a_fee_contract():
    values = evidence()
    values[0].pop("currency")
    values[0]["amounts"]["total"].pop("currency")
    with pytest.raises(ValueError, match="^operational_delivery_currency_missing$"):
        resolve(values)


def test_old_foreign_currency_delivery_does_not_create_a_perpetual_new_event_issue():
    values = evidence()
    values[2][0]["delivered_at"] = "2026-10-31T23:59:59.999999+03:00"
    values[0]["currency"] = "QAR"
    assert resolve(values) is None


@pytest.mark.parametrize("driver", [True, False])
@pytest.mark.parametrize("status", ["cancelled", "refunded", "returned"])
def test_later_customer_order_status_does_not_erase_proved_earned_service(driver, status):
    values = evidence(driver=driver)
    event = resolve(values)
    values[0]["status"] = {"slug": status}
    assert resolve(values, frozen_event=event, recorded_fee=not driver) == event


@pytest.mark.parametrize("current", [None, "malformed", {}])
def test_delivered_label_requires_an_explicit_canonical_current_shipment(current):
    values = evidence(driver=False)
    values[1]["salla_shipping_current"] = current
    with pytest.raises(ValueError, match="^operational_delivery_current_shipment_required$"):
        resolve(values)


def test_root_and_nested_current_shipment_must_not_disagree():
    values = evidence(driver=False)
    values[0]["salla_shipping_current"] = {**values[1]["salla_shipping_current"], "shipment_id": "older-shipment"}
    with pytest.raises(ValueError, match="^operational_delivery_current_shipment_conflict$"):
        resolve(values)


@pytest.mark.parametrize("change", ["superseded", "returned", "root-alias"])
def test_current_shipment_is_not_superseded_returned_or_mislabeled(change):
    values = evidence(driver=False)
    expected = "shipment_inactive"
    if change == "superseded":
        values[1]["salla_shipping_current"]["superseded_shipment_ids"] = ["shipment-one"]
    elif change == "returned":
        values[1]["salla_shipping_current"]["type"] = "return"
    else:
        values[1]["tracking_number"] = "ANOTHER-AWB"
        expected = "current_shipment_conflict"
    with pytest.raises(ValueError, match="^operational_delivery_" + expected + "$"):
        resolve(values)


@pytest.mark.parametrize("change,reason", [
    ("waybill", "shipment_waybill_conflict"),
    ("carrier", "shipment_carrier_conflict"),
    ("returned", "shipment_inactive"),
    ("timestamp", "timestamp_ambiguous"),
])
def test_same_shipment_raw_evidence_must_agree(change, reason):
    values = evidence(driver=False)
    shipment = values[0]["shipments"][0]
    if change == "waybill":
        shipment["tracking_number"] = "ANOTHER-AWB"
    elif change == "carrier":
        shipment["company"]["code"] = "another-carrier"
    elif change == "returned":
        shipment["status"] = "returned"
    else:
        shipment["delivered_at"] = "2026-11-01T00:16:00+03:00"
        event = resolve(values)
        assert event['timestamp_source'] == 'order_status_transition'
        assert event['delivered_at'] == '2026-10-31T21:15:00+00:00'
        return
    with pytest.raises(ValueError, match="^operational_delivery_" + reason + "$"):
        resolve(values)


def test_older_shipments_cannot_supply_missing_current_delivery_time():
    values = evidence(driver=False)
    values[0]["shipping"].pop("delivered_at")
    values[0]["shipments"][0]["id"] = "older-shipment"
    with pytest.raises(ValueError, match="^operational_delivery_order_waybill_unproven$"):
        resolve(values)


def test_current_shipment_requires_same_identity_in_raw_source():
    values = evidence(driver=False)
    values[0].pop("shipments")
    values[0]["shipping"].pop("shipment_id")
    with pytest.raises(ValueError, match="^operational_delivery_shipment_source_missing$"):
        resolve(values)


@pytest.mark.parametrize("change", ["duplicate", "malformed-aliases", "unapproved"])
def test_carrier_mapping_requires_one_approved_exact_alias(change):
    values = evidence(driver=False)
    courier = values[3]["couriers"][0]
    if change == "duplicate":
        values[3]["couriers"].append({**courier, "courier_key": "another-carrier"})
    elif change == "malformed-aliases":
        courier["salla_carrier_keys"] = "prefix-carrier-source-suffix"
    else:
        courier.pop("confirmed_by")
    with pytest.raises(ValueError, match="^operational_delivery_courier_identity_ambiguous$"):
        resolve(values)


def test_carrier_code_and_name_cannot_map_to_different_parties():
    values = evidence(driver=False)
    courier = values[3]["couriers"][0]
    courier["salla_carrier_keys"] = ["carrier-source"]
    values[3]["couriers"].append({**courier, "courier_key": "other", "salla_carrier_keys": ["Test carrier"]})
    with pytest.raises(ValueError, match="^operational_delivery_courier_identity_conflict$"):
        resolve(values)


@pytest.mark.parametrize("at,reason", [
    ("2026-10-31T09:59:59+03:00", "timestamp_before_order"),
    ("2026-11-01", "timestamp_invalid"),
    ({"date": "2026-11-01T00:15:00", "timezone": "Not/AZone"}, "timestamp_invalid"),
])
def test_invalid_or_impossible_delivery_time_fails_closed(at, reason):
    values = evidence()
    values[2][0]["delivered_at"] = at
    with pytest.raises(ValueError, match="^operational_delivery_" + reason + "$"):
        resolve(values)
