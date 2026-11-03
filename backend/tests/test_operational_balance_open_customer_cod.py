from copy import deepcopy
from decimal import Decimal
import pytest

from operational_financial_position_core import build_financial_position
from test_operational_open_cost_exposure import native_order, seeded_db, operating_state, preview, START, NOW

RECENT = "2026-11-01T08:00:00+00:00"
OBSERVED = "2026-11-01T12:00:00+00:00"


def order(number="NEW", *, old=False, paid="23.00", status="in_progress", currency="SAR"):
    doc = native_order(number, status=status) if old else native_order(number, created=RECENT, status=status)
    raw = doc["raw_by_source"]["salla_direct"]
    raw.update(currency=currency, updated_at=OBSERVED, payment_method="cod", amounts={"total":{"currency":currency,"amount":"100"}},
               payment_actions={"remaining_action":{"paid_amount":paid,"remaining_amount":str(100 - Decimal(paid))}})
    doc.update(salla_order_id=raw["id"], g47_salla_snapshot={"source_updated_at":OBSERVED})
    return doc


def projected(doc, mutate=lambda *_: None):
    state = {**operating_state(), "openings": {}, "movements": []}
    db = seeded_db(order_row=doc)
    mutate(db, state)
    before = deepcopy((db.data, state))
    exp = preview(db, state)
    assert before == (db.data, state)
    report = build_financial_position(state, cost_exposure=exp)["currencies"].get("SAR")
    if report is None:
        assert "SAR" not in exp["open_customer_cod"]["by_currency"]
        return []
    table = next(t for t in report["tables"] if t["id"] == "cod_expected")
    assert report["metrics"]["liquidity"] == "0.00" and report["metrics"]["debts_receivable"] == "0.00"
    assert not table["complete"] and table["totals"]["amount"] is None
    return table["rows"]


def test_known_partial_due_grouped_per_period_and_currency_without_driver_or_waybill():
    def add(db, _):
        db.data["unified_orders"].extend([order("OLD", old=True), order("SECOND", paid="0"),
                                          order("OTHER", currency="QAR")])
    rows = projected(order(), add)
    amounts = {r["id"]: (r["orders"],r["amount"]) for r in rows}
    assert amounts == {"current_unassigned": (2,"177.00"), "prestart_unassigned": (1,"77.00")}
    db=seeded_db(order_row=order("OTHER", currency="QAR"))
    assert preview(db)["open_customer_cod"]["by_currency"]["QAR"]["rows"][0]["amount"] == "77.00"


def test_under_review_cod_closed_unpaid_checkout_matches_order_page():
    doc = order(status="under_review", paid="0")
    raw = doc["raw_by_source"]["salla_direct"]
    raw["payment_actions"]["remaining_action"] = {"paid_amount":0,"remaining_amount":None,"has_remaining_amount":False}
    raw.update(remaining_amount=0, paid_amount=0, payment_collection_status="unpaid")
    from order_engine.mapper import map_salla_order
    assert map_salla_order(raw).payment.remaining_amount == 100
    assert projected(doc)[0]["amount"] == "100.00"
    raw["payment_actions"]["remaining_action"]["paid_amount"] = "23"
    assert not projected(doc)


@pytest.mark.parametrize("bad",["no_post_start", "stale_version", "future", "return", "zero_due", "marked_paid",
    "other_method", "split", "refunded", "bad_total", "wrong_currency", "source_status", "shipping_status", "waybill",
    "old_waybill", "driver_delivered", "collected", "earned", "movement", "allocation", "duplicate", "no_payment",
    "watermark_conflict", "booked", "foreign_alias", "ambiguous_assignment", "saved_version", "paid_platform",
    "mixed_method", "stored_custody"])
def test_open_customer_evidence_holds_incomplete_or_closed_cases(bad):
    doc = order(old=bad in {"no_post_start","stale_version"})
    raw=doc["raw_by_source"]["salla_direct"]
    if bad=="no_post_start": doc.pop("g47_salla_snapshot")
    if bad=="stale_version": raw["updated_at"]=doc["g47_salla_snapshot"]["source_updated_at"]=START
    if bad=="future": raw["updated_at"]=doc["g47_salla_snapshot"]["source_updated_at"]="2026-11-03T10:00:00Z"
    if bad=="return": raw["status"]="returned"
    if bad=="zero_due": raw["payment_actions"]["remaining_action"]={"paid_amount":"100","remaining_amount":"0"}
    if bad=="marked_paid": raw["payment_collection_status"]="paid"
    if bad=="other_method": raw["payment_method"]="tabby"
    if bad=="mixed_method": raw["payment_method"]={"code":"cod","slug":"tabby"}
    if bad=="split": raw["payment_methods"]=[{"method":"cod"},{"method":"mada"}]
    if bad=="refunded": raw["refund_amount"]="1"
    if bad=="bad_total": raw["total"]="200"
    if bad=="wrong_currency": raw["payment_actions"]["remaining_action"]["remaining_amount"]={"currency":"QAR","amount":"77"}
    if bad=="source_status": doc["order_status_slug"]="delivered"
    if bad=="shipping_status": doc["salla_shipping_current"]={"status":"delivered"}
    if bad=="waybill": raw["shipping"]={"tracking_number":"W"}
    if bad=="old_waybill": doc["salla_shipping_current"]={"superseded_shipment_ids":["W"]}
    if bad=="driver_delivered": doc["store_delivery_status"]="delivered"
    if bad=="no_payment": raw.pop("payment_actions")
    if bad=="watermark_conflict": doc["g47_salla_snapshot"]["component_pending"]=True
    if bad=="foreign_alias": doc["order_id"]="foreign"
    def conflict(db, state):
        if bad=="collected": db.data["store_delivery_collections"]=[{"user_id":"owner","order_number":"NEW"}]
        if bad=="earned": db.data["store_delivery_driver_earnings"]=[{"user_id":"owner","order_id":"salla-NEW"}]
        if bad=="stored_custody": state["engine"]["cod_reports"]={"cod:salla-NEW":{"confirmed_custody":"60"}}
        if bad=="saved_version": state["engine"]["order_versions"]={"salla-NEW":NOW}
        if bad=="paid_platform": state["engine"]["facts"]={"capture:tabby:captured":{"order_id":"salla-NEW"}}
        if bad=="movement": state["movements"]=[{"order_number":"NEW"}]
        if bad=="allocation": state["movements"]=[{"allocations":[{"obligation_id":"cod:salla-NEW"}]}]
        if bad=="duplicate": db.data["unified_orders"].append(deepcopy(doc))
        if bad=="booked": state["engine"]["facts"]={"delivery_event:salla-NEW":{"party_type":"courier"}}
        if bad=="ambiguous_assignment": db.data["store_delivery_assignments"]=[{"user_id":"owner","order_number":"NEW","order_id":"other"}]
    # Some deliberately malformed stored movement documents are for source quarantine only.
    if bad in {"movement","allocation"}:
        db=seeded_db(order_row=doc); state=operating_state(); conflict(db,state)
        assert preview(db,state)["open_customer_cod"]["by_currency"]=={}
    else:
        assert not projected(doc, conflict)


@pytest.mark.parametrize("fully_paid", [False, True])
def test_assigned_driver_without_a_fee_is_one_customer_estimate_and_zero_custody(fully_paid):
    def assigned(db,state):
        db.data["store_delivery_assignments"]=[{"user_id":"owner","id":"a","driver_id":"driver","order_number":"NEW",
            "order_id":"salla-NEW","status":"assigned","active":True,"assigned_at":OBSERVED}]
        state["engine"]["obligations"]["cod:salla-NEW"]={"id":"cod:salla-NEW","currency":"SAR","kind":"cod",
            "direction":"receivable","party_type":"store_driver","party_id":"driver","confirmed":"0.00","expected":"0.00"}
        state["engine"]["cod_reports"]={"cod:salla-NEW":{"currency":"SAR","order_id":"salla-NEW",
            "customer_outstanding":"77","collected":"23","gross":"100","confirmed_custody":"0.00"}}
    doc = order(paid="100" if fully_paid else "23")
    if fully_paid:
        doc["raw_by_source"]["salla_direct"]["payment_collection_status"] = "paid"
    rows = projected(doc, assigned)
    assert [(r["id"],r["orders"],r["amount"]) for r in rows] == ([] if fully_paid else [("current_assigned",1,"77.00")])


def test_native_projection_is_read_only_across_repeated_runs():
    from operational_balance_store import digest
    from operational_open_cost_exposure import project
    from test_operational_balance_integration import run as mongo

    async def scenario(db):
        await db.unified_orders.insert_many([order(), order("OLD", old=True), {**order("SAME"), "user_id": "foreign"}])
        state = operating_state()
        async def sources():
            return {name: await db[name].find({}, {"_id":0}).to_list(20) for name in
                    ("unified_orders", "store_delivery_assignments", "store_delivery_collections", "store_delivery_driver_earnings")}
        before = digest(await sources())
        first = await project(db, "owner", state, as_of=NOW)
        assert await project(db, "owner", state, as_of=NOW) == first
        assert [(r["orders"],r["amount"]) for r in first["open_customer_cod"]["by_currency"]["SAR"]["rows"]] == [(1,"77.00"),(1,"77.00")]
        assert digest(await sources()) == before
    mongo(scenario)


def test_evidence_scope_limit_does_not_become_zero_known_customers(monkeypatch):
    import operational_open_customer_cod as cod
    db=seeded_db(order_row=order())
    db.data["store_delivery_collections"]=[{"user_id":"owner","order_id":"x"}]*2
    monkeypatch.setattr(cod, "MAX_ROWS", 1)
    with pytest.raises(ValueError, match="operational_source_scope_too_large"):
        preview(db)
