"""One Salla order is paid by two tenders; only each provider's actual share is owed."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_engine import reconcile
from operational_provider_report_coverage import provider_customer_payments
from operational_provider_transaction_evidence import normalize_operational_payment
from test_operational_balance_order_page_payment_flow import CREATED, T1, T2, T3, order_document
from test_operational_balance_engine import START, policy


def source(first="tabby_installment", second="mada"):
    doc = order_document(first, "100", "0")
    raw = doc["raw_by_source"]["salla_direct"]
    raw["payment_methods"] = [
        {"amount": "65", "payment_method": first, "transaction_reference": "ref-A"},
        {"amount": "35", "payment_method": second, "transaction_reference": "ref-B"},
    ]
    return doc, raw


def normalize(doc, rows=()):
    raw = doc["raw_by_source"]["salla_direct"]
    return normalize_operational_payment(raw, "SAR", Decimal(100), CREATED, T2, owner="owner",
        order_number=doc["order_number"], provider_order_id=str(raw["id"]), rows=rows, source=doc)


def normalized(doc):
    payment, errors = normalize(doc)
    assert not errors
    return {"id": doc["salla_order_id"], "order_number": doc["order_number"],
            "created_at": CREATED, "updated_at": CREATED, "currency": "SAR", "payment": payment}


def state_after_previous_tick():
    state = reconcile({"owner_id":"owner", "status":"active", "started_at":START, "movements":[]}, {}, T1)
    state["engine"]["as_of"] = T1
    return state


def amounts(state):
    return {r["party_id"]:r["confirmed"] for r in state["engine"]["obligations"].values() if r["kind"] == "provider"}


def displayed(state):
    e = state["engine"]
    breakdown, _ = provider_customer_payments({"details":{"facts":e["facts"]}, "issues":e["issues"],
        "obligations":list(e["obligations"].values())})
    rows = breakdown["SAR"]
    return {r["name"]: (r["orders"], r["paid"], r["status"]) for r in rows}


def test_mixed_salla_and_bnpl_preserve_each_paid_share_on_replay_and_in_report():
    doc, raw = source()
    state = state_after_previous_tick()
    order = normalized(doc)
    scopes = {"orders":[order], "fee_policies":[policy(),policy(id="s",provider="salla")]}
    first = reconcile(state, scopes, T2)
    assert amounts(first) == {"tabby":"65.00", "salla":"35.00"}
    assert displayed(first) == {"تابي":(1,"65.00","إثبات محفوظ"), "سلة":(1,"35.00","إثبات محفوظ")}
    assert first["engine"]["provider_reports"]["provider:" + order["id"] + ":tabby"]["gross"] == "65.00"
    raw["payment_methods"].reverse()
    repeated = reconcile(first, dict(scopes, orders=[normalized(doc)]), T3)
    assert repeated["engine"]["facts"] == first["engine"]["facts"]
    assert repeated["engine"]["obligations"] == first["engine"]["obligations"]
    assert not repeated["movements"]


def test_wallet_is_evidence_but_never_booked_as_provider_or_cash():
    doc, _ = source("tamara_installment","customer_wallet")
    out = reconcile(state_after_previous_tick(), {"orders":[normalized(doc)]}, T2)
    assert amounts(out) == {"tamara":"65.00"}
    assert displayed(out) == {"تمارا":(1,"65.00","إثبات محفوظ")}
    assert out["movements"] == []


def test_paid_opening_protects_only_affected_tender_and_exposes_gross_for_both():
    doc, _ = source()
    initial = state_after_previous_tick()
    initial["openings"] = {"open":{"id":"open","party_type":"provider","party_id":"salla","currency":"SAR",
                                   "direction":"for_us","amount":"200.00"}}
    initial["movements"] = [{"id":"m", "party_type":"provider","party_id":"salla","currency":"SAR", "amount":"50",
                             "direction":"incoming","kind":"settlement","allocations":[{"obligation_id":"open","amount":"50"}]}]
    out = reconcile(initial, {"orders":[normalized(doc)]}, T2)
    assert amounts(out) == {"tabby":"65.00"}
    summary = displayed(out)
    assert summary["تابي"] == (1,"65.00","إثبات محفوظ")
    assert summary["سلة"][:2] == (1,"35.00") and "للمراجعة" in summary["سلة"][2]
    assert out["movements"] == initial["movements"]


@pytest.mark.parametrize("change", ["shortfall","reference","currency","refund","negative","unknown","owner",
                                    "stale","pending","not_complete","bad_history","unbound_conflict","duplicate"])
def test_mixed_payment_requires_correlated_complete_evidence(change):
    doc, raw = source()
    r = raw["payment_methods"]; rows = []
    if change == "shortfall": r[0]["amount"] = "64"
    elif change == "reference": r[1].pop("transaction_reference")
    elif change == "currency": r[1]["currency"] = "USD"
    elif change == "refund": raw["refunded_amount"] = "5"
    elif change == "negative": r[1]["amount"] = "-1"
    elif change == "unknown": r[1]["payment_method"] = "unverified"
    elif change == "owner": doc["user_id"] = "other"
    elif change == "stale": doc["g47_salla_snapshot"] = {"source_updated_at":T2}
    elif change == "pending": raw["is_pending_payment"] = True
    elif change == "not_complete": raw.pop("payment_actions")
    elif change == "duplicate": r[1]["transaction_reference"] = r[0]["transaction_reference"]
    elif change in ("bad_history", "unbound_conflict"):
        rows.append({"user_id":"owner", "source":"salla_transactions", "order_number":doc["order_number"],
                     "provider_order_id":doc["salla_order_id"],"transaction_id":"t1","native_transaction_evidence":[]})
        if change == "unbound_conflict":
            rows[0]["native_transaction_evidence"] = [{"schema":"salla_transaction_evidence_v1","transaction_id":"t1",
                "status":"paid","currency":"SAR","unbound_order":True,"amount":"66.00","reference":"ref-A","provider":"tabby"}]
    payment, errors = normalize(doc, rows)
    assert payment is None and errors


def test_split_does_not_double_book_when_existing_single_claim_or_changed_tender():
    doc, raw = source()
    order = normalized(doc)
    s = state_after_previous_tick()
    single = deepcopy(order)
    single["payment"] = {"provider":"tabby", "gross":"100","captures":[{"id":"frozen","amount":"100","captured_at":CREATED}]}
    original = reconcile(s, {"orders":[single], "fee_policies":[policy()]}, T2)
    assert amounts(original) == {"tabby":"100.00"}
    out = reconcile(original, {"orders":[order]}, T3)
    assert amounts(out) == amounts(original)
    assert any(x["code"] == "provider_capture_identity_conflict" for x in out["engine"]["issues"])
    split = reconcile(s, {"orders":[order]}, T2)
    raw["payment_methods"][0]["amount"],raw["payment_methods"][1]["amount"] = "55","45"
    changed = reconcile(split, {"orders":[normalized(doc)]}, T3)
    assert amounts(changed) == {"tabby":"65.00", "salla":"35.00"}
    assert any(x["code"] == "provider_order_page_identity_conflict" for x in changed["engine"]["issues"])
    after_single = reconcile(split, {"orders":[single]}, T3)
    assert amounts(after_single) == amounts(split)


def test_existing_single_provider_refund_on_changed_split_never_stops_other_orders():
    from operational_customer_returns import attach_returns
    doc, _ = source()
    order = normalized(doc)
    state = state_after_previous_tick()
    state["customer_returns"] = [{"order_id":order["id"],"status":"refunded","refund_source_type":"provider",
                                 "shipping":{"kind":"none"}}]
    other_doc, raw = source("tamara_installment","customer_wallet")
    other_doc.update(order_number="8205", salla_order_id="518205")
    raw.update(id="518205", reference_id="8205")
    inp = attach_returns(state, {"orders":[order,normalized(other_doc)]})
    out = reconcile(state, inp, T2)
    assert amounts(out) == {"tamara":"65.00"}
    assert any(x["code"] == "provider_refund_execution_evidence_required" for x in out["engine"]["issues"])
    assert out["customer_returns"] == state["customer_returns"]


def test_actual_provider_payout_allocates_to_one_split_share_only_and_replay_is_idempotent():
    from operational_balance_service import create_movement, refresh, report
    from test_operational_balance_integration import movement, run
    from test_operational_balance_order_page_payment_flow import start

    async def scenario(db):
        await start(db, "tabby")
        await refresh(db, "owner", clock=T1)
        doc, _ = source()
        await db.unified_orders.insert_one(doc)
        first = await refresh(db, "owner", clock=T2)
        assert amounts(first) == {"tabby":"65.00", "salla":"35.00"}
        key = "provider:" + doc["salla_order_id"] + ":tabby"
        payout = movement()
        payout.update(request_id="split-statement", party_type="provider", party_id="tabby", direction="incoming",
                          kind="settlement", amount="62.00", actual_fee_amount="3.00", reference="example statement",
                          allocations=[{"obligation_id":key, "amount":"65.00"}])
        mov = await create_movement(db,"owner","owner",payout,clock=T3)
        assert (await create_movement(db,"owner","owner",payout,clock=T3))["id"] == mov["id"]
        final = await refresh(db,"owner",clock=T3)
        parties = {p["party_id"]:p for p in report(final)["parties"] if p["party_type"]=="provider"}
        assert parties["tabby"]["outstanding_receivable"] == "500.00"  # only the opening remains
        assert parties["salla"]["outstanding_receivable"] == "35.00"
        assert report(final)["summary"]["actual_liquidity"] == "1062.00"
        assert len(final["movements"]) == 1
        assert displayed(first) == displayed(final)
    run(scenario)


@pytest.mark.parametrize("prior", ["capture", "split", "page"])
def test_split_reference_cannot_rebook_prior_order_omitted_from_current_source_batch(prior):
    doc, _ = source()
    order = normalized(doc)
    initial = state_after_previous_tick()
    if prior == "capture":
        initial["engine"]["facts"]["capture:ref-A"] = {"order_id":"previous","provider":"tabby","amount":"65.00"}
    elif prior == "page":
        initial["engine"]["facts"]["provider_order_balance:previous"] = {
            "order_id":"previous","provider":"tabby","paid":"65.00","transaction_reference":"ref-A"}
    else:
        other = deepcopy(order)
        other.update(id="previous", order_number="another")
        other["payment"]["split"].update(order_id="previous", order_number="another")
        initial = reconcile(initial, {"orders":[other]}, T2)
        assert amounts(initial) == {"tabby":"65.00","salla":"35.00"}
    out = reconcile(initial, {"orders":[order]}, T3)
    assert amounts(out) == amounts(initial)
    assert out["engine"]["facts"] == initial["engine"]["facts"]
    assert any(i["code"] == "provider_payment_reference_conflict" for i in out["engine"]["issues"])


def test_frozen_split_order_does_not_turn_into_a_second_bank_receipt_or_cod_collection():
    from operational_balance_service import refresh
    from test_operational_balance_bank_orders import seed
    from test_operational_balance_integration import run
    from test_operational_balance_order_page_payment_flow import start

    async def scenario(db):
        await start(db, "tabby")
        await seed(db)
        # Retain the bank binding for the later switch, not its fixture order.
        await db.unified_orders.delete_one({"order_number":"new-bank-order"})
        doc, raw = source()
        await db.unified_orders.insert_one(doc)
        first = await refresh(db, "owner", clock=T1)
        assert amounts(first) == {"tabby":"65.00","salla":"35.00"}
        raw.pop("payment_methods")
        raw.update(status="reviewed",updated_at=T2,payment_method="تحويل بنكي مصرف تجريبي")
        await db.unified_orders.update_one({"order_number":doc["order_number"]},{"$set":{"raw_by_source.salla_direct":raw}})
        bank = await refresh(db, "owner", clock=T2)
        assert bank["movements"] == []
        assert any(i["code"] == "provider_payment_method_conflict" for i in bank["engine"]["issues"])
        raw.update(status="under_review", updated_at=T3, payment_method="cod")
        raw["payment_actions"]["remaining_action"] = {"paid_amount":"0","remaining_amount":"100","has_remaining_amount":True}
        await db.unified_orders.update_one({"order_number":doc["order_number"]},{"$set":{"raw_by_source.salla_direct":raw}})
        cod = await refresh(db, "owner", clock=T3)
        assert not any(r["kind"]=="cod" and Decimal(r["confirmed"]) for r in cod["engine"]["obligations"].values())
        assert amounts(cod) == amounts(first)
        assert any(i["code"] == "provider_payment_method_conflict" for i in cod["engine"]["issues"])
    run(scenario)


def test_partially_refunded_or_pending_refund_split_never_increases_confirmed_receivable():
    doc, raw = source()
    raw["payment_actions"]["refund_action"] = {"pending_refund_amount":{"amount":"1","currency":"SAR"}}
    payment, errors = normalize(doc)
    assert payment is None and errors[0]["code"] == "provider_refund_execution_evidence_required"


def test_conflicting_per_order_transaction_record_and_tender_totals():
    doc, raw = source()
    base = {"schema":"salla_transaction_evidence_v1", "provider_order_id":doc["salla_order_id"],
            "transaction_id":"same-native-id","status":"captured","currency":"SAR","paid_at":None}
    ev_a = dict(base, provider="tabby", reference="ref-A", amount="65.00")
    ev_b = dict(base, provider="salla", reference="ref-B", amount="35.00")
    row = {"user_id":"owner","source":"salla_transactions","order_number":doc["order_number"],
           "provider_order_id":doc["salla_order_id"], "transaction_id":"same-native-id",
           "native_transaction_evidence":[ev_a]}
    assert normalize(doc,[row])[1] == []
    row["native_transaction_evidence"].append(ev_b)
    assert normalize(doc,[row])[1] == [{"code":"provider_transaction_binding_conflict"}]


@pytest.mark.parametrize("linked", [False, True])
def test_first_seen_fresh_split_with_existing_payment_never_books_a_second_tender(linked):
    doc, _ = source()
    order = normalized(doc)
    initial = state_after_previous_tick()
    initial["engine"]["as_of"] = START  # the previous run predates this order
    movement = {"id":"already-received", "party_type":"provider", "party_id":"tabby", "currency":"SAR",
                "direction":"incoming", "amount":"65", "kind":"settlement", "allocations":[], "occurred_at":T1}
    if linked:
        movement["order_number"] = doc["order_number"]
    initial["movements"] = [movement]
    out = reconcile(initial, {"orders":[order]}, T2)
    assert amounts(out) == {"salla":"35.00"}
    assert displayed(out)["تابي"][:2] == (1, "65.00")
    assert "للمراجعة" in displayed(out)["تابي"][2]
    assert out["movements"] == [movement]


def test_unrelated_prior_opening_settlement_does_not_block_a_future_split():
    doc, _ = source()
    initial = state_after_previous_tick()
    initial["engine"]["as_of"] = START
    initial["openings"] = {"open":{"id":"open", "party_type":"provider", "party_id":"tabby", "currency":"SAR",
                                  "amount":"200.00","direction":"for_us"}}
    initial["movements"] = [{"id":"previous-payout","party_type":"provider","party_id":"tabby","currency":"SAR",
                             "direction":"incoming","kind":"settlement","amount":"65","occurred_at":START,
                             "allocations":[{"obligation_id":"open","amount":"65"}]}]
    out = reconcile(initial, {"orders":[normalized(doc)]}, T2)
    assert amounts(out) == {"tabby":"65.00","salla":"35.00"}
