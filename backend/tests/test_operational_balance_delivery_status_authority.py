"""Current delivered authority is independent from optional delivery chronology."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

import operational_courier_transitions as transitions


CREATED = "2026-11-16T00:00:00+00:00"
DELIVERED = "2026-11-17T07:00:00+00:00"
VERSION = "2026-11-17T08:00:00+00:00"
END = datetime.fromisoformat("2026-11-18T00:00:00+00:00")


def source_pair():
    raw = {"id": "100", "reference_id": "10", "created_at": CREATED,
           "updated_at": VERSION, "status": {"slug": "delivered", "name": "تم التوصيل"}}
    source = {"salla_order_id": 100, "order_number": "10", "order_status": "delivered",
              "order_status_slug": "delivered", "g47_salla_snapshot": {"source_updated_at": VERSION}}
    return raw, source


def validate(raw, source):
    return transitions.validate_delivered_order_source(raw, source, END, created=CREATED)


@pytest.mark.parametrize("status", [
    "delivered", "تم التوصيل", {"slug": "delivered", "name": "تم التوصيل"},
    {"slug": "processing", "customized": {"code": "delivered", "label": "تم التوصيل"}},
])
def test_valid_authority_returns_native_version_and_effective_status_without_mutation(status):
    raw, source = source_pair()
    raw["status"] = deepcopy(status)
    before = deepcopy((raw, source))
    effective = status.get("customized", status) if isinstance(status, dict) else status
    assert validate(raw, source) == (VERSION, effective)
    assert (raw, source) == before


@pytest.mark.parametrize("status", [
    None, {}, "completed", "shipped", True,
    {"slug": "delivered", "name": "processing"},
    {"slug": "delivered", "customized": {"slug": "processing"}},
])
def test_non_delivered_or_conflicting_effective_status_is_rejected(status):
    raw, source = source_pair()
    raw["status"] = deepcopy(status)
    with pytest.raises(ValueError, match="^operational_delivery_order_shipment_status_conflict$"):
        validate(raw, source)


@pytest.mark.parametrize("container,field", [
    ("raw", "status_slug"), ("source", "order_status"), ("source", "order_status_slug"),
])
def test_present_status_alias_must_agree_with_current_delivered_status(container, field):
    raw, source = source_pair()
    (raw if container == "raw" else source)[field] = {"slug": "delivered", "name": "returned"}
    with pytest.raises(ValueError, match="^operational_delivery_order_shipment_status_conflict$"):
        validate(raw, source)


@pytest.mark.parametrize("field,value", [("salla_order_id", "another-order"), ("order_number", "another-number")])
def test_present_canonical_identity_alias_must_match(field, value):
    raw, source = source_pair()
    source[field] = value
    with pytest.raises(ValueError, match="^operational_delivery_order_identity_conflict$"):
        validate(raw, source)


@pytest.mark.parametrize("watermark", [None, [], False, "", ["invalid"], "invalid", 7])
def test_historical_reader_rejects_malformed_watermark_as_source_authority_failure(watermark):
    raw, source = source_pair()
    raw["status_changed_at"] = DELIVERED
    source["g47_salla_snapshot"] = watermark
    with pytest.raises(ValueError, match="^operational_delivery_canonical_source_stale$"):
        transitions.order_delivery_time(raw, source, [], END)


@pytest.mark.parametrize("flag", ["cancelled", "component_pending", "requires_authoritative_refresh"])
def test_watermark_flags_cannot_be_treated_as_optional_history(flag):
    raw, source = source_pair()
    source["g47_salla_snapshot"][flag] = True
    with pytest.raises(ValueError, match="^operational_delivery_canonical_source_stale$"):
        validate(raw, source)


def test_watermark_version_must_equal_native_version():
    raw, source = source_pair()
    source["g47_salla_snapshot"]["source_updated_at"] = DELIVERED
    with pytest.raises(ValueError, match="^operational_delivery_canonical_source_stale$"):
        validate(raw, source)


@pytest.mark.parametrize("version", [CREATED, END.isoformat(), "2026-11-17T11:00:00+03:00"])
def test_source_version_accepts_boundaries_and_equivalent_timezone(version):
    raw, source = source_pair()
    raw["updated_at"] = version
    source["g47_salla_snapshot"]["source_updated_at"] = datetime.fromisoformat(version).astimezone(timezone.utc).isoformat()
    assert validate(raw, source)[0] == version


@pytest.mark.parametrize("version", ["2026-11-15T23:59:59+00:00", "2026-11-18T00:00:01+00:00"])
def test_post_start_authority_rejects_version_outside_creation_and_read_window(version):
    raw, source = source_pair()
    raw["updated_at"] = source["g47_salla_snapshot"]["source_updated_at"] = version
    with pytest.raises(ValueError, match="^operational_delivery_canonical_source_stale$"):
        validate(raw, source)


@pytest.mark.parametrize("version,reason", [
    ("invalid-clock", "timestamp_invalid"), ("2026-11-17T08:00:00", "timestamp_timezone_missing"),
])
def test_invalid_native_version_remains_an_authority_failure(version, reason):
    raw, source = source_pair()
    raw["updated_at"] = version
    source.pop("g47_salla_snapshot")
    with pytest.raises(ValueError, match="^operational_delivery_" + reason + "$"):
        validate(raw, source)


def test_watermark_requiring_native_version_rejects_missing_clock():
    raw, source = source_pair()
    raw.pop("updated_at")
    with pytest.raises(ValueError, match="^operational_delivery_timestamp_missing$"):
        validate(raw, source)


@pytest.mark.parametrize("watermark", [{}, {"cancelled": False, "source_updated_at": None}])
def test_missing_version_stays_allowed_when_no_watermark_requires_it(watermark):
    raw, source = source_pair()
    raw.pop("updated_at")
    source["g47_salla_snapshot"] = watermark
    assert validate(raw, source) == (None, raw["status"])
    source.pop("g47_salla_snapshot")
    assert validate(raw, source) == (None, raw["status"])


@pytest.mark.parametrize("location", ["date.updated", "date_updated"])
def test_native_version_aliases_are_preserved(location):
    raw, source = source_pair()
    raw.pop("updated_at")
    if location == "date.updated":
        raw["date"] = {"date": CREATED, "updated": VERSION}
    else:
        raw[location] = VERSION
    assert validate(raw, source)[0] == VERSION


def test_source_authority_does_not_consume_optional_delivery_proof_or_clock():
    raw, source = source_pair()
    raw["status"]["changed_at"] = "invalid-optional-delivery-clock"
    source["salla_shipping_current"] = {transitions.WEBHOOK_PROOF: {"ambiguous": True}}
    before = deepcopy((raw, source))
    assert validate(raw, source) == (VERSION, raw["status"])
    assert (raw, source) == before
    with pytest.raises(ValueError, match="^operational_delivery_timestamp_invalid$"):
        transitions.order_delivery_time(raw, source, [], END)


def test_historical_reader_keeps_effective_child_date_and_missing_date_behavior():
    raw, source = source_pair()
    raw["status"] = {"slug": "processing", "changed_at": CREATED,
                     "customized": {"slug": "delivered", "changed_at": DELIVERED}}
    assert transitions.order_delivery_time(raw, source, [], END) == datetime.fromisoformat(DELIVERED)
    raw["status"]["customized"].pop("changed_at")
    with pytest.raises(ValueError, match="^operational_delivery_timestamp_missing$"):
        transitions.order_delivery_time(raw, source, [], END)
    assert transitions.order_delivery_time(raw, source, [], END, allow_undated=True) is None


def test_historical_reader_still_rejects_a_mismatched_saved_waybill_proof():
    raw, source = source_pair()
    raw["status_changed_at"] = DELIVERED
    source["salla_shipping_current"] = {transitions.WEBHOOK_PROOF: {"ambiguous": True}}
    with pytest.raises(ValueError, match="^operational_delivery_order_waybill_unproven$"):
        transitions.order_delivery_time(raw, source, [], END)
