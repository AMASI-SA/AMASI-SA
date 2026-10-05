"""Real isolated Mongo + HTTP evidence; never imports the production server.

Set OPERATIONAL_TEST_MONGO_URI to localhost. No production environment is read.
"""
import asyncio
from copy import deepcopy
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from operational_balance_routes import make_operational_balance_router
from operational_balance_service import save_opening, create_movement, report, refresh, supplier_return, freeze
from operational_balance_store import read, STATES, RECEIPTS, mutate
from operational_balance_engine import reconcile

START = "2026-10-05T09:00:00+00:00"
NOW = "2026-10-07T12:00:00+00:00"
URI = os.environ.get("OPERATIONAL_TEST_MONGO_URI", "mongodb://127.0.0.1:27305")
assert URI.startswith("mongodb://127.0.0.1:"), "Only dedicated local test Mongo is allowed"


def run(fn):
    async def execute():
        client = AsyncIOMotorClient(URI, serverSelectionTimeoutMS=3000)
        db = client["operational_balance_test_" + uuid4().hex]
        try:
            await client.admin.command("ping")
            await db.users.insert_many([
                {"id": "owner", "role": "owner", "name": "المالك", "is_active": True},
                {"id": "other", "role": "owner", "is_active": True},
                {"id": "staff", "role": "employee", "created_by": "owner", "operational_balance_permissions": ["view", "move"]},
                {"id": "viewer", "role": "employee", "created_by": "owner", "operational_balance_permissions": ["view"]},
            ])
            await db.mz2_financial_accounts.insert_many([
                {"id": "bank", "user_id": "owner", "name": "الراجحي", "account_type": "bank", "currency": "SAR", "status": "active"},
                {"id": "cash", "user_id": "owner", "name": "الصندوق", "account_type": "cash", "currency": "SAR", "status": "active"},
            ])
            await db.mezan_suppliers_v2.insert_one({"id": "supplier", "user_id": "owner", "name": "المورد", "status": "active"})
            yieldless = await fn(db)
            return yieldless
        finally:
            await client.drop_database(db.name)
            client.close()
    return asyncio.run(execute())


def opening(kind="bank", identity="bank", amount="1000", direction="for_us", request="baseline1"):
    return dict(request_id=request, party_type=kind, party_id=identity, amount=amount, currency="SAR", direction=direction)


async def started(db, supplier="500"):
    await save_opening(db, "owner", "owner", opening(), clock=START)
    await save_opening(db, "owner", "owner", opening("supplier", "supplier", supplier, "for_party", "baseline2"), clock=START)
    await save_opening(db, "owner", "owner", {"request_id": "startnow1"}, finish=True, clock=START)


def movement(**changes):
    return dict(request_id="movement1", party_type="supplier", party_id="supplier", bank_id="bank",
                amount="300", currency="SAR", direction="outgoing", kind="payment", note="دفعة موثقة",
                receipt_id=None, order_number=None, reference="", allocations=[], **changes)


def test_baseline_is_not_cash_movement_and_payment_allocates_once():
    async def scenario(db):
        await started(db)
        item = await create_movement(db, "owner", "owner", movement(), clock=NOW)
        again = await create_movement(db, "owner", "owner", movement(), clock=NOW)
        assert item == again
        state = await read(db, "owner")
        assert len(state["movements"]) == 1
        result = report(state)
        assert result["summary"]["actual_liquidity"] == "700.00"
        assert result["summary"]["payable"] == "200.00"
        assert result["summary"]["settled"] == "300.00"
        assert len(state["audit"]) == 4
    run(scenario)


def test_concurrent_same_request_is_one_cash_effect():
    async def scenario(db):
        await started(db)
        await asyncio.gather(*[create_movement(db, "owner", "staff", movement(), clock=NOW) for _ in range(8)])
        state = await read(db, "owner")
        assert len(state["movements"]) == 1
        assert report(state)["summary"]["actual_liquidity"] == "700.00"
    run(scenario)


def test_concurrent_different_settlements_cannot_overconsume():
    async def scenario(db):
        await started(db)
        state = await read(db, "owner")
        oid = next(k for k, v in state["openings"].items() if v["party_type"] == "supplier")
        p = movement(); p.update(kind="settlement", amount="400", allocations=[{"obligation_id": oid, "amount": "400"}])
        q = {**p, "request_id": "movement2"}
        results = await asyncio.gather(create_movement(db, "owner", "owner", p), create_movement(db, "owner", "owner", q), return_exceptions=True)
        assert sum(isinstance(x, HTTPException) for x in results) == 1
        assert report(await read(db, "owner"))["summary"]["payable"] == "100.00"
    run(scenario)


def test_atomic_save_and_finish_retry_and_immutable_baseline():
    async def scenario(db):
        p = {"request_id": "finish123", "opening": opening()}
        first = await save_opening(db, "owner", "owner", p, finish=True, clock=START)
        assert first == await save_opening(db, "owner", "owner", p, finish=True, clock=NOW)
        assert first["started_at"] == START
        with pytest.raises(HTTPException):
            await save_opening(db, "owner", "owner", opening(amount="90", request="other123"))
        assert len((await read(db, "owner"))["movements"]) == 0
    run(scenario)


def test_changed_retry_and_reused_receipt_rejected():
    async def scenario(db):
        await started(db)
        await db[RECEIPTS].insert_one({"_id": "receipt", "owner_id": "owner", "sha256": "hash"})
        p = movement(); p["receipt_id"] = "receipt"
        await create_movement(db, "owner", "owner", p)
        for q in ({**p, "amount": "301"}, {**p, "request_id": "movement2"}):
            with pytest.raises(HTTPException):
                await create_movement(db, "owner", "owner", q)
        assert len((await read(db, "owner"))["movements"]) == 1
    run(scenario)


def test_transfer_conserves_liquidity_and_recurring_never_changes_bank():
    async def scenario(db):
        await started(db)
        p = movement(); p.update(kind="transfer", party_type="cash", party_id="cash", amount="200")
        await create_movement(db, "owner", "owner", p)
        await db.operating_recurring_obligations_v2.insert_one({"id": "rent", "user_id": "owner", "status": "active",
            "start_date": "2026-10-01", "cycle": "monthly", "period_amount": "3100"})
        state = await refresh(db, "owner", clock=NOW)
        result = report(state)
        assert result["summary"]["actual_liquidity"] == "1000.00"
        assert next(r for r in result["parties"] if r["party_type"] == "cash")["actual"] == "200.00"
    run(scenario)


def test_provider_net_settlement_fee_not_second_bank_deduction():
    async def scenario(db):
        await save_opening(db, "owner", "owner", opening(), clock=START)
        await save_opening(db, "owner", "owner", opening("provider", "tabby", "500", request="provider1"), clock=START)
        await save_opening(db, "owner", "owner", {"request_id": "startnow1"}, finish=True, clock=START)
        oid = next(k for k,v in (await read(db,"owner"))["openings"].items() if v["party_type"] == "provider")
        await db[RECEIPTS].insert_one({"_id":"settlement-proof","owner_id":"owner","sha256":"h"})
        p = movement(); p.update(party_type="provider",party_id="tabby",direction="incoming",kind="settlement",amount="480",
            actual_fee_amount="20",receipt_id="settlement-proof",allocations=[{"obligation_id":oid,"amount":"500"}])
        await create_movement(db,"owner","owner",p)
        result=report(await read(db,"owner"))
        assert result["summary"]["actual_liquidity"] == "1480.00"
        assert result["summary"]["receivable"] == "0.00"
    run(scenario)


def test_http_permissions_tenant_isolation_and_legacy_rejection():
    async def scenario(db):
        await db.suppliers.insert_one({"id":"legacy","user_id":"owner"})
        app = FastAPI()
        async def principal(x_test_user: str = Header(default="owner")):
            return {"id":x_test_user}
        app.include_router(make_operational_balance_router(db, principal),prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://isolated") as client:
            base="/api/operational-balances"
            assert (await client.get(base+"/context")).status_code == 200
            assert (await client.post(base+"/openings",json=opening(),headers={"x-test-user":"viewer"})).status_code == 403
            assert (await client.post(base+"/openings",json=opening("supplier","legacy"))).status_code == 409
            assert (await client.post(base+"/openings",json=opening(),headers={"x-test-user":"other"})).status_code == 409
            assert (await client.post(base+"/finish",json={"request_id":"finish123","opening":opening()})).status_code == 200
            assert (await client.get(base+"/reports")).json()["summary"]["actual_liquidity"] == "1000.00"
            assert await db.general_ledger.count_documents({}) == 0
            assert await db.mz2_atomic_owners.count_documents({}) == 0
            frozen=await client.post(base+"/freeze",json={"request_id":"freeze123","reason":"نهاية النظام"})
            assert frozen.status_code == 200
            assert (await client.post(base+"/movements",json=movement())).status_code == 409
    run(scenario)


async def seed_engine(db, sources, at=NOW):
    async def apply(state):
        state.update(reconcile(state, sources, at))
        return state
    return await mutate(db, "owner", apply)


async def seed_ad_account(db):
    await db.mezan_integration_accounts_v2.insert_one({"user_id": "owner", "mezan_integration_account_id": "ia",
        "provider": "meta_ads", "external_account_id": "external", "display_name": "حساب المتجر", "connection_status": "connected", "currency": "SAR", "timezone": "Asia/Riyadh"})
    await db.mz2_ad_account_bindings_v2.insert_one({"_id": "ad", "user_id": "owner", "platform": "meta",
        "integration_account_id": "ia", "platform_account_id": "external", "funding_mode": "prepaid",
        "confirmed_by": "owner", "confirmed_at": START, "status": "active"})


def test_prepaid_spend_consumes_wallet_funding_cash_once():
    async def scenario(db):
        await seed_ad_account(db)
        await save_opening(db, "owner", "owner", opening(), clock=START)
        await save_opening(db, "owner", "owner", opening("ad_account", "ad", "500", request="advertis1"), clock=START)
        await save_opening(db, "owner", "owner", {"request_id": "startnow1"}, finish=True, clock=START)
        await seed_engine(db, {"ad_snapshots": [{"account_id": "ad", "date": "2026-10-06", "observed_at": NOW,
            "amount": "200", "currency": "SAR", "funding_type": "prepaid", "complete": True, "closed": True, "day_ended": True}]})
        first = report(await read(db, "owner"))
        ad = next(r for r in first["parties"] if r["party_type"] == "ad_account")
        assert ad["outstanding_receivable"] == "300.00"
        assert ad["outstanding_payable"] == "0.00"
        p = movement(); p.update(kind="wallet_funding", party_type="ad_account", party_id="ad", amount="100")
        await create_movement(db, "owner", "owner", p)
        final = report(await read(db, "owner"))
        assert final["summary"]["actual_liquidity"] == "900.00"
        assert next(r for r in final["parties"] if r["party_type"] == "ad_account")["outstanding_receivable"] == "400.00"
        for invalid in ({**p, "request_id": "badfund1", "direction": "incoming"},
                        {**p, "request_id": "badfund2", "kind": "settlement", "amount": "200", "allocations": [{"obligation_id": "advertising:ad:2026-10-06", "amount": "200"}]}):
            with pytest.raises(HTTPException):
                await create_movement(db, "owner", "owner", invalid)
    run(scenario)


def test_employee_net_settlement_and_advance_are_distinct():
    async def scenario(db):
        await db.mezan_employees_v2.insert_one({"id": "e", "user_id": "owner", "name": "موظف", "status": "active"})
        await save_opening(db, "owner", "owner", opening(), clock=START)
        await save_opening(db, "owner", "owner", opening("employee", "e", "150", request="employee1"), clock=START)
        await save_opening(db, "owner", "owner", {"request_id": "startnow1"}, finish=True, clock=START)
        await seed_engine(db, {"employees": [{"id": "e", "currency": "SAR", "salary_revisions": [{"effective_from": "2026-01-01", "salary": "3100", "status": "active"}]}]})
        # Two days accrued 200, baseline debt 150, salary payable is 50.
        p = movement(); p.update(party_type="employee", party_id="e", amount="100", kind="settlement", allocations=[{"obligation_id": "salary:e:2026-10-06", "amount": "100"}])
        with pytest.raises(HTTPException) as exc:
            await create_movement(db, "owner", "owner", p)
        assert exc.value.detail["code"] == "operational_employee_net_over_settlement"
        p.update(kind="payment", allocations=[])
        item = await create_movement(db, "owner", "owner", p)
        assert sum(float(a["amount"]) for a in item["allocations"]) == 50
        employee = next(r for r in report(await read(db, "owner"))["parties"] if r["party_type"] == "employee")
        assert employee["outstanding_receivable"] == "50.00"
        assert employee["outstanding_payable"] == "0.00"
    run(scenario)


def test_correction_is_documented_non_cash_adjustment_no_double_allocation():
    async def scenario(db):
        await started(db)
        p = movement(); p.update(kind="correction", amount="100", direction="incoming", bank_id=None, note="تصحيح رصيد المورد")
        await create_movement(db, "owner", "owner", p)
        result = report(await read(db, "owner"))
        assert result["summary"]["actual_liquidity"] == "1000.00"
        assert result["summary"]["payable"] == "400.00"
        assert result["summary"]["receivable"] == "0.00"
        for invalid in ({**p, "request_id": "correction2", "bank_id": "bank"}, {**p, "request_id": "correction3", "allocations": [{"obligation_id": "anything", "amount": "1"}]}):
            with pytest.raises(HTTPException):
                await create_movement(db, "owner", "owner", invalid)
    run(scenario)


def test_supplier_invoice_return_immediate_atomic_and_does_not_reconfirm_receipt():
    async def scenario(db):
        await started(db, supplier="0")
        sources = {"orders": [{"id": "o", "created_at": "2026-10-06T09:00:00+00:00", "currency": "SAR", "status": "completed",
            "items": [{"id": "line", "cost": "100", "supplier_id": "supplier"}]}],
            "supplier_receipts": [{"id": "issued-invoice:line", "order_id": "o", "item_id": "line", "supplier_id": "supplier", "amount": "100", "accepted_at": NOW}]}
        await seed_engine(db, sources)
        await db[RECEIPTS].insert_many([{"_id": "return1", "owner_id": "owner", "sha256": "r1"}, {"_id": "return2", "owner_id": "owner", "sha256": "r2"}])
        payload = {"request_id": "returnreq1", "receipt_id": "issued-invoice:line", "evidence_receipt_id": "return1", "amount": "30", "note": "قبول المورد للمرتجع"}
        first = await supplier_return(db, "owner", "owner", payload, clock=NOW)
        assert first == await supplier_return(db, "owner", "owner", payload, clock=NOW)
        assert report(await read(db, "owner"))["summary"]["payable"] == "70.00"
        p = movement(); p.update(amount="50")
        await create_movement(db, "owner", "owner", p, clock=NOW)
        second_return = {**payload, "request_id": "returnreq2", "evidence_receipt_id": "return2", "amount": "30"}
        returned = await supplier_return(db, "owner", "owner", second_return, clock=NOW)
        assert returned == await supplier_return(db, "owner", "owner", second_return, clock=NOW)
        state = await read(db, "owner")
        sources["supplier_returns"] = state["supplier_returns"]
        sources["orders"][0]["status"] = "cancelled"
        await seed_engine(db, sources)
        after = report(await read(db, "owner"))
        assert after["summary"]["payable"] == "0.00"
        assert after["summary"]["receivable"] == "10.00"
        p.update(request_id="returncash1", kind="collection", direction="incoming", amount="4")
        collected = await create_movement(db, "owner", "owner", p, clock=NOW)
        assert collected == await create_movement(db, "owner", "owner", p, clock=NOW)
        assert report(await read(db, "owner"))["summary"]["receivable"] == "6.00"
        p.update(request_id="returncash2", amount="6")
        await create_movement(db, "owner", "owner", p, clock=NOW)
        final = report(await read(db, "owner"))
        assert final["summary"]["receivable"] == "0.00"
        assert final["summary"]["actual_liquidity"] == "960.00"
    run(scenario)


def test_same_document_hash_cannot_be_uploaded_twice_for_two_movements():
    async def scenario(db):
        await started(db)
        await db[RECEIPTS].insert_many([{"_id": "proof1", "owner_id": "owner", "sha256": "identical"}, {"_id": "proof2", "owner_id": "owner", "sha256": "identical"}])
        p = movement(); p["receipt_id"] = "proof1"
        await create_movement(db, "owner", "owner", p)
        with pytest.raises(HTTPException):
            await create_movement(db, "owner", "owner", {**p, "request_id": "different", "receipt_id": "proof2"})
    run(scenario)


def test_existing_issued_supplier_invoice_only_full_partial_cancel_and_payment():
    async def scenario(db):
        await started(db, supplier="0")
        await db.unified_orders.insert_one({"user_id": "owner", "order_number": "10", "raw_by_source": {"salla_direct": {
            "id": "100", "reference_id": "10", "created_at": "2026-10-06T01:00:00+00:00", "status": "in_progress", "currency": "SAR",
            "total": {"amount": "300", "currency": "SAR"}, "items": [{"id": "line", "product_id": "p1", "quantity": 2}]}}})
        await db.mezan_product_cost_profiles_v2.insert_one({"user_id": "owner", "salla_product_id": "p1", "base_cost": "20"})
        await db.mezan_preparation_pieces_v1.insert_many([{"user_id": "owner", "id": f"piece{i}", "order_number": "10",
            "order_item_id": "salla:10:line", "unit_index": i, "supplier_id": "supplier", "status": "received", "received_at": NOW} for i in (1,2)])
        initial = await refresh(db, "owner", clock=NOW)
        # Physical receipt / preparation completion alone cannot confirm cost.
        assert report(initial)["summary"]["payable"] == "0.00"
        assert report(initial)["summary"]["expected_payable"] == "40.00"
        await db.unified_orders.update_one({"user_id":"owner"}, {"$set": {"raw_by_source.salla_direct.status": "cancelled"}})
        cancelled = await refresh(db, "owner", clock=NOW)
        assert report(cancelled)["summary"]["expected_payable"] == "0.00"
        await db.unified_orders.update_one({"user_id":"owner"}, {"$set": {"raw_by_source.salla_direct.status": "in_progress"}})
        await db.mezan_supplier_invoices_v2.insert_one({"id": "invoice", "user_id": "owner", "supplier_id": "supplier", "approved_at": NOW,
            "lines": [{"piece_ids": ["piece1"], "product_price_authority": "mezan_v2", "product_unit_price_halalas": 2000}]})
        partial = await refresh(db, "owner", clock=NOW)
        assert report(partial)["summary"]["payable"] == "20.00"
        assert report(partial)["summary"]["expected_payable"] == "20.00"
        assert report(await refresh(db, "owner", clock=NOW))["summary"] == report(partial)["summary"]
        await db.mezan_supplier_invoices_v2.insert_one({"id": "invoice2", "user_id": "owner", "supplier_id": "supplier", "approved_at": NOW,
            "lines": [{"piece_ids": ["piece2"], "product_price_authority": "mezan_v2", "product_unit_price_halalas": 2000}]})
        full = await refresh(db, "owner", clock=NOW)
        assert report(full)["summary"]["payable"] == "40.00"
        assert report(full)["summary"]["expected_payable"] == "0.00"
        await db.unified_orders.update_one({"user_id":"owner"}, {"$set": {"raw_by_source.salla_direct.status": "cancelled"}})
        assert report(await refresh(db, "owner", clock=NOW))["summary"]["payable"] == "40.00"
        p = movement(); p["amount"] = "10"
        await create_movement(db,"owner","owner",p,clock=NOW)
        final = await read(db,"owner")
        assert report(final)["summary"]["payable"] == "30.00"
        assert not final.get("supplier_receipts")
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)


def test_provider_refund_after_settlement_creates_payable_credit_and_pays_once():
    async def scenario(db):
        await save_opening(db, "owner", "owner", {"request_id": "finish123", "opening": opening()}, finish=True, clock=START)
        payment = {"provider": "tabby", "gross": "500", "captures": [{"id": "capture", "amount": "500", "captured_at": NOW}]}
        sources = {"orders": [{"id": "paidorder", "created_at": "2026-10-06T09:00:00+00:00", "currency": "SAR", "status": "completed", "items": [], "payment": payment}],
            "fee_policies": [{"id": "policy", "provider": "tabby", "currency": "SAR", "status": "active", "effective_from": "2026-01-01", "percentage": "2", "fixed_amount": "0", "vat_treatment": "exempt", "refund_fee_treatment": "recalculate"}]}
        await seed_engine(db, sources)
        await db[RECEIPTS].insert_one({"_id": "platform-settlement", "owner_id": "owner", "sha256": "platform-evidence"})
        p = movement(); p.update(party_type="provider", party_id="tabby", kind="settlement", direction="incoming", amount="480", actual_fee_amount="20",
            receipt_id="platform-settlement", allocations=[{"obligation_id": "provider:paidorder", "amount": "500"}])
        await create_movement(db, "owner", "owner", p)
        settled_metrics = report(await read(db,"owner"))["details"]["provider_reports"]["provider:paidorder"]
        assert settled_metrics["estimated_fees_original"] == "10.00"
        assert settled_metrics["actual_fees"] == "20.00"
        assert settled_metrics["expected_receivable"] == settled_metrics["settled"] == "480.00"
        assert settled_metrics["outstanding"] == "0.00"
        payment["refunds"] = [{"id": "refund1", "amount": "100", "status": "executed"}]
        credited = await seed_engine(db, sources)
        assert report(credited)["summary"]["payable"] == "100.00"
        assert report(credited)["summary"]["receivable"] == "0.00"
        assert report(await seed_engine(db, sources))["summary"] == report(credited)["summary"]
        refund = movement(); refund.update(request_id="refundpay1", party_type="provider", party_id="tabby", kind="refund", amount="40")
        first = await create_movement(db, "owner", "owner", refund)
        assert first["allocations"] == [{"obligation_id": "credit:provider:paidorder", "amount": "40.00"}]
        assert first == await create_movement(db, "owner", "owner", refund)
        assert report(await read(db, "owner"))["summary"]["payable"] == "60.00"
        refund.update(request_id="refundpay2", amount="60")
        await create_movement(db, "owner", "owner", refund)
        final = report(await seed_engine(db, sources))
        assert final["summary"]["payable"] == "0.00"
        assert final["summary"]["actual_liquidity"] == "1380.00"
        metrics = final["details"]["provider_reports"]["provider:paidorder"]
        assert metrics["outstanding"] == "0.00"
        assert metrics["expected_receivable"] == metrics["settled"] == "380.00"
        assert len((await read(db,"owner"))["movements"]) == 3
    run(scenario)


def test_finish_captures_ad_baseline_and_refresh_only_counts_post_start_delta():
    async def scenario(db):
        await seed_ad_account(db)
        await db.mezan_meta_performance_daily_v2.insert_one({"user_id": "owner", "provider": "meta_ads", "ad_account_id": "external",
            "date": "2026-10-05", "observed_at": START, "spend_native": "80", "currency_native": "SAR", "account_timezone": "Asia/Riyadh"})
        await save_opening(db, "owner", "owner", {"request_id": "finish123", "opening": opening()}, finish=True, clock=START)
        state = await read(db, "owner")
        assert state["source_baselines"]["ad_days"][0]["amount"] == "80"
        assert state["source_baselines"]["ad_days"][0]["verified"] is True
        at = "2026-10-05T13:00:00+00:00"
        await db.mezan_meta_performance_daily_v2.update_one({"user_id": "owner"}, {"$set": {"observed_at": at, "spend_native": "220"}})
        refreshed = await refresh(db, "owner", clock=at)
        ad = next(r for r in report(refreshed)["parties"] if r["party_type"] == "ad_account")
        assert ad["expected_payable"] == "140.00"
        assert ad["name"] == "meta: حساب المتجر"
        assert refreshed["source_baselines"] == state["source_baselines"]
    run(scenario)


def test_accounting_activation_blocks_refresh_but_freeze_preserves_data_timestamp():
    async def scenario(db):
        await started(db)
        state = await refresh(db, "owner", clock=NOW)
        await db.mz2_atomic_owners.insert_one({"_id": "owner", "ledger_backend_state": "v2_active"})
        with pytest.raises(HTTPException):
            await refresh(db,"owner",clock="2026-10-08T12:00:00+00:00")
        cutoff = "2026-10-08T13:00:00+00:00"
        await freeze(db,"owner","system",{"request_id":"freezeauto1","reason":"المحاسبة مفعلة"},clock=cutoff)
        frozen = await read(db,"owner")
        assert frozen["snapshot"]["report"]["as_of"] == state["engine"]["as_of"] == NOW
        assert frozen["cutoff_at"] == cutoff
        assert report(await refresh(db,"owner",clock=cutoff))["summary"]["actual_liquidity"] == "1000.00"
    run(scenario)


def test_unknown_order_is_setup_conflict_not_internal_error():
    async def scenario(db):
        await started(db)
        await db[RECEIPTS].insert_one({"_id":"order-proof","owner_id":"owner","sha256":"missing-order"})
        p = movement(); p.update(order_number="missing",receipt_id="order-proof")
        with pytest.raises(HTTPException) as exc:
            await create_movement(db,"owner","owner",p)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "operational_order_ineligible"
        assert not (await read(db,"owner"))["movements"]
    run(scenario)


def test_partial_platform_fee_replacement_keeps_only_unsettled_estimate():
    async def scenario(db):
        await save_opening(db,"owner","owner",{"request_id":"finish123","opening":opening()},finish=True,clock=START)
        sources = {"orders": [{"id":"fees","created_at":"2026-10-06T09:00:00+00:00","currency":"SAR","items":[],
            "payment":{"provider":"tabby","gross":"1000","captures":[{"id":"fee-capture","amount":"1000","captured_at":NOW}]}}],
            "fee_policies":[{"id":"fee-policy","provider":"tabby","currency":"SAR","status":"active","effective_from":"2026-01-01",
                             "percentage":"3","fixed_amount":"0","vat_treatment":"exempt"}]}
        await seed_engine(db,sources)
        await db[RECEIPTS].insert_many([{"_id":"fee-proof1","owner_id":"owner","sha256":"fee-one"},
                                      {"_id":"fee-proof2","owner_id":"owner","sha256":"fee-two"}])
        p=movement();p.update(party_type="provider",party_id="tabby",kind="settlement",direction="incoming",amount="190",actual_fee_amount="10",
                             receipt_id="fee-proof1",allocations=[{"obligation_id":"provider:fees","amount":"200"}])
        await create_movement(db,"owner","owner",p)
        metrics=report(await read(db,"owner"))["details"]["provider_reports"]["provider:fees"]
        assert metrics["estimated_fees_remaining"] == "24.00"
        assert metrics["expected_receivable"] == "966.00"
        assert metrics["settled"] == "190.00"
        assert metrics["outstanding"] == "776.00"
        p.update(request_id="feesettle2",amount="790",receipt_id="fee-proof2",allocations=[{"obligation_id":"provider:fees","amount":"800"}])
        await create_movement(db,"owner","owner",p)
        result=report(await read(db,"owner"))
        metrics=result["details"]["provider_reports"]["provider:fees"]
        assert metrics["actual_fees"] == "20.00"
        assert metrics["estimated_fees_remaining"] == "0.00"
        assert metrics["expected_receivable"] == metrics["settled"] == "980.00"
        assert metrics["outstanding"] == "0.00"
        assert result["summary"]["actual_liquidity"] == "1980.00"
    run(scenario)


def test_custody_funding_spending_return_and_owner_draw_are_separate_cash_flows():
    async def scenario(db):
        await db.mezan_employees_v2.insert_many([{"id":"ahmed","user_id":"owner","name":"أحمد","status":"active"},
                                               {"id":"foreign","user_id":"other","name":"آخر","status":"active"}])
        for p in (opening(amount="10000"), opening("cash","cash","3000",request="cash-start"),
                  opening("employee_custody","ahmed","0",request="custody-start"),
                  opening("employee","ahmed","1000",request="salary-start"),
                  opening("operating_expense","fuel","0","for_party",request="expense-start"),
                  opening("owner_withdrawal","owner","0",request="withdraw-start")):
            await save_opening(db,"owner","owner",p,clock=START)
        await save_opening(db,"owner","owner",{"request_id":"finish123"},finish=True,clock=START)
        fund=movement();fund.update(party_type="employee_custody",party_id="ahmed",amount="5000",source_account_type="bank")
        result=await create_movement(db,"owner","owner",fund)
        assert result == await create_movement(db,"owner","owner",fund)
        await db[RECEIPTS].insert_one({"_id":"fuel-proof","owner_id":"owner","sha256":"fuel-evidence"})
        expense=movement();expense.update(request_id="custody-spend",party_type="operating_expense",party_id="fuel",bank_id="ahmed",
                                         source_account_type="employee_custody",amount="500",receipt_id="fuel-proof")
        spent=await create_movement(db,"owner","staff",expense,source="employee_app")
        assert spent == await create_movement(db,"owner","staff",expense,source="employee_app")
        current=report(await read(db,"owner"))
        assert current["summary"]["actual_liquidity"] == "8000.00"
        assert current["summary"]["custody_remaining"] == "4500.00"
        assert current["summary"]["operating_expenses_paid"] == "500.00"
        returning={**fund,"request_id":"custody-return","kind":"collection","direction":"incoming","amount":"1000"}
        await create_movement(db,"owner","owner",returning)
        withdraw=movement();withdraw.update(request_id="owner-draw",party_type="owner_withdrawal",party_id="owner",bank_id="cash",source_account_type="cash",amount="2000")
        draw=await create_movement(db,"owner","owner",withdraw)
        assert draw == await create_movement(db,"owner","owner",withdraw)
        final=report(await read(db,"owner"))
        assert final["summary"]["actual_liquidity"] == "7000.00"
        assert final["summary"]["custody_remaining"] == "3500.00"
        assert final["summary"]["owner_withdrawals"] == "2000.00"
        assert final["summary"]["operating_expenses_paid"] == "500.00"
        salary=next(r for r in final["parties"] if r["party_type"]=="employee")
        assert salary["outstanding_receivable"] == "1000.00"
        custody=final["details"]["employee_custody"][0]
        assert (custody["custody_funded"],custody["custody_spent"],custody["custody_returned"],custody["custody_remaining"]) == ("5000.00","500.00","1000.00","3500.00")
        assert len(custody["receipts"]) == 3
        assert any(r["receipt_id"]=="fuel-proof" and r["source"]=="employee_app" for r in custody["receipts"])
        assert next(r for r in final["parties"] if r["party_type"]=="operating_expense")["outstanding_receivable"] == "0.00"
        for invalid in ({**expense,"request_id":"over-spend","amount":"3501","receipt_id":None},
                        {**expense,"request_id":"other-owner","bank_id":"foreign","receipt_id":None},
                        {**expense,"request_id":"wrong-type","source_account_type":"bank_auto","receipt_id":None},
                        {**returning,"request_id":"over-return","amount":"3501"},
                        {**withdraw,"request_id":"other-draw","party_id":"other"}):
            with pytest.raises(HTTPException):
                await create_movement(db,"owner","owner",invalid)
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)


def test_custody_concurrent_spends_cannot_overdraw_and_correction_is_separate():
    async def scenario(db):
        await db.mezan_employees_v2.insert_one({"id":"e","user_id":"owner","name":"موظف","status":"active"})
        await save_opening(db,"owner","owner",{"request_id":"finish123","opening":opening("employee_custody","e","100")},finish=True,clock=START)
        p=movement();p.update(party_type="operating_expense",party_id="fuel",bank_id="e",source_account_type="employee_custody",amount="80")
        results=await asyncio.gather(create_movement(db,"owner","owner",p),create_movement(db,"owner","owner",{**p,"request_id":"another-spend"}),return_exceptions=True)
        assert sum(isinstance(r,HTTPException) for r in results)==1
        correction=movement();correction.update(request_id="custody-fix",party_type="employee_custody",party_id="e",kind="correction",bank_id=None,amount="10",direction="incoming",note="تصحيح موثق")
        await create_movement(db,"owner","owner",correction)
        result=report(await read(db,"owner"))
        assert result["summary"]["actual_liquidity"] == "0.00"
        assert result["summary"]["custody_remaining"] == "30.00"
        assert result["summary"]["operating_expenses_paid"] == "80.00"
        assert result["details"]["employee_custody"][0]["custody_adjustments"] == "10.00"
    run(scenario)


@pytest.mark.parametrize("fraction,wallet,debt", [("0","500.00","200.00"),("0.4","420.00","220.00"),("1","300.00","0.00")])
def test_hybrid_wallet_and_postpaid_debt_are_never_automatically_netted(fraction,wallet,debt):
    async def scenario(db):
        await seed_ad_account(db)
        await db.mz2_ad_account_bindings_v2.update_one({"_id":"ad"},{"$set":{"funding_mode":"hybrid","hybrid_policy":"explicit_split","version":2}})
        await db.mz2_ad_automation_policies_v2.insert_one({"user_id":"owner","id":"split-policy","platform":"meta","integration_account_id":"ia",
            "version":1,"binding_version":2,"status":"active","confirmed_by":"owner","confirmed_at":START,"start_date":"2026-10-01","wallet_fraction":fraction})
        await save_opening(db,"owner","owner",opening(),clock=START)
        await save_opening(db,"owner","owner",opening("ad_account","ad","500",request="hybrid-baseline"),clock=START)
        if fraction == "0.4":
            await save_opening(db,"owner","owner",opening("ad_account","ad","100","for_party",request="hybrid-credit-baseline"),clock=START)
            with pytest.raises(HTTPException):
                await save_opening(db,"owner","owner",opening("ad_account","ad","1",request="hybrid-duplicate"),clock=START)
            before = next(r for r in report(await read(db,"owner"))["parties"] if r["party_type"] == "ad_account")
            assert before["funding_mode"] == "hybrid"
            assert before["ad_wallet_balance"] == "500.00" and before["ad_payable"] == "100.00"
        await save_opening(db,"owner","owner",{"request_id":"finish123"},finish=True,clock=START)
        sources={"ad_snapshots":[{"account_id":"ad","date":"2026-10-06","observed_at":NOW,"amount":"200","currency":"SAR",
                                 "funding_type":"hybrid","wallet_fraction":fraction,"closed":True,"complete":True,"day_ended":True}]}
        state=await seed_engine(db,sources)
        assert report(await seed_engine(db,sources)) == report(state)
        row=next(r for r in report(state)["parties"] if r["party_type"]=="ad_account")
        assert row["ad_wallet_balance"] == row["outstanding_receivable"] == wallet
        assert row["ad_payable"] == row["outstanding_payable"] == debt
        assert report(state)["summary"]["actual_liquidity"] == "1000.00"
        if fraction == "0.4":
            funding=movement();funding.update(kind="wallet_funding",party_type="ad_account",party_id="ad",amount="50")
            await create_movement(db,"owner","owner",funding)
            baseline_id = next(k for k,v in (await read(db,"owner"))["openings"].items() if v["party_type"]=="ad_account" and v["direction"]=="for_party")
            pay=movement();pay.update(request_id="hybrid-creditpay",kind="settlement",party_type="ad_account",party_id="ad",amount="220",
                                    allocations=[{"obligation_id":"advertising:ad:2026-10-06:credit","amount":"120"}, {"obligation_id":baseline_id,"amount":"100"}])
            await create_movement(db,"owner","owner",pay)
            final=report(await read(db,"owner"))
            ad=next(r for r in final["parties"] if r["party_type"]=="ad_account")
            assert ad["ad_wallet_balance"]=="470.00" and ad["ad_payable"]=="0.00"
            assert final["summary"]["actual_liquidity"] == "730.00"
    run(scenario)


def test_missing_hybrid_policy_blocks_opening_and_cash_movement():
    async def scenario(db):
        await seed_ad_account(db)
        await db.mz2_ad_account_bindings_v2.update_one({"_id":"ad"},{"$set":{"funding_mode":"hybrid","hybrid_policy":"explicit_split","version":2}})
        with pytest.raises(HTTPException):
            await save_opening(db,"owner","owner",opening("ad_account","ad","500",request="hybrid-baseline"),clock=START)
        await save_opening(db,"owner","owner",{"request_id":"finish123","opening":opening()},finish=True,clock=START)
        funding=movement();funding.update(kind="wallet_funding",party_type="ad_account",party_id="ad",amount="50")
        with pytest.raises(HTTPException):
            await create_movement(db,"owner","owner",funding)
        final=await read(db,"owner")
        assert not final["movements"]
        assert report(final)["summary"]["actual_liquidity"]=="1000.00"
    run(scenario)


def test_open_day_ad_funding_transitions_replace_estimate_atomically():
    state={"status":"active","started_at":START,"openings":{},"movements":[]}
    snapshot={"account_id":"ad","date":"2026-10-06","observed_at":NOW,"amount":"200","currency":"SAR","funding_type":"prepaid"}
    state=reconcile(state,{"ad_snapshots":[snapshot]},NOW)
    snapshot.update(funding_type="hybrid",wallet_fraction="0.4",amount="300")
    state=reconcile(state,{"ad_snapshots":[snapshot]},NOW)
    assert sum(float(r["expected"]) for r in state["engine"]["obligations"].values())==300
    snapshot.update(funding_type="postpaid",amount="450")
    state=reconcile(state,{"ad_snapshots":[snapshot]},NOW)
    assert sum(float(r["expected"]) for r in state["engine"]["obligations"].values())==450
    final=report(state)
    assert len(final["obligations"])==1
    assert final["parties"][0]["funding_mode"]=="postpaid"
    assert final["summary"]["expected_payable"]=="450.00"


def test_negative_prepaid_wallet_is_visible_with_explicit_issue():
    state={"status":"active","started_at":START,"openings":{},"movements":[]}
    snapshot={"account_id":"ad","date":"2026-10-06","observed_at":NOW,"amount":"200","currency":"SAR","funding_type":"prepaid",
              "complete":True,"closed":True,"day_ended":True}
    state=reconcile(state,{"ad_snapshots":[snapshot]},NOW)
    result=report(state)
    assert result["parties"][0]["ad_wallet_balance"] == "-200.00"
    assert any(i["code"]=="advertising_prepaid_wallet_negative" and i["amount"]=="-200.00" for i in result["issues"])


def test_explicit_recurring_payment_confirms_paid_portion_and_later_invoice_replaces_estimate():
    async def scenario(db):
        await db.mz2_external_persons_v2.insert_one({"user_id":"owner","id":"landlord","name":"المؤجر","currency":"SAR","status":"active"})
        await save_opening(db,"owner","owner",{"request_id":"finish123","opening":opening(amount="10000")},finish=True,clock=START)
        sources={"recurring":[{"id":"rent-period","party_id":"landlord","currency":"SAR","due_at":NOW,"amount":"5000","confirmed":False}]}
        initial=await seed_engine(db,sources)
        initial_report=report(initial)
        assert initial_report["summary"]["actual_liquidity"]=="10000.00"
        assert initial_report["summary"]["expected_payable"]=="5000.00"
        row=initial_report["obligations"][0]
        assert row["available_to_pay"]=="5000.00" and row["pending_confirmation"] is True
        payment=movement();payment.update(party_type="external_person",party_id="landlord",kind="settlement",amount="2000",
                                          allocations=[{"obligation_id":"recurring:rent-period","amount":"2000"}])
        first=await create_movement(db,"owner","owner",payment)
        assert first==await create_movement(db,"owner","owner",payment)
        after=report(await seed_engine(db,sources))
        row=after["obligations"][0]
        assert (row["expected"],row["confirmed"],row["settled"],row["outstanding"],row["available_to_pay"]) == ("3000.00","2000.00","2000.00","0.00","3000.00")
        assert after["summary"]["actual_liquidity"]=="8000.00"
        assert after["summary"]["operating_expenses_paid"]=="2000.00"
        assert after["summary"]["owner_withdrawals"]=="0.00"
        sources["recurring"][0]["confirmed"]=True
        invoiced=await seed_engine(db,sources)
        final=report(invoiced)
        row=final["obligations"][0]
        assert (row["expected"],row["confirmed"],row["settled"],row["outstanding"]) == ("0.00","5000.00","2000.00","3000.00")
        assert report(await seed_engine(db,sources))==final
        assert len(invoiced["movements"])==1
        assert any(a["action"]=="recurring_payment_confirmed" for a in invoiced["audit"])
    run(scenario)


def test_old_refresh_clock_cannot_rewind_state_and_stable_names_do_not_grow_audit():
    async def scenario(db):
        await save_opening(db,"owner","owner",{"request_id":"finish123","opening":opening()},finish=True,clock=START)
        await db.unified_orders.insert_one({"user_id":"owner","order_number":"10","raw_by_source":{"salla_direct":{
            "id":"100","reference_id":"10","created_at":"2026-10-06T01:00:00+00:00","status":"in_progress","currency":"SAR",
            "total":{"amount":"300","currency":"SAR"},"items":[{"id":"line","product_id":"p1","quantity":2}]}}})
        await db.mezan_product_cost_profiles_v2.insert_one({"user_id":"owner","salla_product_id":"p1","base_cost":"20"})
        first=await refresh(db,"owner",clock=NOW)
        count=len(first["engine"]["audit"])
        newer=await refresh(db,"owner",clock="2026-10-07T13:00:00+00:00")
        assert len(newer["engine"]["audit"])==count
        assert next(iter(newer["engine"]["obligations"].values()))["name"]=="تكلفة منتجات غير مسندة"
        older=await refresh(db,"owner",clock=NOW)
        assert older==newer
        assert (await read(db,"owner"))["revision"]==newer["revision"]
    run(scenario)
