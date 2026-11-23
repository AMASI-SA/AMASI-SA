"""Weekly Tabby estimates preserve paid proof, precedence and report arithmetic."""
from copy import deepcopy
from decimal import Decimal

import pytest
from mongomock_motor import AsyncMongoMockClient

from dashboard_ads_costs import DashboardAdsCostCollector
from dashboard_payment_fees import AVERAGE_BASIS, BASIS, dashboard_fee_breakdown, load_dashboard_fee_context
from legacy_dashboard_reader import dashboard as canonical_dashboard
from provider_fee_weekly_average import derive_weekly_statement_average
from provider_fee_weekly_sources import load_weekly_statement_averages, STATES, STATEMENTS
from operational_provider_statement_fees import derive_statement_average_profile, statement_average_summary
from tests.test_dashboard_dated_payment_fees import policy, order
from tests.test_provider_fee_weekly_average import sale, source
from tests.test_dashboard_operational_statement_fees import (
    OWNER as ACTUAL_OWNER, paid_state, accepted_event, accept, project,
)


def tabby_policies():
    return [policy(id="tabby-three", provider="tabby", plan_id="Installments3Months"),
            policy(id="tabby-five", provider="tabby", plan_id="Installments5Months",
                components=[{"id": "fee", "percentage": "9.74", "fixed_amount": "1.00",
                    "vat_treatment": "exclusive", "vat_rate": "15", "vat_basis": "unrounded_fee",
                    "refund_fee_treatment": "retain"}])]


def accepted_document():
    # The older 20% event must not enter the week. Within it: 25 / 400 =6.25%,
    # fixed mean1.50; on150 the independently calculated total is12.51.
    return source("accepted-week", [sale("old", "2026-11-14", "1000.00", "200.00", "1.00", "30.15"),
        sale("a", "2026-11-15", "100.00", "10.00", "1.00", "1.65"),
        sale("b", "2026-11-21", "300.00", "15.00", "2.00", "2.55")])


def weekly_context(**changes):
    average = derive_weekly_statement_average("owner", [accepted_document()], as_of="2026-11-22")
    return {"complete": True, "source_mode": "mz2_policies", "policies": tabby_policies(),
        "statement_plans": {}, "as_of": "2026-11-22", "weekly_statement_averages": {"SAR": average}, **changes}


def paid_order(**changes):
    raw = {"created_at": "2026-11-22T08:00:00+03:00", "updated_at": "2026-11-22T08:01:00+03:00",
        "currency": "SAR", "payment_method": "tabby_installment",
        "payment_methods": [{"payment_method": "tabby_installment", "amount": 150,
                             "transaction_reference": "native-reference"}],
        "payment_actions": {"refund_action": {"paid_amount": {"amount": 150, "currency": "SAR"},
                                                "refund_amount": {"amount": 0, "currency": "SAR"}}}}
    return order(order_number="paid-tabby", payment_method="tabby_installment", total_amount=150,
                 order_date="2026-11-22", _dashboard_fee_source=raw, **changes)


def captured_order(proof, captured_at):
    current = paid_order()
    if proof == "capture":
        payment = {"captures": [{"id": "native-reference", "amount": 150,
                                 "currency": "SAR", "captured_at": captured_at}]}
    elif proof == "paid":
        payment = {"status": "paid", "paid_amount": 150, "paid_at": captured_at}
    else:
        payment = {"paid_at": captured_at}
    current["_dashboard_fee_source"]["payment"] = payment
    return current


async def seed_accepted(db, document=None, owner="owner"):
    document = deepcopy(document or accepted_document())
    proof = document.pop("accepted_summary")
    document["metadata"] = {"row_count": len(document["statement"]["rows"])}
    await db[STATEMENTS].insert_one(document)
    state = {"_id": owner, "owner_id": owner, "status": "active", "revision": 1,
        "started_at": "2026-11-01T00:00:00+03:00",
        "provider_statement_reconciliation": {"statement_summaries": {
            document["id"]: {"statement_average": proof}}, "average_profiles": {}}}
    await db[STATES].insert_one(state)
    return state


@pytest.mark.asyncio
async def test_paid_unknown_plan_uses_weighted_week_and_matches_ad_costs_without_mutation():
    orders, context = [paid_order()], weekly_context()
    before = deepcopy((orders, context))
    result = await dashboard_fee_breakdown(orders, [], context)
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 12.51
    assert result["payment_fees_estimated"] is True
    row = result["payment_breakdown"][0]
    assert row["fee_sources"] == ["weekly_statement_average"]
    assert row["fees_estimated"] is True and row["estimated_orders_count"] == 1
    assert (row["base_commission"], row["vat_amount"]) == (10.88, 1.63)
    metadata = row["fee_estimate_sources"][0]
    assert (metadata["window_start"], metadata["window_end"], metadata["eligible_sale_rows"]) == (
        "2026-11-15", "2026-11-21", 2)
    collector = DashboardAdsCostCollector()
    collector.capture_settings({"payment_methods": [], "shipping_companies": [{"name": "carrier", "cost_per_order": 0}]})
    collector.capture_fee_context(context)
    summary = await collector.summary(orders, scope_complete=True)
    assert summary["payment_fees_sar"] == 12.51 and summary["payment_fees_estimated"] is True
    assert summary["payment_fees_basis"] == result["basis"]
    assert (orders, context) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("payment", [{"status": "paid"}, {"paid_amount": 150},
                                    {"paid_at": "2026-11-22T08:01:00+03:00"}])
async def test_partial_capture_metadata_does_not_hide_proven_full_paid_average(payment):
    current = paid_order()
    current["_dashboard_fee_source"]["payment"] = payment
    result = await dashboard_fee_breakdown([current], [], weekly_context())
    assert result["payment_fees_complete"] is True and result["total_payment_fees"] == 12.51


@pytest.mark.asyncio
async def test_partial_capture_metadata_never_hides_future_or_conflicting_observations():
    for payment in ({"paid_at": "2026-11-23T08:01:00+03:00"}, {"status": "paid", "paid_amount": 100}):
        current = paid_order()
        current["_dashboard_fee_source"]["payment"] = payment
        result = await dashboard_fee_breakdown([current], [], weekly_context())
        assert result["payment_fees_complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_proof", ["capture", "paid", "paid_at"])
@pytest.mark.parametrize("captured_at", ["2026-11-23T09:00:00+03:00", "2026-11-24T12:00:00+03:00"])
async def test_historical_tabby_capture_uses_known_evidence_without_moving_week(capture_proof, captured_at):
    current = captured_order(capture_proof, captured_at)
    context = weekly_context(evidence_as_of="2026-11-24T12:00:00+03:00")
    original = deepcopy((current, context))

    result = await dashboard_fee_breakdown([current], [], context)

    assert result["payment_fees_complete"] is True, result["fee_review_reasons"]
    assert result["total_payment_fees"] == 12.51
    row = result["payment_breakdown"][0]
    assert row["fee_sources"] == ["weekly_statement_average"]
    assert row["estimated_orders_count"] == 1
    assert (row["fee_estimate_sources"][0]["window_start"],
            row["fee_estimate_sources"][0]["window_end"]) == ("2026-11-15", "2026-11-21")
    assert (current, context) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_proof", ["capture", "paid", "paid_at"])
@pytest.mark.parametrize("captured_at", [
    "2026-11-24T12:00:00.000001+03:00", "2026-11-25T09:00:00+03:00", "2026-11-22T07:59:59+03:00",
])
async def test_historical_tabby_capture_rejects_events_outside_evidence_interval(capture_proof, captured_at):
    current = captured_order(capture_proof, captured_at)
    result = await dashboard_fee_breakdown(
        [current], [], weekly_context(evidence_as_of="2026-11-24T12:00:00+03:00"))
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"]
    assert not result.get("payment_fees_estimated")


@pytest.mark.asyncio
async def test_historical_tabby_known_plan_uses_capture_date_not_report_or_evidence_date():
    current = captured_order("capture", "2026-11-23T09:00:00+03:00")
    current["_dashboard_fee_source"]["payment"]["plan_id"] = "Installments3Months"
    current["_dashboard_fee_source"]["updated_at"] = "2026-11-24T10:00:00+03:00"
    old, other_plan = tabby_policies()
    old["effective_to"] = "2026-11-22"
    capture_terms = {**deepcopy(old), "id": "capture-day", "effective_from": "2026-11-23",
                     "effective_to": "2026-11-23"}
    capture_terms["components"][0].update(percentage="10", fixed_amount="2")
    latest = {**deepcopy(capture_terms), "id": "evidence-day", "effective_from": "2026-11-24", "effective_to": None}
    latest["components"][0]["percentage"] = "20"
    context = weekly_context(evidence_as_of="2026-11-24T12:00:00+03:00",
                             policies=[old, other_plan, capture_terms, latest])

    result = await dashboard_fee_breakdown([current], [], context)

    assert result["payment_fees_complete"] is True, result["fee_review_reasons"]
    assert result["total_payment_fees"] == 19.55  # (150 * 10% + 2) * 1.15.
    assert result["payment_breakdown"][0]["fee_sources"] == ["dated_provider_policy"]
    assert not result.get("payment_fees_estimated")


def refunded_order(refunded_at):
    current = captured_order("capture", "2026-11-22T09:00:00+03:00")
    payment = current["_dashboard_fee_source"]["payment"]
    payment.update(plan_id="Installments3Months", refunds=[{
        "id": "executed-refund", "amount": 50, "currency": "SAR", "status": "executed",
        "refunded_at": refunded_at,
    }])
    current["actual_refund_amount"] = 50
    current["_dashboard_fee_source"]["payment_actions"]["refund_action"]["refund_amount"]["amount"] = 50
    return current


@pytest.mark.asyncio
async def test_historical_tabby_executed_refund_uses_its_dated_fee_before_evidence_cutoff():
    current = refunded_order("2026-11-23T10:00:00+03:00")
    refund_terms = policy(id="refund-day", provider="tabby", plan_id="Installments3Months",
                          event_type="refund", effective_from="2026-11-23", effective_to="2026-11-23")
    refund_terms["components"][0].update(percentage="0", fixed_amount="2")
    later_terms = {**deepcopy(refund_terms), "id": "later-refund", "effective_from": "2026-11-24", "effective_to": None}
    later_terms["components"][0]["fixed_amount"] = "9"
    context = weekly_context(evidence_as_of="2026-11-24T12:00:00+03:00",
                             policies=[*tabby_policies(), refund_terms, later_terms])
    original = deepcopy((current, context))

    result = await dashboard_fee_breakdown([current], [], context)

    assert result["payment_fees_complete"] is True, result["fee_review_reasons"]
    assert result["total_payment_fees"] == 16.09  # Capture net11.99 + VAT1.80 + refund fee2.30.
    assert result["payment_breakdown"][0]["fee_sources"] == ["dated_provider_policy"]
    assert (current, context) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("refunded_at", [
    "2026-11-24T12:00:00.000001+03:00", "2026-11-25T09:00:00+03:00", "2026-11-22T07:59:59+03:00",
])
async def test_historical_tabby_refund_rejects_events_outside_evidence_interval(refunded_at):
    result = await dashboard_fee_breakdown(
        [refunded_order(refunded_at)], [], weekly_context(evidence_as_of="2026-11-24T12:00:00+03:00"))
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_refund_timestamp_conflict"]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect,reason", [
    ("amount", "provider_capture_original_basis_required"),
    ("currency", "provider_capture_evidence_incomplete"),
    ("duplicate_identity", "provider_capture_conflict"),
    ("provider", "provider_payment_method_conflict"),
])
async def test_historical_tabby_evidence_clock_preserves_payment_conflicts(defect, reason):
    current = captured_order("capture", "2026-11-23T09:00:00+03:00")
    payment = current["_dashboard_fee_source"]["payment"]
    if defect == "amount":
        payment["captures"][0]["amount"] = 149
    elif defect == "currency":
        payment["captures"][0]["currency"] = "USD"
    elif defect == "duplicate_identity":
        payment["captures"].append({**payment["captures"][0], "amount": 149})
    else:
        payment["method"] = "tamara"
    result = await dashboard_fee_breakdown(
        [current], [], weekly_context(evidence_as_of="2026-11-24T12:00:00+03:00"))
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == [reason]
    assert not result.get("payment_fees_estimated")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,method", [("emkan", "emkan_installment"), ("tamara", "tamara"), ("salla", "mada")])
async def test_tabby_evidence_clock_keeps_other_provider_report_cutoffs(provider, method):
    current = captured_order("capture", "2026-11-23T09:00:00+03:00")
    current["payment_method"] = method
    raw = current["_dashboard_fee_source"]
    raw["payment_method"] = raw["payment_methods"][0]["payment_method"] = method
    context = weekly_context(evidence_as_of="2026-11-24T12:00:00+03:00", policies=[policy(provider=provider)])
    result = await dashboard_fee_breakdown([current], [], context)
    assert result["payment_fees_complete"] is False
    assert result["fee_review_reasons"] == ["provider_fee_transaction_date_invalid"]


@pytest.mark.asyncio
async def test_actual_and_known_plan_supersede_average_and_mixed_count_is_precise():
    actual = paid_order(payment_fee_status="actual", actual_payment_fee=1, actual_payment_vat=.15)
    actual["order_number"] = "actual"
    known = paid_order()
    known["order_number"] = "known"
    known["_dashboard_fee_source"]["payment"] = {"plan_id": "Installments3Months"}
    result = await dashboard_fee_breakdown([actual, known, paid_order()], [], weekly_context())
    known_result = await dashboard_fee_breakdown([known], [], weekly_context())
    assert result["total_payment_fees"] == round(1.15 + known_result["total_payment_fees"] + 12.51, 2)
    row = result["payment_breakdown"][0]
    assert row["estimated_orders_count"] == 1 and row["orders_count"] == 3
    assert set(row["fee_sources"]) == {"actual_statement", "dated_provider_policy", "weekly_statement_average"}
    replaced = await dashboard_fee_breakdown([actual], [], weekly_context())
    assert replaced["total_payment_fees"] == 1.15
    assert not replaced.get("payment_fees_estimated")
    assert not replaced["payment_breakdown"][0].get("fees_estimated")
    assert not known_result.get("payment_fees_estimated")


@pytest.mark.asyncio
async def test_empty_week_uses_exact_operational_profile_and_marks_its_own_period():
    document = source("operational-period", [sale("earlier", "2026-11-10", variable="10.00", vat="1.65")])
    profile = derive_statement_average_profile({"owner_id": "owner"}, document["statement"],
        statement_id=document["id"], accepted_at=document["uploaded_at"])
    fallback = {"available": True, "profile": profile, "source_summary": statement_average_summary(profile),
        "basis": "operational_balance_average", "period_start": "2026-11-01T00:00:00+03:00",
        "period_end": "2026-11-22T08:00:00+03:00"}
    context = weekly_context(weekly_statement_averages={"SAR": {
        "available": False, "reason_code": "source_weekly_average_unavailable", "operational_fallback": fallback}})
    result = await dashboard_fee_breakdown([paid_order()], [], context)
    assert result["payment_fees_complete"] is True
    assert result["total_payment_fees"] == 18.40
    assert result["payment_breakdown"][0]["fee_sources"] == ["operational_balance_average"]
    metadata = result["payment_fee_estimate_sources"][0]
    assert metadata["rate_percent"] == profile["rate_percent"] == "10.00000000"
    assert metadata["basis"] == "operational_balance_average"
    assert (metadata["period_start"], metadata["period_end"]) == (fallback["period_start"], fallback["period_end"])
    assert "window_start" not in metadata and metadata["source_as_of"] == "2026-11-10"


@pytest.mark.asyncio
@pytest.mark.parametrize("later_capture", [False, True])
async def test_historical_report_reprices_after_later_uploaded_statement_changes_average(later_capture):
    db = AsyncMongoMockClient().weekly_history
    old = source("old-accepted", [sale("first", "2026-11-14", variable="10.00", vat="1.65")],
        uploaded="2026-11-21T12:00:00+03:00")
    await seed_accepted(db, old)
    await db.settings.insert_one({"user_id": "owner", "payment_methods": [],
        "shipping_companies": [{"name": "carrier", "cost_per_order": 0}], "net_sales_config": {}})
    current = paid_order()
    raw = current.pop("_dashboard_fee_source")
    raw["created_at"] = raw["updated_at"] = "2026-11-15T08:00:00+03:00"
    if later_capture:
        raw["payment"] = {"status": "paid", "paid_amount": 150, "paid_at": "2026-11-16T09:00:00+03:00"}
    current.update(user_id="owner", total_product_cost=10, order_date="2026-11-15", raw_by_source={"salla_direct": raw})
    await db.unified_orders.insert_one(current)
    orders_before = await db.unified_orders.find({}).to_list(10)
    setup = {"complete": True, "source_mode": "mz2_policies", "policies": tabby_policies(),
             "evidence_as_of": "2026-11-22T12:00:00+03:00"}
    kwargs = dict(database=db, owner_id="owner", from_date="2026-11-01", to_date="2026-11-15",
        include_legacy_analyses=False, allow_self_heal=False, persist_settings=False, fee_context=setup)
    before = await canonical_dashboard(**kwargs)
    assert before["totals"]["total_payment_fees"] == 18.4
    new = source("later-accepted", [sale("second", "2026-11-14", variable="6.00", vat="1.05")],
        uploaded="2026-11-22T08:00:00+03:00")
    proof = new.pop("accepted_summary")
    new["metadata"] = {"row_count": 1}
    await db[STATEMENTS].insert_one(new)
    await db[STATES].update_one({"_id": "owner"}, {"$set": {
        "revision": 2, "provider_statement_reconciliation.statement_summaries.later-accepted": {"statement_average": proof}}})
    after = await canonical_dashboard(**kwargs)
    assert after["range"] == before["range"]
    assert after["totals"]["total_sales"] == before["totals"]["total_sales"] == 150
    assert after["totals"]["total_payment_fees"] == 14.95  # Latest known week:8%, plus1, plus15% VAT.
    assert after["totals"]["net_profit"] == 125.05
    evidence = after["payment_fee_authority"]["payment_fee_estimate_sources"][0]
    assert evidence["eligible_sale_rows"] == 2
    assert (evidence["window_start"], evidence["window_end"]) == ("2026-11-08", "2026-11-14")
    assert await db.unified_orders.find({}).to_list(10) == orders_before


def actual_context_and_order(refunded="0.00"):
    state, _ = paid_state("tabby", refunded=refunded)
    events = [accepted_event(state)]
    if Decimal(refunded):
        events.append(accepted_event(state, key="refund", kind="refund", cover="refund:tabby:refund",
            principal=refunded, fee="-2.00", vat="-.30", total="-2.30"))
    accept(state, *events)
    current = order(order_number="order-one", payment_method="tabby_installment", total_amount=100,
        _dashboard_fee_source={"currency": "SAR", "payment_method": "tabby_installment",
            "payment_methods": [{"payment_method": "tabby_installment", "amount": 100, "transaction_reference": "receipt"}]})
    return weekly_context(operational_statement_fees=project(state)), current


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["refund", "native_refund", "cancelled", "split", "account", "replacement_capture", "principal"])
async def test_accepted_actual_does_not_hide_newer_payment_basis_changes(defect):
    context, current = actual_context_and_order()
    raw = current["_dashboard_fee_source"]
    if defect == "refund":
        current["actual_refund_amount"] = 40
    elif defect == "native_refund":
        raw["payment_actions"] = {"refund_action": {"refund_amount": 40}}
    elif defect == "cancelled":
        current["actual_canceled_amount"] = 100
    elif defect == "split":
        raw["payment_methods"].append({"payment_method": "bank", "amount": 10})
    elif defect == "account":
        raw["payment"] = {"merchant_account": "changed-account"}
    elif defect == "replacement_capture":
        raw["payment_methods"][0]["transaction_reference"] = "new-receipt"
    else:
        current["total_amount"] = 150
    result = await dashboard_fee_breakdown([current], [], context)
    assert result["payment_fees_complete"] is False
    assert not result.get("payment_fees_estimated")


@pytest.mark.asyncio
async def test_accepted_actual_with_matching_refund_uses_exact_signed_fee_once():
    context, current = actual_context_and_order("40.00")
    current["actual_refund_amount"] = 40
    current["_dashboard_fee_source"]["payment_actions"] = {"refund_action": {"refund_amount": 40}}
    result = await dashboard_fee_breakdown([current], [], context)
    assert result["payment_fees_complete"] is True and result["total_payment_fees"] == 5.75
    assert result["payment_breakdown"][0]["fee_sources"] == ["actual_statement"]
    assert not result.get("payment_fees_estimated")


@pytest.mark.asyncio
@pytest.mark.parametrize("identity,status,complete", [
    ("refund", "executed", True), ("new-refund", "executed", False),
    (None, "executed", False), ("refund", None, False),
])
async def test_native_refund_identity_must_match_the_accepted_fee_coverage(identity, status, complete):
    context, current = actual_context_and_order("40.00")
    current["actual_refund_amount"] = 40
    native = {"id": identity, "amount": 40, "currency": "SAR", "status": status,
              "refunded_at": "2026-11-02T12:00:00+03:00"}
    current["_dashboard_fee_source"]["payment_actions"] = {"refund_action": {
        "refund_amount": 40, "refunded_transactions": [native]}}
    if complete:
        # Canonical/native observations of the same execution count once.
        current["_dashboard_fee_source"]["payment"] = {"refunds": [native]}
    result = await dashboard_fee_breakdown([current], [], context)
    assert result["payment_fees_complete"] is complete
    if complete:
        assert result["total_payment_fees"] == 5.75


@pytest.mark.asyncio
async def test_accepted_operational_upload_replaces_historical_estimate_without_updating_orders():
    db = AsyncMongoMockClient().accepted_historical_dashboard
    document = source("historical-week", [sale("sample", "2026-10-31", variable="10.00", vat="1.65")],
        owner=ACTUAL_OWNER, uploaded="2026-11-02T08:00:00+03:00")
    seeded = await seed_accepted(db, document, owner=ACTUAL_OWNER)
    state, _ = paid_state()
    state.update(revision=1, provider_statement_reconciliation=seeded["provider_statement_reconciliation"])
    await db[STATES].replace_one({"_id": ACTUAL_OWNER}, state)
    await db.settings.insert_one({"user_id": ACTUAL_OWNER, "payment_methods": [],
        "shipping_companies": [{"name": "carrier", "cost_per_order": 0}], "net_sales_config": {}})
    _, current = actual_context_and_order()
    raw = current.pop("_dashboard_fee_source")
    raw.update(created_at="2026-11-01T12:00:00+03:00", updated_at="2026-11-01T12:00:00+03:00",
        payment_actions={"refund_action": {"paid_amount": 100, "refund_amount": 0}})
    current.update(user_id=ACTUAL_OWNER, total_product_cost=10, order_date="2026-11-01",
                   raw_by_source={"salla_direct": raw})
    await db.unified_orders.insert_one(current)
    kwargs = dict(database=db, owner_id=ACTUAL_OWNER, from_date="2026-11-01", to_date="2026-11-01",
        include_legacy_analyses=False, allow_self_heal=False, persist_settings=False,
        fee_context={"complete": True, "source_mode": "mz2_policies", "policies": tabby_policies()})
    before = await canonical_dashboard(**kwargs)
    assert before["totals"]["total_payment_fees"] == 12.65
    assert before["totals"]["payment_fees_estimated"] is True
    orders_before = await db.unified_orders.find({}).to_list(10)
    accept(state, accepted_event(state))
    state["revision"] = 2
    await db[STATES].replace_one({"_id": ACTUAL_OWNER}, state)
    after = await canonical_dashboard(**kwargs)
    assert after["range"] == before["range"]
    assert after["totals"]["total_payment_fees"] == 8.05 and after["totals"]["net_profit"] == 81.95
    assert not after["totals"].get("payment_fees_estimated")
    assert after["payment_breakdown"][0]["fee_amount"] == 8.05
    assert not after["payment_breakdown"][0].get("fees_estimated")
    assert await db.unified_orders.find({}).to_list(10) == orders_before


@pytest.mark.asyncio
async def test_same_request_shares_bounded_statement_reads_and_never_returns_tasks(monkeypatch):
    import asyncio
    import dashboard_payment_fees as fees
    db = AsyncMongoMockClient().shared_fee_reads
    state = await seed_accepted(db)
    counts = {"weekly": 0, "actual": 0}
    original_weekly, original_actual = fees.load_weekly_statement_averages, fees.load_operational_statement_fee_index

    async def weekly(*args, **kwargs):
        counts["weekly"] += 1
        return await original_weekly(*args, **kwargs)

    async def actual(*args, **kwargs):
        counts["actual"] += 1
        return await original_actual(*args, **kwargs)

    monkeypatch.setattr(fees, "load_weekly_statement_averages", weekly)
    monkeypatch.setattr(fees, "load_operational_statement_fee_index", actual)
    setup = {"complete": True, "source_mode": "mz2_policies", "policies": tabby_policies(),
        "weekly_fee_source_state": state, "evidence_as_of": "2026-11-22T12:00:00+03:00", "_request_fee_reads": {}}
    one, two = await asyncio.gather(*[
        load_dashboard_fee_context(db, "owner", [paid_order()], as_of="2026-11-22", fee_context=setup) for _ in range(2)])
    assert counts == {"weekly": 1, "actual": 1}
    assert one == two and "_request_fee_reads" not in one
    assert one["weekly_statement_averages"]["SAR"]["profile"] is not two["weekly_statement_averages"]["SAR"]["profile"]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["no_paid_proof", "partial_paid", "conflicting_paid", "pending", "cancelled", "refund", "split", "plan_conflict", "unknown_plan", "no_week", "bad_tax"])
async def test_weekly_average_does_not_hide_unverified_or_contradictory_money(defect):
    current, context = paid_order(), weekly_context()
    raw = current["_dashboard_fee_source"]
    if defect == "no_paid_proof":
        raw.pop("payment_actions")
    elif defect == "partial_paid":
        raw["payment_methods"][0]["amount"] = 75
        raw["payment_actions"]["refund_action"]["paid_amount"]["amount"] = 75
    elif defect == "conflicting_paid":
        raw["payment_actions"]["remaining_action"] = {"paid_amount": 100, "remaining_amount": 50}
    elif defect == "pending":
        raw["is_pending_payment"] = True
    elif defect == "cancelled":
        raw["status"] = {"slug": "cancelled"}
    elif defect == "refund":
        raw["payment_actions"]["refund_action"]["refund_amount"]["amount"] = 10
    elif defect == "split":
        raw["payment_methods"].append({"payment_method": "bank", "amount": 25})
    elif defect == "plan_conflict":
        context["statement_plans"] = {("tabby", "paid-tabby"): {"fee_plan_issue": "provider_payment_plan_conflict"}}
    elif defect == "unknown_plan":
        raw["payment"] = {"plan_id": "unsupported-plan"}
    elif defect == "no_week":
        context["weekly_statement_averages"] = {}
    else:
        context["policies"][1]["components"][0]["vat_rate"] = "5"
    result = await dashboard_fee_breakdown([current], [], context)
    assert result["payment_fees_complete"] is False
    assert not result.get("payment_fees_estimated")


@pytest.mark.asyncio
async def test_reader_uses_only_owner_accepted_documents_and_preserves_all_collections():
    db = AsyncMongoMockClient().weekly_sources
    await seed_accepted(db)
    orphan = source("orphan", [sale("orphan", "2026-11-21", variable="50", vat="7.65")])
    orphan.pop("accepted_summary")
    orphan["metadata"] = {"row_count": 1}
    await db[STATEMENTS].insert_one(orphan)
    await db[STATES].insert_one({"_id": "foreign", "owner_id": "foreign", "status": "active"})
    before = {name: await db[name].find({}).to_list(100) for name in await db.list_collection_names()}
    result = await load_weekly_statement_averages(db, "owner", as_of="2026-11-22")
    assert result["SAR"]["available"] is True
    assert result["SAR"]["profile"]["rate_percent"] == "6.25000000"
    assert result["SAR"]["source_statement_ids"] == ["accepted-week"]
    after = {name: await db[name].find({}).to_list(100) for name in await db.list_collection_names()}
    assert before == after


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing", "foreign", "oversized", "bad_count", "bad_hash", "future", "inactive"])
async def test_reader_refuses_unavailable_or_unbounded_accepted_sources(defect):
    db = AsyncMongoMockClient().weekly_bad_sources
    state = await seed_accepted(db)
    if defect == "missing":
        await db[STATEMENTS].delete_many({})
    elif defect == "foreign":
        await db[STATEMENTS].update_one({}, {"$set": {"owner_id": "foreign"}})
    elif defect == "oversized":
        await db[STATEMENTS].update_one({}, {"$set": {"metadata.row_count": 10001}})
    elif defect == "bad_count":
        await db[STATEMENTS].update_one({}, {"$set": {"metadata.row_count": 1}})
    elif defect == "bad_hash":
        await db[STATEMENTS].update_one({}, {"$set": {"content_hash": "f" * 64}})
    elif defect == "future":
        state["provider_statement_reconciliation"]["statement_summaries"]["accepted-week"]["statement_average"]["accepted_at"] = "2026-11-23T08:00:00+03:00"
    else:
        state["status"] = "draft"
    result = await load_weekly_statement_averages(db, "owner", as_of="2026-11-22", source_state=state)
    assert result["SAR"]["available"] is False


@pytest.mark.asyncio
async def test_context_and_canonical_legacy_reader_share_weekly_authority_and_actual_replacement():
    db = AsyncMongoMockClient().weekly_dashboard
    await seed_accepted(db)
    settings = {"user_id": "owner", "payment_methods": [],
        "shipping_companies": [{"name": "carrier", "cost_per_order": 0}], "net_sales_config": {}}
    await db.settings.insert_one(settings)
    current = paid_order()
    raw = current.pop("_dashboard_fee_source")
    current.update(user_id="owner", total_product_cost=10, raw_by_source={"salla_direct": raw})
    await db.unified_orders.insert_one(current)
    setup = {"complete": True, "source_mode": "mz2_policies", "policies": tabby_policies()}
    context = await load_dashboard_fee_context(db, "owner", [current], as_of="2026-11-22", fee_context=setup)
    assert context["weekly_statement_averages"]["SAR"]["available"] is True
    kwargs = dict(database=db, owner_id="owner", from_date="2026-11-01", to_date="2026-11-22",
        include_legacy_analyses=False, allow_self_heal=False, persist_settings=False, fee_context=setup)
    before = {name: await db[name].find({}).to_list(100) for name in await db.list_collection_names()}
    result = await canonical_dashboard(**kwargs)
    authority = result["payment_fee_authority"]
    assert authority["basis"] == AVERAGE_BASIS
    assert authority["payment_fees_estimated"] is True
    assert authority["estimated_orders_count"] == 1
    metadata = authority["payment_fee_estimate_sources"]
    assert [(row["basis"], row["window_start"], row["window_end"]) for row in metadata] == [
        ("weekly_statement_average", "2026-11-15", "2026-11-21")]
    assert result["totals"]["payment_fees_complete"] is True
    assert result["totals"]["payment_fees_estimated"] is True
    assert result["totals"]["total_payment_fees"] == result["totals"]["tabby_fees"] == 12.51
    assert result["totals"]["net_profit"] == 127.49
    group = result["payment_breakdown"][0]
    assert group["fees_estimated"] is True and group["estimated_orders_count"] == 1
    assert group["sub_methods"][0]["fees_estimated"] is True
    assert group["fee_estimate_sources"] == group["sub_methods"][0]["fee_estimate_sources"] == metadata
    assert result["totals"]["payment_fee_estimate_sources"] == metadata
    assert {name: await db[name].find({}).to_list(100) for name in await db.list_collection_names()} == before
    await db.unified_orders.update_one({"order_number": "paid-tabby"}, {"$set": {
        "payment_fee_status": "actual", "actual_payment_fee": 10, "actual_payment_vat": 1.5}})
    replaced = await canonical_dashboard(**kwargs)
    assert replaced["totals"]["total_payment_fees"] == 11.5
    assert replaced["totals"]["net_profit"] == 128.5
    assert not replaced["totals"].get("payment_fees_estimated")
    assert replaced["payment_fee_authority"]["basis"] == BASIS
    assert not replaced["payment_fee_authority"].get("payment_fees_estimated")
    assert not replaced["payment_breakdown"][0].get("fees_estimated")
    assert not replaced["payment_breakdown"][0]["sub_methods"][0].get("fees_estimated")
