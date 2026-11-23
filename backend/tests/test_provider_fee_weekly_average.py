"""Source-backed weekly fees: money, temporal scope, acceptance and isolation."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_provider_statement_fees import (
    derive_statement_average_profile,
    select_statement_average_profile,
    statement_average_projection,
    statement_average_summary,
    statement_evidence_fingerprint as fingerprint,
)
from provider_fee_weekly_average import derive_weekly_statement_average


def sale(order, day, principal="100.00", variable="7.00", fixed="1.00", vat="1.20", **changes):
    net = Decimal(variable) + Decimal(fixed)
    total = net + Decimal(vat)
    return {"provider": "tabby", "order_number": order, "provider_order_id": None,
        "event_type": "sale", "event_reference": None, "event_date": day,
        "date_precision": "day" if day else None, "native_currency": "SAR",
        "principal_amount": principal, "fee_variable_net": variable, "fee_fixed_net": fixed,
        "fee_net": format(net, ".2f"), "fee_vat": vat, "fee_total": format(total, ".2f"),
        "transferred_amount": format(Decimal(principal) - total, ".2f"),
        "fee_vat_rate": "0.15", "fee_component_issues": [], "payment_method": "tabby",
        "plan_id": "Installments3Months", "installments": None, "merchant_account": None,
        "blockers": [], **changes}


def source(identity, rows, *, owner="owner", account=None, currency="SAR", provider="tabby",
           uploaded="2026-11-22T08:00:00+03:00", accepted_kind="summary"):
    rows = deepcopy(rows)
    for index, row in enumerate(rows):
        row.update(id="row:" + str(index), source_row=index, provider=provider,
                   merchant_account=account, native_currency=currency, statement_reference=identity)
        row["row_fingerprint"] = fingerprint({key: value for key, value in row.items()
            if key not in {"id", "source_row", "blockers"}})
    data = {"schema_version": "operational_statement_v1", "provider": provider,
            "merchant_account": account, "currency": currency, "statement_reference": identity,
            "statement_date": None, "rows": rows, "file_hash": fingerprint(identity)}
    data["fingerprint"] = fingerprint({**{key: data[key] for key in (
        "schema_version", "provider", "statement_reference", "merchant_account", "currency", "statement_date")},
        "rows": sorted(row["row_fingerprint"] for row in rows)})
    document = {"_id": identity, "id": identity, "owner_id": owner, "uploaded_at": uploaded,
                "content_hash": fingerprint(data), "statement": data}
    profile = derive_statement_average_profile({"owner_id": owner}, data,
        statement_id=identity, accepted_at=uploaded)
    assert profile is not None, "the fixture must represent a valid accepted statement"
    document["accepted_" + accepted_kind] = profile if accepted_kind == "profile" else statement_average_summary(profile)
    return document


def weekly(sources, **changes):
    return derive_weekly_statement_average("owner", sources, as_of=changes.pop("as_of", "2026-11-22"), **changes)


def test_seven_calendar_days_use_weighted_variable_and_separate_fixed_vat():
    document = source("one", [sale("old", "2026-11-14", "1000.00", "200.00", "1.00", "30.15"),
        sale("a", "2026-11-15", "100.00", "10.00", "1.00", "1.65"),
        sale("b", "2026-11-21", "300.00", "15.00", "2.00", "2.55")])
    before = deepcopy(document)
    result = weekly([document])
    assert result["available"] is True
    assert (result["window_start"], result["window_end"], result["sample_days"]) == ("2026-11-15", "2026-11-21", 2)
    assert result["stale_source"] is False
    profile = result["profile"]
    assert (profile["principal_total"], profile["fee_variable_net_total"], profile["fee_fixed_net_total"]) == ("400.00", "25.00", "3.00")
    assert profile["rate_percent"] == "6.25000000"  # 25 / 400; not the mean of 10% and 5%.
    assert profile["fixed_amount_per_sale"] == "1.50000000"
    projected = statement_average_projection(profile, "150.00", operations=1)
    assert (projected["variable"], projected["fixed"], projected["vat"], projected["fee"]) == (
        Decimal("9.38"), Decimal("1.50"), Decimal("1.63"), Decimal("12.51"))
    state = {"owner_id": "owner", "engine": {"provider_fee_inputs": {"average_profiles": [profile]}}}
    assert select_statement_average_profile(state, "tabby", "SAR") == profile
    assert document == before


def test_older_sales_do_not_replace_an_empty_current_calendar_week():
    document = source("older-week", [sale("older", "2026-11-02"), sale("last", "2026-11-10")])
    result = weekly([document])
    assert result["available"] is False and result["profile"] is None
    assert result["reason_code"] == "source_weekly_average_unavailable"
    assert (result["window_start"], result["window_end"], result["latest_complete_day"]) == ("2026-11-15", "2026-11-21", "2026-11-21")
    assert result["source_sale_rows"] == 0 and result["stale_source"] is False


def test_current_week_boundaries_do_not_move_when_its_last_days_have_no_sales():
    document = source("sparse-week", [sale("old", "2026-11-14", variable="25.00", vat="3.90"),
        sale("current", "2026-11-15")])
    result = weekly([document])
    assert result["available"] is True and result["source_sale_rows"] == 1
    assert (result["window_start"], result["window_end"]) == ("2026-11-15", "2026-11-21")
    assert result["basis"] == "last_seven_complete_calendar_days"
    assert result["profile"]["rate_percent"] == "7.00000000"


def test_empty_sources_retain_the_requested_calendar_week():
    result = weekly([])
    assert result["reason_code"] == "source_weekly_average_unavailable"
    assert (result["window_start"], result["window_end"]) == ("2026-11-15", "2026-11-21")


def test_a_current_week_source_accepted_before_week_end_is_still_eligible():
    document = source("early-upload", [sale("a", "2026-11-15")], uploaded="2026-11-16T08:00:00+03:00")
    result = weekly([document])
    assert result["available"] is True and result["source_sale_rows"] == 1
    assert (result["window_start"], result["window_end"]) == ("2026-11-15", "2026-11-21")
    assert result["profile"]["source_as_of"] == "2026-11-15"
    assert result["profile"]["source_date_basis"] == "sale_event_date"


def test_current_day_sales_are_excluded_even_in_an_accepted_statement():
    result = weekly([source("with-today", [sale("past", "2026-11-21"), sale("today", "2026-11-22")])])
    assert result["source_sale_rows"] == 1 and result["window_end"] == "2026-11-21"
    only_today = weekly([source("today", [sale("today", "2026-11-22")])])
    assert only_today["available"] is False


def test_timestamp_uses_riyadh_day_and_exact_acceptance_instant():
    document = source("late", [sale("a", "2026-11-21")], uploaded="2026-11-21T23:00:00Z")
    rejected = weekly([document], as_of="2026-11-21T22:00:00Z")  # Nov 22, 01:00 Riyadh.
    assert rejected["available"] is False
    accepted = weekly([document], as_of="2026-11-21T23:30:00Z")
    assert accepted["available"] is True and accepted["as_of_day"] == "2026-11-22"


def test_future_import_is_not_used_for_a_historical_estimate():
    past = source("past", [sale("a", "2026-11-20")], uploaded="2026-11-21T12:00:00+03:00")
    future = source("future", [sale("b", "2026-11-21")], uploaded="2026-11-23T12:00:00+03:00")
    result = weekly([past, future])
    assert result["available"] is True and result["source_statement_ids"] == ["past"]
    assert result["window_end"] == "2026-11-21"


def test_historical_window_can_use_later_accepted_fees_without_moving_its_dates():
    late = source("late-upload", [sale("a", "2026-11-10")], uploaded="2026-11-22T08:00:00+03:00")
    initial = weekly([late], as_of="2026-11-12")
    assert initial["reason_code"] == "source_weekly_average_unavailable"
    restated = weekly([late], as_of="2026-11-12", accepted_as_of="2026-11-22T09:00:00+03:00")
    assert restated["available"] is True and restated["source_sale_rows"] == 1
    assert (restated["window_start"], restated["window_end"]) == ("2026-11-05", "2026-11-11")
    assert restated["accepted_as_of"] == "2026-11-22T09:00:00+03:00"


def test_restated_window_still_excludes_imports_after_its_acceptance_cutoff():
    late = source("late-upload", [sale("a", "2026-11-10")], uploaded="2026-11-22T08:00:00+03:00")
    future = source("future-upload", [sale("b", "2026-11-11")], uploaded="2026-11-22T10:00:00+03:00")
    result = weekly([late, future], as_of="2026-11-12", accepted_as_of="2026-11-22T09:00:00+03:00")
    assert result["available"] is True and result["source_statement_ids"] == ["late-upload"]
    assert result["source_sale_rows"] == 1


@pytest.mark.parametrize("proof", ["profile", "summary"])
def test_accepts_only_a_matching_saved_source_proof(proof):
    document = source("one", [sale("a", "2026-11-21")], accepted_kind=proof)
    assert weekly([document])["available"] is True
    document["accepted_" + proof]["rate_percent"] = "99.00000000"
    assert weekly([document])["reason_code"] == "source_average_acceptance_changed"


def test_orphan_import_is_not_an_accepted_source():
    document = source("one", [sale("a", "2026-11-21")])
    del document["accepted_summary"]
    assert weekly([document])["reason_code"] == "source_average_not_accepted"


@pytest.mark.parametrize("change", ["content", "row", "statement", "owner", "id"])
def test_owner_and_all_source_hash_layers_are_checked(change):
    document = source("one", [sale("a", "2026-11-21")])
    if change == "content":
        document["content_hash"] = "f" * 64
    elif change == "row":
        document["statement"]["rows"][0]["fee_variable_net"] = "88.00"
        document["content_hash"] = fingerprint(document["statement"])
    elif change == "statement":
        document["statement"]["fingerprint"] = "e" * 64
        document["content_hash"] = fingerprint(document["statement"])
    elif change == "owner":
        document["owner_id"] = "someone-else"
    else:
        document["_id"] = "another-statement"
    assert weekly([document])["available"] is False


def test_multiple_accepted_sources_reexports_and_input_order_are_deterministic():
    first = source("first", [sale("a", "2026-11-16")])
    second = source("second", [sale("b", "2026-11-21")])
    reexport = source("reexport", [sale("a", "2026-11-16")])
    result = weekly([first, second, reexport, first])
    assert result["available"] is True and result["source_sale_rows"] == 2
    assert result["profile"]["principal_total"] == "200.00"
    assert result == weekly([reexport, second, first])


@pytest.mark.parametrize("referenced", [False, True])
def test_conflicting_economic_event_is_never_averaged(referenced):
    reference = {"event_reference": "capture-a"} if referenced else {}
    one = source("one", [sale("a", "2026-11-21", **reference)])
    changed = source("changed", [sale("a", "2026-11-21", variable="8.00", vat="1.35", **reference)])
    assert weekly([one, changed])["reason_code"] == "source_sale_conflict"


def test_distinct_proved_capture_references_can_share_an_order_and_day():
    document = source("two-captures", [sale("a", "2026-11-21", event_reference="capture-a"),
        sale("a", "2026-11-21", event_reference="capture-b")])
    assert weekly([document])["source_sale_rows"] == 2


def test_same_statement_id_with_changed_content_is_rejected():
    one = source("one", [sale("a", "2026-11-21")])
    changed = source("one", [sale("b", "2026-11-21")])
    assert weekly([one, changed])["reason_code"] == "source_statement_conflict"


def test_accounts_currencies_and_providers_do_not_cross():
    one = source("one", [sale("a", "2026-11-21")], account="account-a")
    two = source("two", [sale("b", "2026-11-21")], account="account-b")
    foreign = source("foreign", [sale("c", "2026-11-21")], account="account-a", currency="QAR")
    other = source("tamara", [sale("d", "2026-11-21")], provider="tamara")
    assert weekly([one, two])["reason_code"] == "source_account_ambiguous"
    result = weekly([one, two, foreign, other], merchant_account="account-a")
    assert result["source_statement_ids"] == ["one"]
    assert result["profile"]["native_currency"] == "SAR"
    assert weekly([foreign])["available"] is False
    assert weekly([foreign], native_currency="QAR")["available"] is True
    assert weekly([other])["available"] is False


def test_undated_sale_cannot_disappear_from_the_weekly_sample():
    document = source("undated", [sale("a", "2026-11-21"), sale("unknown", None)])
    assert weekly([document])["reason_code"] == "source_sale_date_unavailable"


def test_refunds_and_payout_fees_are_excluded_not_net_off_against_sale_ratio():
    document = source("mixed", [sale("a", "2026-11-21"),
        sale("refund", "2026-11-21", event_type="refund"),
        sale("payout", "2026-11-21", event_type="settlement_fee")])
    result = weekly([document])
    assert result["source_sale_rows"] == 1
    assert result["profile"]["rate_percent"] == "7.00000000"


def test_missing_vat_is_preserved_and_cannot_become_a_complete_fee():
    document = source("unknown-tax", [sale("a", "2026-11-21", fee_vat_rate=None)])
    profile = weekly([document])["profile"]
    assert profile["vat_rate"] is None
    assert statement_average_projection(profile, "100.00", operations=1)["fee"] is None


def test_source_and_row_budgets_never_silently_truncate():
    document = source("one", [sale("a", "2026-11-21")])
    assert weekly([document] * 33)["available"] is False
    oversized = deepcopy(document)
    oversized["statement"]["rows"] *= 10001
    assert weekly([oversized])["reason_code"] == "source_row_limit"


@pytest.mark.parametrize("value", [None, {}, [{"statement": []}], [{"statement": {"rows": None}}]])
def test_invalid_shapes_remain_unavailable(value):
    assert weekly(value)["available"] is False
