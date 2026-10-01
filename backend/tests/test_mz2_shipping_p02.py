"""P02 behavioral regressions against native V2 shipping, on disposable Mongo.

Track F intentionally keeps collection and fee payment separate. Regression
coverage uses canonical operational sources and the V2 ledger exclusively.
"""
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException

from accounting_ledger_v2 import read_reporting_entries_v2
from accounting_mz2_reports import mz2_financial_position
from accounting_periods import PeriodChange, set_period
from accounting_shipping_native import recognize_cod, recognize_fee_delivery, accrue_fee, settle
from accounting_shipping_native_contract import EVIDENCE, EVENTS, SettlementInput
from accounting_shipping_native_routes import readiness
from accounting_shipping_native_setup import save_setup
from ledger_core import post_txn_group
from shipping_p02_native_fixture import db
from pydantic import ValidationError
from test_mz2_shipping_native import (
    OWNER, AT, CUT, source, rate, bind, movement, settlement, report,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def shipping_accounts(db):
    # Real canonical Track A account, never a bank-port mock or Legacy account.
    await db.mz2_financial_accounts.insert_one({"id": "bank-f", "user_id": OWNER,
        "name": "Synthetic bank", "account_type": "bank", "currency": "SAR", "status": "active", "version": 1})
    await save_setup(db, OWNER, OWNER, rate(2, kind="store_driver", identity="driver-f", delivery_fee="20.00"))


async def rows(db):
    return await read_reporting_entries_v2(db, user_id=OWNER, effective_before="2026-10-01T00:00:00Z")


async def financial_snapshot(db):
    names = ("accounting_journal_groups_v2", "accounting_general_ledger_v2", "accounting_audit_log_v2", EVIDENCE, EVENTS,
             "mz2_daily_movements", "store_delivery_assignments", "store_delivery_collections",
             "store_delivery_delivery_proofs", "unified_orders")
    return {name: await db[name].find({}).to_list(1000) for name in names}


def legs(items, group):
    return {(r["entity_type"], r["entity_id"], r.get("sub_account"), r["side"], Decimal(r["amount"]))
            for r in items if r["txn_group_id"] == group}


async def driver(db, number="1", value="150.00", method="cash", custody=None):
    await source(db, number, value=value)
    assignment = "assignment-" + number
    await db.store_delivery_assignments.insert_one({"id": assignment, "user_id": OWNER,
        "driver_id": "driver-f", "order_id": "salla-" + number, "order_number": number,
        "status": "delivered", "delivered_at": AT})
    await db.store_delivery_collections.insert_one({"id": "collection-" + number, "user_id": OWNER,
        "driver_id": "driver-f", "assignment_id": assignment, "order_id": "salla-" + number,
        "order_number": number, "payment_method": method, "amount": value,
        "cod_custody_amount": custody if custody is not None else value,
        "accounting_status": "operational_only", "delivery_proof_reference": "proof-" + number,
        "review_status": "not_required" if method == "cash" else "pending_accountant_review"})
    await db.store_delivery_delivery_proofs.insert_one({"user_id": OWNER, "driver_id": "driver-f",
        "token": "proof-" + number, "status": "bound", "bound_assignment_id": assignment})
    return assignment


async def recognize_driver(db, assignment):
    result = await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    fee = await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=result["evidence_id"])
    return result, fee


async def close_period(db):
    await set_period(db, OWNER, OWNER, PeriodChange(month="2026-09", closed=True, revision=0,
        reason="Synthetic close", evidence_ref="Synthetic approved close"))


async def test_external_courier_fee_uses_verified_rate_not_salla_charge_and_no_revenue(db):
    await source(db, payment_method="credit_card", shipping_cost="24.07")
    before = await rows(db)
    result = await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="1")
    assert result["state"] == "posted"
    after = await rows(db)
    assert len(after) == len(before) + 2
    assert legs(after, result["fee"]["txn_group_id"]) == {
        ("expense", "shipping", None, "debit", Decimal("17.25")),
        ("courier", "smsa", "payable", "credit", Decimal("17.25"))}
    assert result["fee"]["costs"] == {"gross": "17.25", "expense": "17.25", "input_vat": "0.00"}
    assert not any(r["entity_type"] in {"revenue", "tax"} for r in after)
    raw = await db.unified_orders.find_one({"user_id": OWNER, "order_number": "1"})
    assert raw["raw_by_source"]["salla_direct"]["shipping_cost"] == "24.07"
    retry = await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="1")
    assert retry["state"] == retry["fee"]["state"] == "already_posted"
    assert retry["fee"]["txn_group_id"] == result["fee"]["txn_group_id"]
    assert await rows(db) == after


async def test_store_driver_cash_cod_posts_sale_once_and_fee_separately(db):
    assignment = await driver(db)
    before = await rows(db)
    sale, fee = await recognize_driver(db, assignment)
    after = await rows(db)
    assert sale["txn_group_id"] != fee["txn_group_id"]
    assert legs(after, sale["txn_group_id"]) == {
        ("store_driver", "driver-f", "cod_receivable", "debit", Decimal("150")),
        ("revenue", "bnpl_sales", None, "credit", Decimal("130.43")),
        ("tax", "sales_vat_payable", None, "credit", Decimal("19.57"))}
    assert legs(after, fee["txn_group_id"]) == {
        ("expense", "store_delivery", None, "debit", Decimal("20")),
        ("store_driver", "driver-f", "delivery_fee_payable", "credit", Decimal("20"))}
    assert len(after) == len(before) + 5
    evidence = await db[EVIDENCE].find_one({"id": sale["evidence_id"]})
    assert evidence["status"] == "sealed" and evidence["cod_amount"] == "150.00"
    retry, retry_fee = await recognize_driver(db, assignment)
    assert retry["state"] == retry_fee["state"] == "already_posted"
    assert retry["txn_group_id"] == sale["txn_group_id"]
    assert await rows(db) == after
    position = await mz2_financial_position(db, owner=OWNER)
    assert position["status"] == "available"
    assert position["assets"]["store_driver_cod_receivable"] == 150
    assert position["liabilities"]["store_driver_payable"] == 20


async def test_cod_order_creation_cutover_rejects_without_financial_changes(db):
    assignment = await driver(db)
    for created, expected_code in (
        (None, "MZ2_COURIER_COD_COLLECTION_EVIDENCE_REQUIRED"),
        ("invalid", "MZ2_COURIER_COD_COLLECTION_EVIDENCE_REQUIRED"),
        ("2026-08-31T23:59:59Z", "pre_cutover_order"),
    ):
        await source(db, value="150.00", date=created)
        before = await financial_snapshot(db)
        with pytest.raises(HTTPException) as denied:
            await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
        assert denied.value.detail["code"] == expected_code
        assert await financial_snapshot(db) == before
    await source(db, value="150.00", date=CUT)
    assert (await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment))["state"] == "posted"


async def test_non_cash_pending_is_not_collected_and_mismatched_cash_never_posts(db):
    # Native #1211 records responsibility for all methods, but pending non-cash
    # cannot become a cash handover or reduce responsibility without approval.
    assignment = await driver(db, method="card_terminal", custody="0")
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)
    await bind(db, "store_driver", "driver-f")
    await movement(db, "pending-card", "150", "in")
    before = await financial_snapshot(db)
    with pytest.raises(HTTPException, match="driver_non_cash_approved_review_required"):
        await settle(db, owner=OWNER, actor_id=OWNER,
            payload=settlement("pending-card", kind="store_driver", identity="driver-f"))
    assert await financial_snapshot(db) == before
    view = await report(db, "store_driver", "driver-f")
    assert (view["cod_receivable"], view["collections"]) == ("150.00", "0.00")
    bad = await driver(db, "2", value="100", custody="99")
    before = await financial_snapshot(db)
    with pytest.raises(HTTPException, match="shipping_driver_collection_amount_conflict"):
        await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=bad)
    assert await financial_snapshot(db) == before


async def test_p02_lock_blocks_financial_write_without_partial_event(db):
    await source(db, payment_method="credit_card")
    await db.settings.update_one({"user_id": OWNER}, {"$set": {"mezan2_financial_cutover.p02_shipping_cod_enabled": False}})
    before = await financial_snapshot(db)
    with pytest.raises(HTTPException) as denied:
        await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="1")
    assert denied.value.status_code == 423
    assert denied.value.detail["code"] == "p02_shipping_cod_locked"
    assert await financial_snapshot(db) == before


async def test_closed_period_rolls_back_cod_sale_fee_and_source_marks(db):
    assignment = await driver(db, value="115")
    await close_period(db)
    before = await financial_snapshot(db)
    with pytest.raises(HTTPException, match="accounting_period_closed"):
        await recognize_driver(db, assignment)
    assert await financial_snapshot(db) == before
    assert await db[EVIDENCE].count_documents({}) == await db[EVENTS].count_documents({}) == 0


async def test_changed_cod_facts_after_post_conflict_instead_of_second_sale(db):
    assignment = await driver(db, value="115")
    first, _ = await recognize_driver(db, assignment)
    # Native fee contracts replace Legacy driver-earnings amounts. Change the
    # actual sealed responsibility rather than a no-longer-authoritative row.
    await source(db, value="120")
    await db.store_delivery_collections.update_one({"assignment_id": assignment},
        {"$set": {"amount": "120", "cod_custody_amount": "120"}})
    before = await financial_snapshot(db)
    with pytest.raises(HTTPException, match="shipping_recognition_source_changed"):
        await recognize_driver(db, assignment)
    assert await financial_snapshot(db) == before
    assert first["txn_group_id"]


async def test_driver_cod_collection_and_fee_payment_are_independent_without_netting(db):
    await recognize_driver(db, await driver(db))
    await bind(db, "store_driver", "driver-f")
    before = await rows(db)
    # User-approved Track F semantics: a 130 receipt clears only 130 COD.
    await movement(db, "partial", "130", "in")
    received = await settle(db, owner=OWNER, actor_id=OWNER,
        payload=settlement("partial", kind="store_driver", identity="driver-f"))
    assert legs(await rows(db), received["txn_group_id"]) == {
        ("bank", "bank-f", "main", "debit", Decimal("130")),
        ("store_driver", "driver-f", "cod_receivable", "credit", Decimal("130"))}
    view = await report(db, "store_driver", "driver-f")
    assert (view["cod_receivable"], view["payable"], view["collections"], view["payments"]) == ("20.00", "20.00", "130.00", "0.00")
    with pytest.raises(ValidationError):
        SettlementInput(request_id="net-request", movement_id="partial", action="net_settlement",
            party_type="store_driver", party_id="driver-f", offset_amount="20", reason="Synthetic prohibited netting")
    await movement(db, "driver-fee", "20", "out")
    fee_payload = settlement("driver-fee", "pay_fee", "store_driver", "driver-f")
    paid = await settle(db, owner=OWNER, actor_id=OWNER, payload=fee_payload)
    assert legs(await rows(db), paid["txn_group_id"]) == {
        ("bank", "bank-f", "main", "credit", Decimal("20")),
        ("store_driver", "driver-f", "delivery_fee_payable", "debit", Decimal("20"))}
    view = await report(db, "store_driver", "driver-f")
    assert (view["cod_receivable"], view["payable"]) == ("20.00", "0.00")
    await movement(db, "remaining", "20", "in")
    await settle(db, owner=OWNER, actor_id=OWNER,
        payload=settlement("remaining", kind="store_driver", identity="driver-f"))
    view = await report(db, "store_driver", "driver-f")
    assert (view["cod_receivable"], view["payable"], view["collections"], view["payments"]) == ("0.00", "0.00", "150.00", "20.00")
    after = await rows(db)
    assert len([r for r in after if r["entity_type"] in {"expense", "revenue"}]) == len(
        [r for r in before if r["entity_type"] in {"expense", "revenue"}])
    assert (await mz2_financial_position(db, owner=OWNER))["assets"]["banks"] == 1130
    retry = await settle(db, owner=OWNER, actor_id=OWNER, payload=fee_payload)
    assert retry["state"] == "already_posted" and retry["txn_group_id"] == paid["txn_group_id"]
    assert await rows(db) == after


async def test_courier_fee_payment_consumes_outbound_bank_row_without_second_expense(db):
    await source(db, payment_method="credit_card")
    accrual = await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="1")
    before = await rows(db)
    await bind(db)
    await movement(db, "fee", "17.25", "out")
    payload = settlement("fee", "pay_fee")
    result = await settle(db, owner=OWNER, actor_id=OWNER, payload=payload)
    after = await rows(db)
    assert legs(after, result["txn_group_id"]) == {
        ("courier", "smsa", "payable", "debit", Decimal("17.25")),
        ("bank", "bank-f", "main", "credit", Decimal("17.25"))}
    assert len([r for r in after if r["entity_type"] == "expense"]) == len([r for r in before if r["entity_type"] == "expense"])
    assert (await report(db))["payable"] == "0.00"
    assert (await mz2_financial_position(db, owner=OWNER))["assets"]["banks"] == 982.75
    assert accrual["fee"]["txn_group_id"]
    assert (await db.mz2_daily_movements.find_one({"id": "fee"}))["status"] == "accounting_posted"
    retry = await settle(db, owner=OWNER, actor_id=OWNER, payload=payload)
    assert retry["state"] == "already_posted" and retry["txn_group_id"] == result["txn_group_id"]


async def test_shipping_movement_cannot_be_consumed_twice_or_over_settle(db):
    await recognize_driver(db, await driver(db, value="100"))
    await bind(db, "store_driver", "driver-f")
    await movement(db, "full", "100", "in")
    await settle(db, owner=OWNER, actor_id=OWNER,
        payload=settlement("full", kind="store_driver", identity="driver-f"))
    before = await financial_snapshot(db)
    with pytest.raises(HTTPException, match="shipping_movement_unavailable"):
        await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("full").model_copy(update={"request_id": "reuse"}))
    assert await financial_snapshot(db) == before
    await movement(db, "over", "1", "in")
    before = await financial_snapshot(db)
    with pytest.raises(HTTPException, match="driver_non_cash_approved_review_required"):
        await settle(db, owner=OWNER, actor_id=OWNER,
            payload=settlement("over", kind="store_driver", identity="driver-f"))
    assert await financial_snapshot(db) == before
    assert (await db.mz2_daily_movements.find_one({"id": "over"}))["status"] == "unclassified"


async def test_closed_period_rolls_back_shipping_payment_and_bank_evidence_consumption(db):
    await source(db, payment_method="credit_card")
    await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="1")
    await bind(db)
    await movement(db, "closed", "17.25", "out")
    await close_period(db)
    before = await financial_snapshot(db)
    with pytest.raises(HTTPException, match="accounting_period_closed"):
        await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("closed", "pay_fee"))
    assert await financial_snapshot(db) == before
    row = await db.mz2_daily_movements.find_one({"id": "closed"})
    assert row["status"] == "unclassified" and not row.get("accounting_event_id")


async def test_shipping_workspace_native_readiness_keeps_unprocessed_movements_pending(db):
    await source(db, payment_method="credit_card")
    assignment = await driver(db, "2")
    await movement(db, "workspace", "130", "in")
    context = await readiness(db, OWNER)
    assert context["legacy_evidence_used"] is False
    assert context["activation_performed"] is False
    assert context["delivery_policy"] == "canonical_salla_delivered_no_upload"
    assert {r["courier_key"] for r in context["couriers"]} == {"smsa"}
    assert {r["id"] for r in context["store_drivers"]} == {"driver-f"}
    assert all(context["stages"][key]["ready"] for key in ("7", "8", "9"))
    assert await db[EVIDENCE].count_documents({}) == await db[EVENTS].count_documents({}) == 0
    courier_result = await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="1")
    driver_result, _ = await recognize_driver(db, assignment)
    assert await db[EVIDENCE].count_documents({"status": "sealed"}) == 2
    assert await db[EVENTS].count_documents({"kind": "fee"}) == 2
    assert courier_result["evidence_id"] != driver_result["evidence_id"]
    courier_view, driver_view = await report(db), await report(db, "store_driver", "driver-f")
    assert (courier_view["cod_receivable"], courier_view["payable"]) == ("0.00", "17.25")
    assert (driver_view["cod_receivable"], driver_view["payable"]) == ("150.00", "20.00")
    pending = await db.mz2_daily_movements.find_one({"id": "workspace"})
    assert pending["status"] == "unclassified" and not pending.get("accounting_event_id")
    refreshed = await readiness(db, OWNER)
    assert all(refreshed["stages"][key]["ready"] for key in ("7", "8", "9"))


async def test_old_shipping_ledger_writer_is_explicitly_rejected_in_v2(db):
    before = await financial_snapshot(db)
    with pytest.raises(HTTPException) as denied:
        await post_txn_group(db, user_id=OWNER, actor_id=OWNER, actor_name=OWNER, txn_type="shipping_fee",
            entries=[{"entity_type": "expense", "entity_id": "shipping", "side": "debit", "amount": 17.25, "entry_type": "shipping_fee"},
                     {"entity_type": "courier", "entity_id": "smsa", "sub_account": "payable", "side": "credit", "amount": 17.25, "entry_type": "shipping_fee"}])
    assert denied.value.status_code == 423
    assert denied.value.detail["code"] == "accounting_legacy_writer_disabled"
    assert await financial_snapshot(db) == before
    assert "general_ledger" not in await db.list_collection_names()
