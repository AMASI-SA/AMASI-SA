"""Offline tests for presentation diagnostics, not financial posting acceptance."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from operational_delivery_report_coverage import delivery_coverage_diagnostics

START = "2026-11-01T00:30:01+03:00"
END = "2026-11-01T02:00:00+03:00"


def snapshot():
    state = {
        "status": "active", "started_at": START,
        "openings": {"supplier-opening": {"amount": "100.00"}},
        "movements": [{"id": "existing-payment", "amount": "30.00"}],
        "engine": {
            "delivery_event_coverage": {"version": 1, "started_at": START, "as_of": END},
            "obligations": {"shipping:old": {"confirmed": "20.00"}},
            "cash_effects": {},
        },
    }
    projection = {"status": "active", "started_at": START, "as_of": END, "issues": []}
    return state, projection


def dependency(result, code):
    return next(row for row in result["dependencies"] if row["code"] == code)


def test_reader_support_and_noncash_posting_are_separate():
    state, projection = snapshot()
    result = delivery_coverage_diagnostics(state, projection)
    assert result["event_reader_supported"] is True
    assert dependency(result, "operational_delivery_coverage")["status"] == "ready"
    assert dependency(result, "operational_driver_noncash_posting")["status"] == "incomplete"
    assert "للطلبات القديمة والجديدة" in result["event_note"]


def test_presentation_never_changes_financial_facts_or_balances():
    state, projection = snapshot()
    original = deepcopy((state, projection))
    first = delivery_coverage_diagnostics(state, projection)
    assert first == delivery_coverage_diagnostics(state, projection)
    assert (state, projection) == original
    assert not any(key in first for key in ("amount", "balance", "cash_effects", "movements", "openings"))


def test_equivalent_explicit_utc_offset_is_accepted():
    state, projection = snapshot()
    state["engine"]["delivery_event_coverage"].update(
        started_at="2026-10-31T21:30:01Z", as_of="2026-10-31T23:00:00Z"
    )
    assert delivery_coverage_diagnostics(state, projection)["event_reader_supported"] is True


def test_aware_datetime_values_are_supported():
    state, projection = snapshot()
    state["engine"]["delivery_event_coverage"]["as_of"] = datetime(2026, 10, 31, 23, tzinfo=timezone.utc)
    assert delivery_coverage_diagnostics(state, projection)["event_reader_supported"] is True


@pytest.mark.parametrize("version", [True, False, "1", 1.0, 0, 2, None])
def test_only_explicit_supported_integer_version(version):
    state, projection = snapshot()
    state["engine"]["delivery_event_coverage"]["version"] = version
    result = delivery_coverage_diagnostics(state, projection)
    assert result["event_reader_supported"] is False
    assert dependency(result, "operational_delivery_coverage")["status"] == "incomplete"


@pytest.mark.parametrize("value", [None, "", "invalid", "2026-11-01", "2026-11-01T00:30:01", 1, True])
@pytest.mark.parametrize("field", ["started_at", "as_of"])
def test_incomplete_or_naive_marker_times_never_prove_coverage(field, value):
    state, projection = snapshot()
    state["engine"]["delivery_event_coverage"][field] = value
    assert delivery_coverage_diagnostics(state, projection)["event_reader_supported"] is False


@pytest.mark.parametrize("field,value", [
    ("started_at", "2026-11-01T00:00:00+03:00"),
    ("as_of", "2026-11-01T01:00:00+03:00"),
    ("as_of", "2026-11-01T03:00:00+03:00"),
])
def test_stale_or_different_window_cannot_mark_a_snapshot_ready(field, value):
    state, projection = snapshot()
    state["engine"]["delivery_event_coverage"][field] = value
    assert delivery_coverage_diagnostics(state, projection)["event_reader_supported"] is False


def test_saved_start_must_match_projection_start():
    state, projection = snapshot()
    state["started_at"] = "2026-11-01T00:00:00+03:00"
    assert delivery_coverage_diagnostics(state, projection)["event_reader_supported"] is False


def test_reverse_time_range_is_incomplete():
    state, projection = snapshot()
    projection["as_of"] = "2026-10-30T00:00:00+03:00"
    state["engine"]["delivery_event_coverage"]["as_of"] = projection["as_of"]
    assert delivery_coverage_diagnostics(state, projection)["event_reader_supported"] is False


@pytest.mark.parametrize("status", ["draft", "frozen", "accounting_active", "inactive", None])
def test_inactive_or_competing_authority_cannot_be_marked_ready(status):
    state, projection = snapshot()
    projection["status"] = status
    result = delivery_coverage_diagnostics(state, projection)
    assert result["event_reader_supported"] is False


@pytest.mark.parametrize("marker", [None, {}, [], "v1", {"version": 1}])
def test_missing_or_malformed_marker_remains_visible(marker):
    state, projection = snapshot()
    state["engine"]["delivery_event_coverage"] = marker
    result = delivery_coverage_diagnostics(state, projection)
    assert result["event_reader_supported"] is False
    assert dependency(result, "operational_delivery_coverage")["status"] != "ready"


@pytest.mark.parametrize("issues", [None, {}, "unknown", [{"code": "operational_delivery_timestamp_missing"}], [{"code": "supplier_invoice_currency_conflict"}]])
def test_existing_or_unknown_issues_are_not_hidden(issues):
    state, projection = snapshot()
    projection["issues"] = issues
    result = delivery_coverage_diagnostics(state, projection)
    assert result["event_reader_supported"] is True
    assert dependency(result, "operational_delivery_coverage")["status"] == "incomplete"


def test_no_personal_or_raw_source_details_are_echoed():
    state, projection = snapshot()
    state["owner_id"] = "PRIVATE_OWNER"
    projection["issues"] = [{"code": "x", "secret": "PRIVATE_SECRET"}]
    output = str(delivery_coverage_diagnostics(state, projection))
    assert "PRIVATE_OWNER" not in output and "PRIVATE_SECRET" not in output


@pytest.mark.parametrize("state,projection", [(None, {}), ({}, None), ([], {})])
def test_mapping_contract_is_explicit(state, projection):
    with pytest.raises(TypeError, match="operational_snapshot_mapping_required"):
        delivery_coverage_diagnostics(state, projection)
