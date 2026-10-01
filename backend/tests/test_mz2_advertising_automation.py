"""Setup once, deterministic automatic days, sealed zeros and reviewed deltas."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from accounting_advertising_contract import *
from accounting_advertising_setup import setup
from accounting_advertising_policy import due_at
from accounting_advertising_automation import run_day, run_batch, due_items, approve_adjustment
from accounting_advertising_wallet import MOVEMENTS, wallet_position
from accounting_advertising_sources import SOURCES
from integrations_control_center.ad_daily_close_proof import close_proof
from tests.test_mz2_advertising_v2 import db, OWNER, DAY, seed, binding, posted_legs
from tests.test_mz2_advertising_wallet import seed_native_wallet_opening

AS_OF = datetime(2026, 1, 3, 7, tzinfo=timezone.utc)


async def source_proof(db, platform="meta", amount=None, **extra):
    collection = db[SOURCES[platform][1]]
    row = await collection.find_one({})
    snap = platform == "snapchat"
    currency = row["currency" if snap else "currency_native"]
    amount = row["base_spend_native" if snap else "spend_native"] if amount is None else amount
    zone = "America/New_York" if snap else "Asia/Riyadh"
    observed = "2026-01-03T06:00:00+00:00"
    proof = close_proof(account_id=platform + "-external", business_date=DAY, timezone=zone,
        currency=currency, spend=amount, provider_row_count=24 if snap else 1,
        complete_response=True, explicit_spend_present=True, identity_proven=True,
        source_mode="snapchat_v2_daily_projection" if snap else row["source_mode"], observed_at=observed)
    changes = {"source_close_proof": proof, "updated_at": observed, "account_timezone": zone,
               "base_spend_native" if snap else "spend_native": amount}
    if snap:
        changes.update(projection_timezone=zone, sync_run_id="sync-1", source_latest_updated_at=observed,
            data_state="confirmed_zero" if amount == 0 else "confirmed_data",
            coverage=dict(expected_local_hours=24, known_fact_hours=24, missing_closed_hours=0,
                          provisional_hours=0, future_hours=0, amount_complete=True))
        await db.mezan_integration_accounts_v2.update_one({"provider": SOURCES[platform][0]}, {"$set": {"timezone": zone}})
    else:
        changes["observed_at"] = observed
    changes.update(extra)
    await collection.update_one({"_id": row["_id"]}, {"$set": changes})


async def configure(db, platform="meta", mode="postpaid", currency="SAR", amount=100, fraction=None, rate="3.75"):
    await seed(db, platform, currency, amount)
    await source_proof(db, platform)
    bind = await setup(db, OWNER, binding(platform, mode, currency))
    await setup(db, OWNER, Expense(purpose="advertising", entity_id="approved-ad-expense", evidence="setup expense authority"))
    zone = "America/New_York" if platform == "snapchat" else "Asia/Riyadh"
    policy = AutomationPolicy(platform=platform, integration_account_id=platform + "-v2", binding_version=1,
        version=0, start_date=DAY, business_timezone=zone, schedule_timezone="Asia/Riyadh" if platform == "meta" else zone,
        run_at="01:00" if platform == "meta" else "00:00", close_delay_minutes=0, max_source_age_hours=48,
        timezone_evidence="Provider timezone settings", source_close_contract="provider_complete_day_v1",
        fx_policy="sar_identity" if currency == "SAR" else "fixed_rate", wallet_fraction=fraction,
        fx_rate_to_sar=rate if currency != "SAR" else None, fx_at="2026-01-01T00:00:00Z" if currency != "SAR" else None,
        fx_source="Owner approved rate authority" if currency != "SAR" else None,
        fx_evidence="Quote with explicit date-validity" if currency != "SAR" else None,
        fx_valid_from="2026-01-01" if currency != "SAR" else None,
        fx_valid_through="2026-12-31" if currency != "SAR" else None, evidence="Enable automatic daily accounting")
    confirmed = await setup(db, OWNER, policy)
    return bind, confirmed


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", list(SOURCES))
async def test_automatic_four_platforms_no_daily_owner_click(db, platform):
    await configure(db, platform)
    assert await db[FACTS].count_documents({}) == 0
    result = await run_batch(db, OWNER, as_of=AS_OF)
    assert result["items"][0]["status"] == "POSTED"
    assert await db[FACTS].count_documents({}) == 1
    assert await db.accounting_journal_groups_v2.count_documents({}) == 1
    fact = await db[FACTS].find_one({})
    assert fact["created_by"] == "mz2-advertising-runner" and fact["setup_confirmed_by"] == OWNER
    assert "confirmed_by" not in fact  # no fictitious daily owner approval
    assert (await run_batch(db, OWNER, as_of=AS_OF))["items"] == []


@pytest.mark.asyncio
async def test_not_due_and_paused_runner_read_only_423(db):
    await configure(db)
    assert (await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of="2026-01-02T21:59:59Z"))["status"] == "NOT_DUE"
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    before = {name: await db[name].count_documents({}) for name in await db.list_collection_names()}
    assert len(await due_items(db, OWNER, as_of=AS_OF)) == 1
    with pytest.raises(HTTPException) as error:
        await run_batch(db, OWNER, as_of=AS_OF)
    assert error.value.status_code == 423
    after = {name: await db[name].count_documents({}) for name in await db.list_collection_names()}
    assert before == after
    assert await db[POSTINGS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_incomplete_and_stale_source_exact_blocked_event(db):
    await configure(db)
    await db[SOURCES["meta"][1]].update_one({}, {"$unset": {"source_close_proof": ""}})
    result = await run_batch(db, OWNER, as_of=AS_OF)
    assert result["items"][0]["reason"]["code"] == "ad_provider_close_proof_missing"
    assert await db[EVENTS].count_documents({}) == 1
    assert await db[FACTS].count_documents({}) == 0 and await db[POSTINGS].count_documents({}) == 0
    await source_proof(db)
    with pytest.raises(HTTPException, match="ad_source_stale"):
        await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of="2026-01-10T07:00:00Z")


@pytest.mark.asyncio
async def test_concurrent_automatic_runner_posts_one_journal(db):
    await configure(db)
    results = await asyncio.gather(*(run_batch(db, OWNER, as_of=AS_OF) for _ in range(5)))
    assert await db.accounting_journal_groups_v2.count_documents({}) == 1
    assert await db[POSTINGS].count_documents({}) == 1
    assert await db[FACTS].count_documents({}) == 1
    assert all(all(item["status"] == "POSTED" for item in result["items"]) for result in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", list(SOURCES))
async def test_real_zero_seals_without_journal_and_missing_not_zero(db, platform):
    await configure(db, platform, amount=0)
    result = await run_day(db, OWNER, platform, platform + "-v2", DAY, as_of=AS_OF)
    assert result["status"] == "CLOSED_ZERO" and result["txn_group_id"] is None
    assert await db.accounting_general_ledger_v2.count_documents({}) == 0
    await db[SOURCES[platform][1]].delete_many({})
    # Normal due queue never re-inspects a sealed zero, even if source disappears.
    assert (await run_batch(db, OWNER, as_of=AS_OF))["items"] == []
    with pytest.raises(HTTPException, match="ad_daily_v2_fact_missing"):
        await run_day(db, OWNER, platform, platform + "-v2", DAY, as_of=AS_OF)


@pytest.mark.asyncio
async def test_foreign_opening_1000_daily_120_leaves_880_and_explicit_fx(db):
    bind, policy = await configure(db, "snapchat", mode="prepaid", currency="USD", amount=120)
    await seed_native_wallet_opening(db, bind)
    result = await run_day(db, OWNER, "snapchat", "snapchat-v2", DAY, as_of=AS_OF)
    rows = await posted_legs(db, result)
    assert {row["amount"] for row in rows} == {"450.00"}
    position = await wallet_position(db, OWNER, bind)
    assert Decimal(position["posted_original_balance"]) == 880
    assert await db[MOVEMENTS].count_documents({}) == 2
    fx = (await db[POSTINGS].find_one({}))["fx"]
    assert fx["fx_rate_to_sar"] == "3.75" and fx["fx_source"] == "Owner approved rate authority"


@pytest.mark.asyncio
async def test_original_sufficiency_not_inferred_from_sar(db):
    bind, _ = await configure(db, mode="prepaid", currency="USD", amount=1100, rate="3")
    await seed_native_wallet_opening(db, bind, original_amount="1000", rate="4")
    # SAR4000 can cover3300 but original1000 cannot cover1100.
    with pytest.raises(HTTPException, match="ad_original_wallet_insufficient_balance"):
        await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
    assert await db[POSTINGS].count_documents({}) == 0
    assert await db[MOVEMENTS].count_documents({}) == 0  # opening materialization rolled back too
    assert await db.accounting_journal_groups_v2.count_documents({}) == 1  # fixture opening only


@pytest.mark.asyncio
async def test_foreign_hybrid_explicit_setup_allocation(db):
    bind, _ = await configure(db, mode="hybrid", currency="USD", amount=120, fraction="0.25")
    await seed_native_wallet_opening(db, bind)
    result = await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
    rows = await posted_legs(db, result)
    assert {(row["sub_account"], row["amount"]) for row in rows if row["side"] == "credit"} == {
        ("balance", "112.50"), ("debt", "337.50")}
    assert Decimal((await wallet_position(db, OWNER, bind))["posted_original_balance"]) == 970


@pytest.mark.asyncio
@pytest.mark.parametrize("final,delta,side", [(110, "10.00", "debit"), (90, "-10.00", "credit")])
async def test_source_revision_only_delta_owner_exception_review_and_retry(db, final, delta, side):
    await configure(db, amount=100)
    original = await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
    await source_proof(db, amount=final)
    proposal = await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
    assert proposal["status"] == "REVIEW_REQUIRED" and proposal["sar_delta"] == delta
    assert await db.accounting_journal_groups_v2.count_documents({}) == 1
    result = await approve_adjustment(db, OWNER, proposal["proposal_id"], "Owner reviewed changed provider final value", as_of=AS_OF)
    rows = await posted_legs(db, result)
    assert len(rows) == 2 and {row["amount"] for row in rows} == {"10.00"}
    assert next(row for row in rows if row["entity_type"] == "expense")["side"] == side
    retried = await approve_adjustment(db, OWNER, proposal["proposal_id"], "retry", as_of=AS_OF)
    assert retried["txn_group_id"] == result["txn_group_id"]
    assert await db.accounting_journal_groups_v2.count_documents({}) == 2
    record = await db[ADJUSTMENT_POSTS].find_one({})
    assert record["original_txn_group_id"] == original["txn_group_id"]
    assert record["old_source_revision"] != record["source_revision"]
    assert (await db[POSTINGS].find_one({}))["totals"]["original_amount"] == "100"  # never overwrite


@pytest.mark.asyncio
async def test_foreign_negative_adjustment_restores_units(db):
    bind, _ = await configure(db, mode="prepaid", currency="USD", amount=120)
    await seed_native_wallet_opening(db, bind)
    await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
    await source_proof(db, amount=110)
    proposal = await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
    result = await approve_adjustment(db, OWNER, proposal["proposal_id"], "Review refund correction", as_of=AS_OF)
    assert result["original_delta"] == "-10" and result["sar_delta"] == "-37.50"
    assert Decimal((await wallet_position(db, OWNER, bind))["posted_original_balance"]) == 890
    assert await db[MOVEMENTS].count_documents({}) == 3


@pytest.mark.asyncio
async def test_zero_revision_to_positive_is_reviewed_delta(db):
    await configure(db, amount=0)
    await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
    await source_proof(db, amount=10)
    proposal = await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
    assert proposal["status"] == "REVIEW_REQUIRED"
    await approve_adjustment(db, OWNER, proposal["proposal_id"], "Review late finalized spend", as_of=AS_OF)
    assert await db.accounting_journal_groups_v2.count_documents({}) == 1
    assert (await db[POSTINGS].find_one({}))["status"] == "CLOSED_ZERO"


def test_due_policy_meta_riyadh_and_snap_new_york_dst():
    meta = dict(business_timezone="Asia/Riyadh", schedule_timezone="Asia/Riyadh", run_at="01:00", close_delay_minutes=0)
    assert due_at(meta, "2026-01-02").isoformat() == "2026-01-02T22:00:00+00:00"
    snap = dict(business_timezone="America/New_York", schedule_timezone="America/New_York", run_at="00:00", close_delay_minutes=0)
    assert due_at(snap, "2026-03-08").isoformat() == "2026-03-09T04:00:00+00:00"
    assert due_at(snap, "2026-11-01").isoformat() == "2026-11-02T05:00:00+00:00"


@pytest.mark.asyncio
async def test_repeated_financial_revision_cycle_keeps_distinct_adjustments(db):
    await configure(db, amount=100)
    await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
    proposals = []
    for amount in (110, 100, 110):
        await source_proof(db, amount=amount)
        proposal = await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
        proposals.append(proposal["proposal_id"])
        result = await approve_adjustment(db, OWNER, proposal["proposal_id"], "Reviewed recurring provider revision", as_of=AS_OF)
        assert result["status"] == "ADJUSTED"
    assert len(set(proposals)) == 3
    assert await db.accounting_journal_groups_v2.count_documents({}) == 4
    latest = await db[ADJUSTMENT_POSTS].find_one({"sequence": 3})
    assert latest["totals"]["total_sar"] == "110.00"


@pytest.mark.asyncio
@pytest.mark.parametrize("opening,opening_rate,insufficient", [("0.000001", "1000000", False), ("0.0000003", "3000000", True)])
async def test_submicro_original_units_never_disappear(db, opening, opening_rate, insufficient):
    bind, _ = await configure(db, mode="prepaid", currency="USD", amount="0.0000004", rate="1000000")
    await seed_native_wallet_opening(db, bind, original_amount=opening, rate=opening_rate)
    if insufficient:
        with pytest.raises(HTTPException, match="ad_original_wallet_insufficient_balance"):
            await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
        assert await db[POSTINGS].count_documents({}) == 0
        assert await db[MOVEMENTS].count_documents({}) == 0
    else:
        await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
        assert Decimal((await wallet_position(db, OWNER, bind))["posted_original_balance"]) == Decimal("0.0000006")


@pytest.mark.asyncio
async def test_manual_bridge_also_preserves_submicro_original_units(db):
    from tests.test_mz2_advertising_v2 import approved
    from accounting_advertising_bridge import post_spend
    await seed(db, currency="USD", amount="0.0000004")
    snapshot = await approved(db, mode="prepaid", currency="USD")
    bind = binding(mode="prepaid", currency="USD").model_dump(mode="json")
    await seed_native_wallet_opening(db, bind, original_amount="0.000001", rate="1000000")
    fx = await setup(db, OWNER, FxSnapshot(currency="USD", business_date=DAY,
        fx_rate_to_sar="1000000", fx_at="2026-01-02T00:00:00Z", fx_source="Reviewed rate",
        evidence="Test daily rate authority"))
    await post_spend(db, OWNER, SpendPost(snapshot_id=snapshot["id"], fx_snapshot_id=fx["id"]))
    assert Decimal((await wallet_position(db, OWNER, bind))["posted_original_balance"]) == Decimal("0.0000006")


@pytest.mark.asyncio
@pytest.mark.parametrize("authority", ["initial_post", "adjustment_post", "snapshot", "snapshot_reuse", "proposal", "approved_retry"])
async def test_corrupt_reused_authority_fails_before_delta_or_success(db, authority):
    await configure(db, amount=100)
    await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
    if authority == "initial_post":
        await db[POSTINGS].update_one({}, {"$set": {"totals.total_sar": "99.00"}})
        await source_proof(db, amount=110)
        action = lambda: run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
        code = "ad_posting_integrity_failure"
    else:
        await source_proof(db, amount=110)
        proposal = await run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
        if authority == "snapshot":
            row = await db[ADJUSTMENTS].find_one({"_id": proposal["proposal_id"]})
            await db[FACTS].update_one({"id": row["new_snapshot_id"]}, {"$set": {"original_amount": "10000"}})
            action = lambda: approve_adjustment(db, OWNER, proposal["proposal_id"], "Review", as_of=AS_OF)
            code = "ad_snapshot_integrity_failure"
        elif authority == "proposal":
            await db[ADJUSTMENTS].update_one({"_id": proposal["proposal_id"]}, {"$set": {"target_totals.total_sar": "10000"}})
            action = lambda: run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
            code = "ad_adjustment_proposal_integrity_failure"
        else:
            await approve_adjustment(db, OWNER, proposal["proposal_id"], "Review", as_of=AS_OF)
            if authority == "snapshot_reuse":
                original = await db[POSTINGS].find_one({})
                await db[FACTS].update_one({"id": original["snapshot_id"]}, {"$set": {"original_amount": "10000"}})
                await source_proof(db, amount=100)
                action = lambda: run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
                code = "ad_snapshot_integrity_failure"
            elif authority == "approved_retry":
                await db[ADJUSTMENT_POSTS].update_one({}, {"$set": {"totals.total_sar": "99.00"}})
                action = lambda: approve_adjustment(db, OWNER, proposal["proposal_id"], "Retry", as_of=AS_OF)
                code = "ad_adjustment_post_integrity_failure"
            else:
                await db[ADJUSTMENT_POSTS].update_one({}, {"$set": {"totals.total_sar": "99.00"}})
                await source_proof(db, amount=120)
                action = lambda: run_day(db, OWNER, "meta", "meta-v2", DAY, as_of=AS_OF)
                code = "ad_adjustment_post_integrity_failure"
    journals = await db.accounting_journal_groups_v2.count_documents({})
    with pytest.raises(HTTPException, match=code):
        await action()
    assert await db.accounting_journal_groups_v2.count_documents({}) == journals
