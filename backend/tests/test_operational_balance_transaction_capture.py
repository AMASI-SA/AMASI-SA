"""The native transaction endpoint is independent of customer-order timestamps."""
from copy import deepcopy

import pytest

from order_activity_v1 import normalize_transaction_row

OWNER, NUMBER, ORDER = "fixture-merchant", "98001", "1001"
CREATED = "2026-10-05T10:00:00+00:00"
PAID = "2026-10-05T11:00:00+00:00"
NOW = "2026-10-06T08:00:00+00:00"


def native_txn(method="mada", **changes):
    # Salla /transactions shape: labels for display; slugs for identity.
    return {"id": 42, "references": {"order_id": 1001, "transaction": "capture-A"},
            "total": {"amount": "57.19", "currency": "SAR"},
            "status": {"name": "مؤكدة", "slug": "paid"},
            "payment_method": {"name": "وسيلة الدفع", "slug": method},
            "created_at": {"date": "2026-10-05 13:10:00", "timezone": "Asia/Riyadh"},
            "paid_at": PAID, **changes}


def source_order(method):
    return {"id": ORDER, "reference_id": NUMBER, "payment_method": method,
            "status": "under_review", "payment_methods": [
                {"payment_method": method, "transaction_reference": "capture-A", "amount": "57.19"}],
            "payment_actions": {"remaining_action": {"paid_amount": {"amount": "57.19", "currency": "SAR"}}}}


def stored(native=None, **changes):
    x = normalize_transaction_row(native or native_txn(), order_number=NUMBER, provider_order_id=ORDER)
    x["user_id"] = OWNER
    # The writer accumulates distinct observations on the immutable display row.
    if "native_transaction_evidence" in x:
        x["native_transaction_evidence"] = [x["native_transaction_evidence"]]
    x.update(changes)
    return x


def recognize(raw, rows):
    from operational_provider_transaction_evidence import normalize_transaction_payment
    before = deepcopy((raw, rows))
    result = normalize_transaction_payment(raw, "SAR", "57.19", CREATED, NOW, owner=OWNER,
                                           order_number=NUMBER, provider_order_id=ORDER, rows=rows)
    assert before == (raw, rows)
    return result


def test_native_writer_preserves_provenance_not_customer_or_card_information():
    doc = stored(native_txn(customer={"name": "must-not-appear"}, card={"number": "private"}))
    assert doc.get("native_transaction_evidence"), "activity writer drops authoritative native status, currency and timestamp provenance"
    ev = doc["native_transaction_evidence"][0]
    assert (ev["provider_order_id"], ev["reference"], ev["amount"], ev["currency"], ev["status"], ev["paid_at"]) == (
        ORDER, "capture-A", "57.19", "SAR", "paid", PAID)
    assert "customer" not in ev and "card" not in ev


@pytest.mark.parametrize(("method","provider"), [("mada","salla"),("tamara_installment","tamara"),("tabby_installment","tabby")])
def test_normalized_authenticated_capture_fills_raw_order_gap_once(method, provider):
    doc = stored(native_txn(method))
    payment, issues = recognize(source_order(method), [doc, deepcopy(doc)])
    assert not issues
    assert payment["provider"] == provider
    assert payment["captures"] == [{"id":"capture-A", "amount":"57.19", "captured_at":PAID}]


@pytest.mark.parametrize("changes", [{"paid_at":None}, {"paid_at":"2026-10-05"},
    {"paid_at":"2026-10-05T11:00:00"}, {"paid_at":"2028-01-01T11:00:00Z"},
    {"paid_at":"2026-09-01T11:00:00Z"}, {"paid_at":None,"updated_at":PAID},
    {"status":{"slug":"authorized"}},{"status":{"slug":"pending"}},
    {"status":{"name":"paid"}}, {"status":{"slug":"refunded"}},{"status":{"slug":"voided"}},
    {"references":{"order_id":"other","transaction":"capture-A"}}, {"references":{"transaction":"capture-A"}},
    {"references":{"order_id":ORDER}}, {"references":{"order_id":ORDER,"transaction":"other"}},
    {"total":{"amount":"57.19"}}, {"total":{"amount":"57.19","currency":"KWD"}},
    {"total":{"amount":"57.194","currency":"SAR"}}, {"total":{"amount":"56","currency":"SAR"}},
    {"total":{"amount":0,"currency":"SAR"}}, {"payment_method":{"slug":"tabby"}},
    {"type":"refund"}, {"operation":"void"}])
def test_ambiguous_or_nonfinal_transaction_never_becomes_provider_money(changes):
    p, issues = recognize(source_order("mada"), [stored(native_txn(**changes))])
    assert not (p or {}).get("captures") and issues


@pytest.mark.parametrize("changes", [{"user_id":"foreign"}, {"order_number":"another"},
                                    {"provider_order_id":"other"}, {"source":"manual"}])
def test_no_cross_tenant_order_or_untrusted_source(changes):
    p,issues=recognize(source_order("mada"),[stored(**changes)])
    assert not (p or {}).get("captures") and issues


def test_legacy_and_current_paid_state_do_not_turn_order_time_into_paid_at():
    doc=stored(native_txn(paid_at=None))
    assert doc["native_transaction_evidence"][0]["paid_at"] is None
    p, issues=recognize(source_order("mada"),[doc])
    assert not (p or {}).get("captures") and any("timestamp" in str(i) for i in issues)
    doc.pop("native_transaction_evidence")
    p, issues=recognize(source_order("mada"),[doc])
    assert not (p or {}).get("captures") and issues


def test_conflicting_repeat_and_second_transaction_fail_closed():
    for changed in (native_txn(total={"amount":"56","currency":"SAR"}),
                    native_txn(status={"slug":"refunded"}), native_txn(id=1234)):
        p, issues=recognize(source_order("mada"),[stored(),stored(changed)])
        assert not (p or {}).get("captures") and issues


def test_original_capture_contract_remains_valid_and_duplicate_is_not_summed():
    raw=source_order("mada")
    raw["payment"]={"method":"mada","status":"captured","paid_at":PAID,"reference":"capture-A"}
    for rows in ([],[stored()]):
        p,issues=recognize(raw,rows)
        assert p["captures"]==[{"id":"capture-A","amount":"57.19","captured_at":PAID}] and not issues


def test_refunds_split_and_cancellations_remain_review_only_even_with_signed_capture():
    raw=source_order("mada")
    split=deepcopy(raw["payment_methods"])+[{"payment_method":"tabby","amount":"9","transaction_reference":"b"}]
    for changes in ({"status":"cancelled"},{"refunded_amount":"1"},{"payment_methods":split},{"is_pending_payment":True}):
        p, issues=recognize({**raw,**changes},[stored()])
        assert not (p or {}).get("captures") and issues


def test_native_positive_capture_with_no_fee_schedule_is_gross_only_and_replay_safe():
    from operational_balance_engine import reconcile
    from operational_balance_service import report
    from operational_financial_position_core import _provider_tables
    payment, _ = recognize(source_order("mada"),[stored()])
    sources = {"orders":[{"id":ORDER, "order_number":NUMBER, "currency":"SAR","created_at":CREATED,
                          "updated_at":PAID, "payment":payment}], "fee_policies":[]}
    state = {"status":"active", "started_at":"2026-10-05T00:00:00+00:00","movements":[],"openings":{}}
    new = reconcile(state, sources, NOW)
    key = "provider:"+ORDER
    assert new["engine"]["obligations"][key]["confirmed"]=="57.19"
    assert new["engine"]["obligations"][key]["fee_estimate_complete"] is False
    assert new["movements"]==[] and new["openings"]=={}
    assert reconcile(new,sources,NOW)["engine"]["facts"]==new["engine"]["facts"]
    view=report(new)
    fees, _ = _provider_tables(view["obligations"],view["details"],view["issues"])
    assert fees["totals"]["amount"] is None and fees["rows"][0]["amount"] is None


def test_late_dated_capture_after_start_books_once_without_fee_schedule():
    from operational_balance_engine import reconcile
    payment, _ = recognize(source_order("mada"),[stored()])
    sources={"orders":[{"id": ORDER,"order_number":NUMBER,"currency":"SAR","created_at":CREATED,
        "updated_at":PAID,"payment":payment}],"fee_policies":[]}
    state={"owner_id": OWNER, "status":"active","started_at":"2026-10-05T00:00:00+00:00","engine":{"as_of":NOW},
           "movements":[],"openings":{}}
    out=reconcile(state,sources,"2026-10-07T00:00:00+00:00")
    assert out["engine"]["facts"]["capture:capture-A"]["amount"] == "57.19"
    assert out["engine"]["obligations"]["provider:" + ORDER]["confirmed"] == "57.19"
    assert reconcile(out, sources, "2026-10-07T00:00:00+00:00") == out
    assert out["movements"]==[] and out["openings"]=={}


def test_bad_ambiguous_plan_or_postsettlement_refund_cannot_become_gross_only():
    from operational_balance_engine import reconcile
    payment,_=recognize(source_order("mada"),[stored()])
    base={"id":"p","status":"active","provider":"salla","currency":"SAR","effective_from":"2020-01-01"}
    start={"status":"active","started_at":"2026-10-05T00:00:00+00:00","movements":[],"openings":{}}
    for p,policies in [(payment,[base,{**base,"id":"p2"}]),({**payment,"refunds":[{"id":"r","status":"executed","amount":"1"}]},[])]:
        out=reconcile(start,{"orders":[{"id":ORDER,"order_number":NUMBER,"currency":"SAR","created_at":CREATED,
                     "updated_at":PAID,"payment":p}],"fee_policies":policies},NOW)
        assert "capture:capture-A" not in out["engine"]["facts"] and "provider:"+ORDER not in out["engine"]["obligations"]


def test_scoped_native_transaction_source_to_gross_claim_does_not_touch_order_or_bank():
    from operational_balance_sources import collect_sources
    from operational_balance_engine import reconcile
    from test_operational_balance_sources import HardBoundaryDB, row, order, run
    canonical = order(**{**source_order("mada"), "created_at":CREATED, "total":{"amount":"57.19","currency":"SAR"}})
    canonical["order_number"] = NUMBER
    ledger = stored(user_id="owner")
    db=HardBoundaryDB({"unified_orders":[canonical],"order_payment_transactions_v2":[
        ledger, {**ledger,"user_id":"other"}, {**ledger,"order_number":"998877"}],
        "mezan_product_cost_profiles_v2":[row(salla_product_id="p1",base_cost="20")]})
    before=deepcopy(db.data)
    cut="2026-10-05T00:00:00+00:00"
    inputs=run(collect_sources(db,"owner",cut,NOW))
    assert inputs["orders"][0]["payment"]["captures"]==[{"id":"capture-A","amount":"57.19","captured_at":PAID}]
    assert db.reads.count("order_payment_transactions_v2")==1
    initial={"status":"active","started_at":cut,"movements":[],"openings":{}}
    out=reconcile(initial,inputs,NOW)
    assert out["engine"]["obligations"]["provider:"+ORDER]["confirmed"]=="57.19"
    assert db.data==before and out["movements"]==[] and out["openings"]=={} and not initial.get("engine")


def test_sparse_then_complete_same_transaction_can_gain_real_paid_timestamp():
    previous=stored(native_txn(paid_at=None))
    p,issues=recognize(source_order("mada"),[previous,stored()])
    assert not issues and p["captures"][0]["captured_at"]==PAID


def test_cross_order_reference_collision_quarantines_both_read_paths():
    from operational_provider_transaction_evidence import load_transaction_rows
    from operational_payment_evidence import _instant
    from test_operational_balance_sources import HardBoundaryDB, run
    original=source_order("mada")
    another={**original,"reference_id":"98002","id":"1002"}
    first=stored(user_id="owner")
    second=deepcopy(first)
    second.update(order_number="98002",provider_order_id="1002")
    second["native_transaction_evidence"][0]["provider_order_id"]="1002"
    db=HardBoundaryDB({"order_payment_transactions_v2":[first,second]})
    rows=run(load_transaction_rows(db,"owner",[dict(x,created_at=CREATED) for x in (original,another)],
        _instant("2026-10-05T00:00:00Z"),_instant(NOW),to_instant=_instant,max_rows=50))
    from operational_provider_transaction_evidence import normalize_transaction_payment
    for raw in (original, another):
        p, issues=normalize_transaction_payment(raw,"SAR","57.19",CREATED,NOW,owner="owner",
            order_number=raw["reference_id"],provider_order_id=raw["id"],rows=rows[raw["reference_id"]])
        assert not (p or {}).get("captures") and issues


def test_manual_authenticated_refresh_persists_immutable_evidence_and_ignores_foreign_order(monkeypatch):
    import asyncio
    from mongomock_motor import AsyncMongoMockClient
    import order_activity_v1 as activity

    async def exercise():
        db = AsyncMongoMockClient().isolated
        await db.unified_orders.insert_one({"user_id":OWNER,"order_number":NUMBER,"salla_order_id":ORDER})
        variants=[native_txn(),native_txn(references={"order_id":"wrong","transaction":"another"})]
        async def salla(_db, uid, path, *, base_params=None, **kw):
            assert uid == OWNER
            if path == "/transactions":
                assert base_params=={"order_id":ORDER}
                return variants
            return []
        monkeypatch.setattr(activity,"_fetch_salla_pages",salla)
        # Simulate an old row: normalized display fields existed, attestation didn't.
        old=stored();old.pop("native_transaction_evidence")
        await db.order_payment_transactions_v2.insert_one(old)
        for _ in range(2):
            await activity.refresh_order_activity_from_salla(db,user_id=OWNER,order_number=NUMBER)
        rows=await db.order_payment_transactions_v2.find({"user_id":OWNER}).to_list(20)
        assert len(rows)==1 and len(rows[0]["native_transaction_evidence"])==1
        assert recognize(source_order("mada"),rows)[0]["captures"][0]["amount"]=="57.19"
        # Same display identity but raw native operation is now marked as a void.
        variants[:]=[native_txn(operation="void")]
        await activity.refresh_order_activity_from_salla(db,user_id=OWNER,order_number=NUMBER)
        rows=await db.order_payment_transactions_v2.find({"user_id":OWNER}).to_list(20)
        assert len(rows)==1 and len(rows[0]["native_transaction_evidence"])==2
        p,issues=recognize(source_order("mada"),rows)
        assert not (p or {}).get("captures") and issues
        assert await db.unified_orders.count_documents({})==1
        assert set(await db.list_collection_names())=={"unified_orders","order_payment_transactions_v2"}
    asyncio.run(exercise())


def test_cash_normalizer_does_not_synthesize_unpriced_forecast_or_settlement():
    from operational_balance_engine import reconcile
    from operational_balance_service import report
    payment,_=recognize(source_order("mada"),[stored()])
    inp={"orders":[{"id":ORDER,"order_number":NUMBER,"currency":"SAR","created_at":CREATED,
                    "updated_at":PAID,"payment":payment}],"fee_policies":[]}
    start={"status":"active","started_at":"2026-10-05T00:00:00Z","movements":[],"openings":{}}
    new=reconcile(start,inp,NOW)
    p=next(z for z in report(new)["parties"] if z["party_type"]=="provider")
    assert p["outstanding_receivable"]=="57.19" and p["settled"]=="0.00"
    assert report(new)["summary"]["actual_liquidity"]=="0.00"
    assert not report(new)["details"]["provider_reports"]
    # Replay after a separately recorded store settlement must preserve the
    # original claim/allocation and bank receipt; never create a second one.
    new["movements"]=[{"id":"settle1","amount":"55.19","currency":"SAR","actual_fee_amount":"2",
        "bank_id":"bank1","bank_kind":"bank","bank_name":"Test Bank","party_type":"provider",
        "party_id":"salla","direction":"incoming","kind":"collection","allocations":[
            {"obligation_id":"provider:"+ORDER,"amount":"57.19"}]}]
    again=reconcile(new,inp,NOW); summary=report(again)
    p=next(z for z in summary["parties"] if z["party_type"]=="provider")
    assert p["outstanding_receivable"]=="0.00" and p["settled"]=="57.19"
    assert summary["summary"]["actual_liquidity"]=="55.19" and again["movements"]==new["movements"]


def test_legacy_incomplete_optional_observation_does_not_veto_independently_complete_canonical_payment():
    raw=source_order("mada")
    raw["payment"]={"method":"mada","status":"paid","paid_at":PAID,"reference":"capture-A"}
    doc = stored(native_txn(references={}))
    p,issues=recognize(raw,[doc])
    assert not issues and p["captures"]==[{"id":"capture-A","amount":"57.19","captured_at":PAID}]


@pytest.mark.parametrize("change", [
    {"status":{"slug":"refunded"}}, {"operation":"refund"}, {"references":"malformed"},
    {"references":{"order_id":"other","transaction":"capture-A"}},
    {"total":{"amount":"99","currency":"SAR"}}, {"total":{"amount":"57.19","currency":"QAR"}},
    {"references":{"transaction":"another"}}, {"paid_at":NOW},
])
def test_inconsistent_optional_observation_cannot_release_a_canonical_capture(change):
    raw=source_order("mada")
    raw["payment"]={"method":"mada","status":"paid","paid_at":PAID,"reference":"capture-A"}
    doc=stored(native_txn(**{"references":{},**change}))
    p,issues=recognize(raw,[doc])
    assert issues and not (p or {}).get("captures")


def test_recovered_canonical_capture_posts_after_a_refresh_but_not_over_a_pooled_settlement():
    from operational_balance_engine import reconcile
    from test_operational_balance_engine import policy
    raw=source_order("mada")
    raw["payment"]={"method":"mada","status":"paid","paid_at":PAID,"reference":"capture-A"}
    payment,issues=recognize(raw,[stored(native_txn(references={}))])
    assert not issues and payment["captures"] and payment["capture_source"]=="canonical_order_with_unbound_activity"
    sources={"orders":[{"id":ORDER,"order_number":NUMBER,"currency":"SAR","created_at":CREATED,
                        "updated_at":PAID,"payment":payment}],"fee_policies":[policy(provider="salla")]}
    start={"owner_id": OWNER,"status":"active","started_at":"2026-10-05T00:00:00+00:00","movements":[],"openings":{},
           "engine":{"as_of":NOW}}
    pooled = {**start, "movements":[{"party_type":"provider","party_id":"salla","currency":"SAR","kind":"collection",
                                     "direction":"incoming","amount":"12.00","allocations":[]}]}
    before=deepcopy(pooled)
    out=reconcile(pooled,sources,NOW)
    assert "provider_historical_capture_review_required" in [i["code"] for i in out["engine"]["issues"]]
    assert not out["engine"]["facts"]
    assert out["movements"]==before["movements"] and pooled==before
    first=reconcile(start,sources,NOW)
    assert first["engine"]["obligations"]["provider:"+ORDER]["confirmed"]=="57.19"
    assert reconcile(first,sources,NOW)==first


def test_recovered_partial_capture_does_not_stack_on_a_different_reference_for_same_order():
    from operational_balance_engine import reconcile
    from test_operational_balance_engine import policy
    raw = source_order("mada")
    raw["payment_methods"][0]["amount"] = "20.00"
    raw["payment_actions"]["remaining_action"]["paid_amount"]["amount"] = "20.00"
    raw["payment"] = {"method":"mada","status":"paid","paid_at":PAID,"reference":"capture-A"}
    payment, issues = recognize(raw,[stored(native_txn(total={"amount":"20.00","currency":"SAR"},references={}))])
    assert not issues
    prior = {"capture:other":{"order_id":ORDER,"provider":"salla","amount":"10.00"}}
    state = {"status":"active","started_at":"2026-10-05T00:00:00Z","engine":{"facts":prior}}
    sources = {"orders":[{"id":ORDER,"currency":"SAR","created_at":CREATED,"updated_at":PAID,
                           "payment":payment}],"fee_policies":[policy(provider="salla")]}
    out = reconcile(state,sources,NOW)
    assert out["engine"]["facts"] == prior
    assert "provider_capture_identity_conflict" in [i["code"] for i in out["engine"]["issues"]]


def test_cross_order_canonical_reference_cannot_win_against_a_native_capture_in_either_order():
    from operational_provider_transaction_evidence import load_transaction_rows, normalize_transaction_payment
    from operational_payment_evidence import _instant
    from test_operational_balance_sources import HardBoundaryDB, run
    a={**source_order("mada"),"created_at":CREATED}
    b={**a,"id":"1002","reference_id":"98002","payment":{"method":"mada","status":"paid","paid_at":PAID,"reference":"capture-A"}}
    db=HardBoundaryDB({"order_payment_transactions_v2":[stored(user_id="owner")]})
    for source in ([a,b],[b,a]):
        groups=run(load_transaction_rows(db,"owner",source,_instant("2026-10-05T00:00:00Z"),_instant(NOW),
                                         to_instant=_instant,max_rows=50))
        for raw in source:
            p,issues=normalize_transaction_payment(raw,"SAR","57.19",CREATED,NOW,owner="owner",
                    order_number=raw["reference_id"],provider_order_id=raw["id"],rows=groups.get(raw["reference_id"],[]))
            assert not (p or {}).get("captures") and issues


def test_financial_position_previews_only_known_provider_evidence_without_summing_money_or_orders_twice():
    from operational_financial_position_core import build_financial_position
    from operational_balance_engine import reconcile
    from test_operational_balance_engine import policy
    payment,_=recognize(source_order("mada"),[stored()])
    before={"status":"active","started_at":"2026-10-05T00:00:00Z","movements":[],"openings":{}}
    input_data={"orders":[{"id":ORDER,"currency":"SAR","created_at":CREATED,"updated_at":PAID,
                            "payment":payment}],"fee_policies":[policy(provider="salla")]}
    state=reconcile(before,input_data,NOW)
    state["engine"]["issues"]=[
        {"scope":"provider_payment","code":"provider_capture_evidence_incomplete","source_id":"two",
         "provider":"salla","missing_fields":["capture_timestamp"]},
        {"scope":"provider_payment","code":"provider_capture_evidence_incomplete","source_id":"two",
         "provider":"salla","missing_fields":["capture_timestamp"]},
        {"source_id":"provider:"+ORDER,"code":"provider_fee_estimate_unavailable"},
        {"scope":"provider_payment","code":"provider_payment_method_conflict","source_id":"three"},
    ]
    state["movements"]=[{"id":"partial","amount":"15.00","actual_fee_amount":"1.00","currency":"SAR",
        "bank_id":"synthetic","bank_kind":"bank","party_type":"provider","party_id":"salla","direction":"incoming",
        "kind":"collection","allocations":[{"obligation_id":"provider:"+ORDER,"amount":"16.00"}]}]
    original=deepcopy(state)
    report=build_financial_position(state)
    salla=next(d for d in report["dependencies"] if d["code"]=="provider_evidence_coverage_salla")
    assert salla["observed_order_counts"]=={"capture":1,"settlement_allocation":1,"missing_time":1,"fee_review":1}
    assert salla["status"]=="incomplete" and "لقطة" in salla["message"] and "سلة" in salla["message"]
    unknown=next(d for d in report["dependencies"] if d["code"]=="provider_evidence_coverage_unassigned")
    assert unknown["observed_order_counts"]=={"capture_review":1}
    assert report["currencies"]["SAR"]["metrics"]["liquidity"]=="15.00"
    assert state==original


def test_absence_of_saved_payment_evidence_is_unavailable():
    from operational_financial_position_core import build_financial_position
    report=build_financial_position({"status":"active","started_at":CREATED,"engine":{"as_of":NOW},
                                     "movements":[],"openings":{}})
    coverage=next(d for d in report["dependencies"] if d["code"]=="provider_evidence_coverage")
    assert coverage["status"]=="unavailable" and coverage["observed_order_counts"]=={}


@pytest.mark.parametrize("priced", [True, False])
@pytest.mark.parametrize("gap", ["another_reference", "unallocated", "partially_allocated", "same_order_allocation"])
def test_bound_native_capture_checks_prior_money_before_gross_recognition(priced, gap):
    from operational_balance_engine import reconcile
    from test_operational_balance_engine import policy
    raw = source_order("mada")
    raw["payment_methods"][0]["amount"] = "20.00"
    raw["payment_actions"]["remaining_action"]["paid_amount"]["amount"] = "20.00"
    payment, issues = recognize(raw, [stored(native_txn(total={"amount":"20.00","currency":"SAR"}))])
    assert not issues and payment["capture_source"] == "salla_transaction_evidence_v1"
    state = {"status":"active", "started_at":"2026-10-05T00:00:00Z", "movements":[], "openings":{}}
    if gap == "another_reference":
        state["engine"] = {"facts":{"capture:previous":{"order_id":ORDER, "provider":"salla", "amount":"10.00"}}}
        expected = "provider_capture_identity_conflict"
    else:
        allocations = {"unallocated":[], "partially_allocated":[{"obligation_id":"provider:other","amount":"18"}],
                       "same_order_allocation":[{"obligation_id":"provider:"+ORDER,"amount":"20"}]}[gap]
        state["movements"] = [{"id":"settlement","party_type":"provider","party_id":"salla","currency":"SAR",
            "kind":"settlement","direction":"incoming","amount":"18.00","actual_fee_amount":"2.00","allocations":allocations}]
        expected = "provider_settled_capture_review_required"
    original = deepcopy(state)
    sources = {"orders":[{"id":ORDER,"order_number":NUMBER,"currency":"SAR","created_at":CREATED,
                          "updated_at":PAID,"payment":payment}], "fee_policies":[policy(provider="salla")] if priced else []}
    out = reconcile(state, sources, NOW)
    assert expected in {i["code"] for i in out["engine"]["issues"]}
    assert out["engine"]["facts"] == state.get("engine",{}).get("facts",{})
    assert "provider:"+ORDER not in out["engine"]["obligations"]
    assert state == original and out["movements"] == state["movements"]


def test_canonical_paid_total_cannot_stack_with_a_different_saved_partial_capture():
    from operational_balance_engine import reconcile
    from test_operational_balance_engine import policy
    raw = source_order("mada")
    raw["payment"] = {"method":"mada","status":"paid","paid_at":PAID,"reference":"capture-A"}
    raw["payment_methods"][0]["amount"] = "20.00"
    raw["payment_actions"]["remaining_action"]["paid_amount"]["amount"] = "20.00"
    for observations in ([], [stored(native_txn(total={"amount":"20.00","currency":"SAR"}))]):
        payment, issues = recognize(raw, observations)
        assert not issues and payment["paid_observation"]["amount"] == "20.00"
        prior = {"capture:previous":{"order_id":ORDER,"provider":"salla","amount":"10.00"}}
        state = {"status":"active","started_at":"2026-10-05T00:00:00Z","engine":{"facts":prior},"movements":[]}
        sources = {"orders":[{"id":ORDER,"currency":"SAR","created_at":CREATED,"updated_at":PAID,
                            "payment":payment}],"fee_policies":[policy(provider="salla")]}
        out = reconcile(state,sources,NOW)
        assert out["engine"]["facts"] == prior
        assert "provider_capture_identity_conflict" in [i["code"] for i in out["engine"]["issues"]]


def test_old_immutable_split_facts_cannot_exceed_the_current_authoritative_paid_total_on_replay():
    from operational_balance_engine import reconcile
    from test_operational_balance_engine import policy
    raw = source_order("mada")
    raw["payment_methods"][0]["amount"] = "20.00"
    raw["payment_actions"]["remaining_action"]["paid_amount"]["amount"] = "20.00"
    payment, _ = recognize(raw, [stored(native_txn(total={"amount":"20.00","currency":"SAR"}))])
    prior = {"capture:capture-A":{"order_id":ORDER,"provider":"salla","amount":"20.00"},
             "capture:prior":{"order_id":ORDER,"provider":"salla","amount":"10.00"}}
    state = {"status":"active","started_at":"2026-10-05T00:00:00Z","engine":{"facts":prior},"movements":[]}
    sources = {"orders":[{"id":ORDER,"currency":"SAR","created_at":CREATED,"updated_at":PAID,
                         "payment":payment}],"fee_policies":[policy(provider="salla")]}
    out = reconcile(state, sources, NOW)
    assert out["engine"]["facts"] == prior
    assert "provider:"+ORDER not in out["engine"]["obligations"]
    assert "provider_capture_amount_conflict" in [i["code"] for i in out["engine"]["issues"]]
