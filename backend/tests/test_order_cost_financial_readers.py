"""Historical COGS in financial readers without touching saved ledger facts."""
from copy import deepcopy
from datetime import date

import pytest

import mezan_attribution_profit_bridge as bridge
from customer_cohort_profit_report import build_customer_cohort_report
from mezan_attribution_order_ledger import LEDGER_COLLECTION, _order_key
from order_cost_authority import LIFECYCLES
from product_v2_details_routes import COST_PROFILES
from .test_dashboard_bundle_cost_authority import OWNER, ReadOnlyDB, db, freeze, sale, sale_line


async def add_sale(db, number, *, quantity=4, day="2026-11-01", old_cost=80, **fields):
    order = sale(number, sale_line("line-" + number, quantity), order_id="provider-" + number,
        order_date=day, order_created_at=day + "T12:00:00+00:00", data_source="salla_direct",
        customer_id="synthetic-repeat-customer", **fields)
    await freeze(db, order)
    revenue = order["total_amount"] - order.get("actual_partial_refund_amount", 0)
    ledger = {"user_id": OWNER, "order_key": _order_key(order), "order_created_at": order["order_created_at"],
        "attribution": {"quality": "confirmed", "decision_safe": True, "provider": "snapchat",
                        "marketing_source": "snapchat", "account_id": "synthetic-account", "campaign_id": "synthetic-campaign"},
        "profit": {"known": True, "revenue_sar": revenue, "cogs_sar": old_cost,
                   "allocated_ad_spend_sar": 20, "shipping_sar": 0, "fees_sar": 0,
                   "net_profit_sar": revenue - old_cost - 20}}
    await db[LEDGER_COLLECTION].insert_one(deepcopy(ledger))
    return order, ledger


@pytest.mark.asyncio
async def test_bridge_reads_frozen_220_and_adjusts_old_300_profit_to_160_without_ledger_write(db, monkeypatch):
    _, ledger = await add_sale(db, "bridge")
    before = await db[LEDGER_COLLECTION].find_one({"user_id": OWNER})
    await db[COST_PROFILES].update_many({"user_id": OWNER}, {"$set": {"base_cost": 999}})

    async def unrelated_store_envelope(*args, **kwargs):
        return {"totals": {"net_profit": 160}, "quality": {"complete": True}}

    import mezan_profit_engine
    monkeypatch.setattr(mezan_profit_engine, "build_mezan_profit_envelope", unrelated_store_envelope)
    report = await bridge.build_attribution_profit_bridge(ReadOnlyDB(db), OWNER,
        from_date="2026-11-01", to_date="2026-11-02")
    assert report["attribution"]["known_order_profit"]["net_profit_sar"] == 160
    assert report["attribution"]["campaigns"][0]["known_net_profit_sar"] == 160
    assert report["attribution"]["coverage"]["historical_order_cost"]["historical_cost_verified_orders"] == 1
    assert report["reconciliation"]["difference_sar"] == 0
    assert report["reconciliation"]["comparable_as_full_total"]
    assert await db[LEDGER_COLLECTION].find_one({"user_id": OWNER}) == before
    assert ledger["profit"]["cogs_sar"] == 80


@pytest.mark.asyncio
async def test_cohort_repeat_contribution_uses_same_220_frozen_cost_and_preserves_privacy(db):
    await add_sale(db, "first", quantity=1, old_cost=20, day="2026-10-10")
    await add_sale(db, "repeat")
    before = await db[LEDGER_COLLECTION].find({}).to_list(None)
    await db[COST_PROFILES].update_many({"user_id": OWNER}, {"$set": {"base_cost": 999}})
    report = await build_customer_cohort_report(ReadOnlyDB(db), OWNER, as_of=date(2026, 11, 2))
    contribution = report["summary"]["later_order_contribution_profit"]
    assert contribution["known_amount_sar"] == 160
    assert contribution["known_orders"] == 1 and not contribution["partial"]
    assert report["coverage"]["historical_order_cost"]["historical_cost_verified_orders"] == 2
    assert report["summary"]["first_acquisition_cost"]["known_amount_sar"] == 20
    assert "synthetic-repeat-customer" not in str(report)
    assert await db[LEDGER_COLLECTION].find({}).to_list(None) == before


@pytest.mark.asyncio
async def test_projection_applies_refund_policy_once_and_is_idempotent_on_a_report_copy(db):
    order, ledger = await add_sale(db, "refunded", old_cost=60, actual_partial_refund_amount=100)
    before = deepcopy(ledger)
    projected, coverage = await bridge.project_ledger_historical_costs(ReadOnlyDB(db), OWNER, [ledger], orders=[order])
    assert projected[0]["profit"]["cogs_sar"] == 165
    assert projected[0]["profit"]["net_profit_sar"] == 115  # 300 revenue - 165 COGS - 20 ads
    repeated, _ = await bridge.project_ledger_historical_costs(ReadOnlyDB(db), OWNER, projected, orders=[order])
    assert repeated[0]["profit"]["net_profit_sar"] == 115
    assert repeated[0]["profit"]["cogs_sar"] == 165
    assert coverage["historical_cost_verified_orders"] == 1
    assert ledger == before


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["snapshot_missing", "line_changed", "foreign_owner", "duplicate_order"])
async def test_unresolved_or_ambiguous_historical_order_cannot_certify_saved_profit(db, cause):
    order, ledger = await add_sale(db, "unresolved")
    if cause == "snapshot_missing":
        await db[LIFECYCLES].delete_many({"user_id": OWNER})
    elif cause == "line_changed":
        await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {"products.0.quantity": 5}})
    elif cause == "foreign_owner":
        await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {"user_id": "foreign-owner"}})
    else:
        await db.unified_orders.insert_one(deepcopy(order))
    projected, coverage = await bridge.project_ledger_historical_costs(ReadOnlyDB(db), OWNER, [ledger])
    assert not projected[0]["profit"]["known"]
    assert projected[0]["profit"]["cogs_sar"] is None
    assert projected[0]["profit"]["net_profit_sar"] is None
    assert coverage["unresolved_orders"] == 1 and not coverage["complete"]
    assert ledger["profit"]["known"]


@pytest.mark.asyncio
async def test_unmapped_cohort_cost_increases_unknown_coverage_instead_of_zero_profit(db):
    await add_sale(db, "first", quantity=1, old_cost=20, day="2026-10-10")
    await add_sale(db, "repeat")
    await db[LIFECYCLES].delete_one({"order_number": "repeat"})
    report = await build_customer_cohort_report(ReadOnlyDB(db), OWNER, as_of=date(2026, 11, 2))
    contribution = report["summary"]["later_order_contribution_profit"]
    assert contribution["known_orders"] == 0 and contribution["unknown_orders"] == 1
    assert contribution["partial"]
    assert report["coverage"]["historical_order_cost"]["unresolved_orders"] == 1


@pytest.mark.asyncio
async def test_existing_profit_without_old_cogs_is_explicit_legacy_unverified_not_reconciled(db, monkeypatch):
    _, ledger = await add_sale(db, "legacy")
    await db[LEDGER_COLLECTION].update_one({"user_id": OWNER}, {"$unset": {"profit.cogs_sar": ""}})

    async def unrelated_store_envelope(*args, **kwargs):
        return {"totals": {"net_profit": 300}, "quality": {"complete": True}}

    import mezan_profit_engine
    monkeypatch.setattr(mezan_profit_engine, "build_mezan_profit_envelope", unrelated_store_envelope)
    report = await bridge.build_attribution_profit_bridge(ReadOnlyDB(db), OWNER,
        from_date="2026-11-01", to_date="2026-11-02")
    assert report["attribution"]["known_order_profit"]["net_profit_sar"] == ledger["profit"]["net_profit_sar"]
    assert report["attribution"]["known_order_profit"]["partial"]
    assert report["attribution"]["campaigns"][0]["cost_unverified_orders"] == 1
    assert report["attribution"]["campaigns"][0]["known_net_profit_is_partial"]
    assert not report["reconciliation"]["comparable_as_full_total"]
    assert report["reconciliation"]["difference_sar"] is None


@pytest.mark.asyncio
async def test_financial_reader_queries_are_batched_across_80_orders(db):
    rows = []
    for index in range(80):
        _, ledger = await add_sale(db, "batch-" + str(index))
        rows.append(ledger)
    single, batch = ReadOnlyDB(db), ReadOnlyDB(db)
    one, _ = await bridge.project_ledger_historical_costs(single, OWNER, rows[:1])
    many, coverage = await bridge.project_ledger_historical_costs(batch, OWNER, rows)
    assert one[0]["profit"]["cogs_sar"] == 220
    assert sum(row["profit"]["cogs_sar"] for row in many) == 17600
    assert coverage["historical_cost_verified_orders"] == 80
    assert single.reads == batch.reads
    assert sum(batch.reads.values()) <= 10
