"""Match the order-detail payment summary to the provider ledger in isolated Mongo."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_service import create_movement, refresh, report, save_opening
from operational_balance_store import read
from order_engine.repository import MongoOrderRepository
from order_engine.service import get_order
from test_operational_balance_integration import START, movement, opening, run


CREATED = "2026-10-05T11:00:00+00:00"
T1 = "2026-10-06T09:00:00+00:00"
T2 = "2026-10-06T10:00:00+00:00"
T3 = "2026-10-06T11:00:00+00:00"
T4 = "2026-10-06T12:00:00+00:00"
T5 = "2026-10-06T13:00:00+00:00"


def order_document(method, paid, remaining, *, created=CREATED, number="8101"):
    raw = {
        "id": "51" + number, "reference_id": number, "created_at": created, "updated_at": created,
        "currency": "SAR", "total": {"amount": "100.00", "currency": "SAR"},
        "status": {"name": "بانتظار المراجعة", "slug": "under_review"}, "items": [],
        "payment_method": method,
        "payment_actions": {"remaining_action": remaining_action(paid, remaining)},
    }
    return {"user_id": "owner", "salla_order_id": raw["id"], "order_number": number, "order_date": created,
            "raw_by_source": {"salla_direct": raw}}


def remaining_action(paid, remaining):
    return {"paid_amount": {"amount": paid, "currency": "SAR"},
            "remaining_amount": None if remaining is None else {"amount": remaining, "currency": "SAR"},
            "has_remaining_amount": remaining is not None and Decimal(remaining) > 0}


async def start(db, provider):
    await save_opening(db, "owner", "owner", opening(), clock=START)
    await save_opening(db, "owner", "owner", opening("provider", provider, "500", request="opening-provider"), clock=START)
    await save_opening(db, "owner", "owner", {"request_id": "finish-opening"}, finish=True, clock=START)
    return (await read(db, "owner"))["openings"]


def provider_view(state, provider):
    return next(p for p in report(state)["parties"] if p["party_type"] == "provider" and p["party_id"] == provider)


@pytest.mark.parametrize(("method", "provider", "paid", "remaining", "outstanding", "status"), [
    ("mada", "salla", "100.00", None, "600.00", "paid"),
    ("tamara_installment", "tamara", "45.00", "55.00", "545.00", "partial"),
    ("tabby_installment", "tabby", "100.00", "0.00", "600.00", "paid"),
    ("mada", "salla", "0.00", "100.00", "500.00", "unpaid"),
])
def test_saved_order_page_balance_flows_to_right_platform_without_a_transaction_fetch(method, provider, paid, remaining, outstanding, status):
    async def scenario(db):
        initial = await start(db, provider)
        doc = order_document(method, paid, remaining)
        await db.unified_orders.insert_one(deepcopy(doc))
        page = (await get_order(MongoOrderRepository(db), user_id="owner", order_number=doc["order_number"])).payment
        assert page.source_balance is not None
        assert (page.source_balance.paid, page.source_balance.remaining) == (paid, remaining or "0.00")
        assert (page.collection_status, page.paid_amount, page.remaining_amount) == (
            status, float(paid), float(remaining or 0))
        state = await refresh(db, "owner", clock=T1)
        assert provider_view(state, provider)["outstanding_receivable"] == outstanding
        assert report(state)["summary"]["actual_liquidity"] == "1000.00"
        facts = deepcopy(state["engine"].get("facts", {}))
        repeated = await refresh(db, "owner", clock=T2)
        assert provider_view(repeated, provider)["outstanding_receivable"] == outstanding
        assert repeated["engine"].get("facts", {}) == facts
        assert repeated["openings"] == initial and repeated["movements"] == []
        assert await db.order_payment_transactions_v2.count_documents({}) == 0
    run(scenario)


def test_unpaid_then_partial_then_fully_paid_credits_the_delta_and_settlement_only_once():
    async def scenario(db):
        initial = await start(db, "salla")
        doc = order_document("mada", "0.00", "100.00")
        raw = doc["raw_by_source"]["salla_direct"]
        await db.unified_orders.insert_one(doc)
        for updated_at, observed, paid, remaining, balance in [
            (CREATED, T1, "0.00", "100.00", "500.00"),
            (T2, T2, "45.00", "55.00", "545.00"),
            (T3, T3, "100.00", None, "600.00"),
        ]:
            raw["updated_at"] = updated_at
            raw["payment_actions"]["remaining_action"] = remaining_action(paid, remaining)
            await db.unified_orders.update_one({"user_id": "owner", "order_number": doc["order_number"]},
                                                {"$set": {"raw_by_source.salla_direct": raw}})
            state = await refresh(db, "owner", clock=observed)
            assert provider_view(state, "salla")["outstanding_receivable"] == balance
            assert state["movements"] == [] and state["openings"] == initial
            assert report(state)["summary"]["actual_liquidity"] == "1000.00"

        payout = movement()
        payout.update(request_id="paid-out-once", party_type="provider", party_id="salla", direction="incoming",
                      kind="settlement", amount="97.00", actual_fee_amount="3.00", reference="synthetic-statement",
                      allocations=[{"obligation_id": "provider:" + raw["id"], "amount": "100.00"}])
        first = await create_movement(db, "owner", "owner", payout, clock=T4)
        assert (await create_movement(db, "owner", "owner", payout, clock=T4))["id"] == first["id"]
        state = await refresh(db, "owner", clock=T5)
        assert provider_view(state, "salla")["outstanding_receivable"] == "500.00"
        assert len(state["movements"]) == 1 and state["openings"] == initial
        assert report(state)["summary"]["actual_liquidity"] == "1097.00"
    run(scenario)


def test_prestart_purchase_and_unseen_historical_amount_do_not_recreate_opening_money():
    async def scenario(db):
        initial = await start(db, "salla")
        await refresh(db, "owner", clock=T2)
        docs = [order_document("mada", "100.00", None, created=created, number=id_)
                for created, id_ in (("2026-10-04T12:00:00+00:00", "8102"), (CREATED, "8103"))]
        await db.unified_orders.insert_many(docs)
        state = await refresh(db, "owner", clock=T3)
        assert provider_view(state, "salla")["outstanding_receivable"] == "500.00"
        assert state["movements"] == [] and state["openings"] == initial
        assert any(i["code"] == "provider_historical_capture_review_required" for i in state["engine"]["issues"])
    run(scenario)


def test_positive_display_label_with_no_native_money_never_becomes_provider_or_bank_credit():
    async def scenario(db):
        initial = await start(db, "tamara")
        doc = order_document("tamara_installment", "0.00", None)
        raw = doc["raw_by_source"]["salla_direct"]
        del raw["payment_actions"]
        raw["payment"] = {"method": "tamara_installment", "status": "paid"}
        await db.unified_orders.insert_one(doc)
        state = await refresh(db, "owner", clock=T1)
        assert provider_view(state, "tamara")["outstanding_receivable"] == "500.00"
        assert report(state)["summary"]["actual_liquidity"] == "1000.00"
        assert state["movements"] == [] and state["openings"] == initial
    run(scenario)
