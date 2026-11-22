"""Actual dashboard function + isolated real Mongo; never imports server bootstrap."""
import pytest

from test_awaiting_payment_eligibility import fixture
from test_dashboard_dated_payment_fees import context, legacy, order, real_server_dashboard_without_bootstrap
from test_operational_balance_integration import run


async def seed(db, *, only_unpaid=False, malformed=False, actual=False):
    source, raw = fixture(number="unpaid")
    raw["customer"] = {"name": "must-not-appear-in-response"}
    if malformed:
        raw["payment_actions"]["remaining_action"].pop("paid_amount")
    unpaid = {**order(), **source, "total_product_cost": 30, "order_date": "2026-11-20",
              "raw_by_source": {"salla_direct": raw}}
    if actual:
        unpaid.update(payment_fee_status="actual", actual_payment_fee=1, actual_payment_vat=.15, actual_payment_method="mada")
    await db.settings.insert_one({"user_id": "owner", "payment_methods": legacy(),
        "shipping_companies": [{"name": "carrier", "cost_per_order": 0}], "net_sales_config": {}})
    rows = [unpaid]
    if not only_unpaid:
        rows.append({**order(order_number="paid"), "user_id": "owner", "total_product_cost": 10})
    await db.unified_orders.insert_many(rows)
    return unpaid


async def calculate(db):
    before = {name: await db[name].find({}).to_list(1000) for name in await db.list_collection_names()}
    result = await real_server_dashboard_without_bootstrap()(
        database=db, owner_id="owner", from_date="2026-11-01", to_date="2026-11-21",
        include_legacy_analyses=False, allow_self_heal=False, persist_settings=False, fee_context=context())
    after = {name: await db[name].find({}).to_list(1000) for name in await db.list_collection_names()}
    assert after == before
    assert "must-not-appear-in-response" not in str(result)
    return result


def test_unpaid_dashboard_defers_history_preserves_costs_and_blocks_final_profit():
    async def scenario(db):
        await seed(db)
        result = await calculate(db)
        totals = result["totals"]
        assert totals["total_orders"] == totals["salla_reference_orders_count"] == 2
        assert totals["total_sales"] == 200
        assert totals["computed_product_cost"] == 40
        assert totals["total_payment_fees"] is None
        assert totals["net_profit"] is None
        assert totals["excluded_orders_count"] == 0 and totals["excluded_gross"] == 0
        assert sum(row["count"] for row in result["source_breakdown"]) == 2
        coverage = result["financial_cohort"]
        assert coverage["total_orders"] == 2 and coverage["financial_eligible_orders"] == 2
        assert coverage["excluded_unpaid_orders"] == 0 and coverage["review_orders"] == 1 and not coverage["complete"]
    run(scenario)


def test_all_unpaid_dashboard_is_pending_not_certified_zero():
    async def scenario(db):
        await seed(db, only_unpaid=True)
        result = await calculate(db)
        assert result["totals"]["total_orders"] == 1
        assert result["financial_cohort"]["financial_eligible_orders"] == 1
        assert result["totals"]["total_sales"] == 100
        assert result["totals"]["computed_product_cost"] == 30
        assert result["totals"]["net_profit"] is None
        assert result["financial_cohort"]["complete"] is False
    run(scenario)


@pytest.mark.parametrize("kind", ["bank_receipt", "shipping_charge", "supplier_receipt"])
def test_prior_financial_fact_prevents_dropping_waiting_order_and_its_cost(kind):
    async def scenario(db):
        await seed(db)
        collection, row = {
            "bank_receipt": ("mz2_bank_transfer_receipt_events", {"facts": {"order_number": "unpaid"}, "status": "posted"}),
            "shipping_charge": ("mz2_shipping_native_events_v2", {"order_number": "unpaid", "kind": "charge"}),
            "supplier_receipt": ("mezan_supplier_receiving_events_v1", {"order_number": "unpaid"}),
        }[kind]
        await db[collection].insert_one({"user_id": "owner", **row})
        result = await calculate(db)
        assert result["totals"]["total_orders"] == 2 and result["totals"]["total_sales"] == 200
        assert result["totals"]["computed_product_cost"] == 40
        assert result["totals"]["net_profit"] is None
        assert result["financial_cohort"]["excluded_unpaid_orders"] == 0
        assert result["financial_cohort"]["review_orders"] == 1
    run(scenario)


def test_foreign_owner_history_does_not_authorize_dashboard_exclusion():
    async def scenario(db):
        await seed(db)
        await db.mz2_bank_transfer_receipt_events.insert_one({"user_id": "other", "status": "posted", "facts": {"order_number": "unpaid"}})
        result = await calculate(db)
        assert result["financial_cohort"]["excluded_unpaid_orders"] == 0
        assert result["totals"]["total_sales"] == 200
        assert result["totals"]["net_profit"] is None
    run(scenario)


@pytest.mark.parametrize("actual", [False, True])
def test_uncertain_waiting_stays_in_registry_and_profit_remains_unknown(actual):
    async def scenario(db):
        await seed(db, malformed=not actual, actual=actual)
        result = await calculate(db)
        assert result["totals"]["total_orders"] == 2 and result["totals"]["total_sales"] == 200
        assert result["financial_cohort"]["review_orders"] == 1
        assert result["financial_cohort"]["complete"] is False
        assert result["totals"]["net_profit"] is None
        if actual:
            assert result["totals"]["payment_fees_complete"] is True
            assert result["totals"]["total_payment_fees"] == 10.91
    run(scenario)


def test_dashboard_never_starts_history_reads_even_with_unpaid_candidates(monkeypatch):
    calls = []
    async def unavailable(*args, **kwargs):
        calls.append(True)
        raise AssertionError("dashboard must not read financial history")
    monkeypatch.setattr("awaiting_payment_history._load", unavailable)

    async def scenario(db):
        await seed(db)
        result = await calculate(db)
        assert result["totals"]["total_orders"] == 2 and result["totals"]["total_sales"] == 200
        assert result["financial_cohort"]["excluded_unpaid_orders"] == 0
        assert result["financial_cohort"]["review_reason_counts"] == {"awaiting_payment_history_deferred": 1}
        assert calls == []
        assert result["totals"]["net_profit"] is None
    run(scenario)


def test_cod_method_keeps_its_current_financial_policy(monkeypatch):
    async def unexpected_history_read(*args, **kwargs):
        raise AssertionError("No placeholder must require a history read")
    monkeypatch.setattr("awaiting_payment_history._load", unexpected_history_read)

    async def scenario(db):
        await seed(db, only_unpaid=True)
        await db.settings.update_one({"user_id": "owner"}, {"$set": {"payment_methods": [
            {"name": "cod", "commission_percent": 0, "fixed_fee": 0, "vat_percent": 0}]}})
        await db.unified_orders.update_one({"user_id": "owner"}, {"$set": {
            "payment_method": "cod", "raw_by_source.salla_direct.payment_method": "cod",
            "raw_by_source.salla_direct.payment_methods": [{"payment_method": "cod", "amount": 0}]}})
        result = await calculate(db)
        assert result["totals"]["total_orders"] == result["financial_cohort"]["financial_eligible_orders"] == 1
        assert result["financial_cohort"]["excluded_unpaid_orders"] == 0
        assert result["financial_cohort"]["complete"] is True
        assert result["totals"]["total_sales"] == 100
        assert result["totals"]["computed_product_cost"] == 30
        assert result["totals"]["total_payment_fees"] == 0
        assert result["totals"]["net_profit"] == 70
    run(scenario)


@pytest.mark.parametrize("normalized", [None, "mada"])
def test_native_only_placeholder_is_review_without_complete_exclusion_proof(normalized):
    async def scenario(db):
        await seed(db)
        await db.unified_orders.update_one({"user_id": "owner", "order_number": "unpaid"},
                                          {"$set": {"payment_method": normalized}})
        result = await calculate(db)
        assert result["totals"]["total_orders"] == 2 and result["totals"]["total_sales"] == 200
        assert result["financial_cohort"]["excluded_unpaid_orders"] == 0
        assert result["financial_cohort"]["review_orders"] == 1
        assert result["totals"]["net_profit"] is None
    run(scenario)
