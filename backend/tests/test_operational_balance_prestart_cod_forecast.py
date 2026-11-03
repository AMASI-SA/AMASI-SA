"""Pre-cutover expected customer money is never cash or a posted liability."""
from copy import deepcopy
import pytest
from operational_financial_position_core import build_financial_position
from test_operational_open_cost_exposure import native_order, seeded_db, operating_state, preview, NOW, START
from salla_shipping import extract_shipping

SEEN = "2026-11-01T15:00:00+00:00"


def proved(number="OLD", currency="SAR"):
    doc = native_order(number)
    raw = doc["raw_by_source"]["salla_direct"]
    def money(a): return {"amount":a,"currency":currency}
    raw.update(currency=currency, updated_at=SEEN, payment_method="cod", amounts={"total":money("100.00")},
               payment_actions={"remaining_action":{"paid_amount":money("23.00"),"remaining_amount":money("77.00")}},
               shipments=[{"id":"shipment-"+number,"tracking_number":"awb-"+number,"courier_name":"iMile","courier_id":"imile",
                           "status":"in_transit","updated_at":SEEN}])
    doc.update(salla_order_id=raw["id"], salla_shipping_current=extract_shipping(raw))
    return doc


def test_proved_prestart_cod_is_counted_once_in_its_currency_outside_debt_and_cash():
    db=seeded_db(order_row=proved("A"))
    db.data["unified_orders"].append(proved("B","QAR"))
    state={**operating_state(),"openings":{},"movements":[]}
    before=deepcopy((db.data,state))
    exp=preview(db,state)
    assert preview(db,state)==exp
    assert exp["prestart_open_cod"]["by_currency"]=={
        "SAR":{"orders":1,"amount":"77.00"}, "QAR":{"orders":1,"amount":"77.00"}}
    report=build_financial_position(state,cost_exposure=exp)
    for ccy in ("SAR","QAR"):
        tab=next(t for t in report["currencies"][ccy]["tables"] if t["id"]=="cod_expected")
        assert len(tab["rows"])==1 and tab["rows"][0]["amount"]=="77.00" and tab["rows"][0]["orders"]==1
        assert not tab["complete"] and tab["totals"]["amount"] is None and tab["totals"]["orders"] is None
        assert report["currencies"][ccy]["metrics"]["debts_receivable"]=="0.00"
        assert report["currencies"][ccy]["metrics"]["liquidity"]=="0.00"
    assert before==(db.data,state)


@pytest.mark.parametrize("bad",[
    "wrong_owner_key","stale","late","old_observation","missing_paid","missing_due","wrong_currency",
    "imbalanced","method_conflict","refund","order_delivered","order_cancelled","current_delivered",
    "shipment_delivered","shipping_delivered","replaced","driver","duplicate","saved_cod","booked_foreign",
    "movement","saved_delivery","recorded_collection","orphan_alias","local_method","pending_watermark",
])
def test_ambiguity_withholds_prestart_forecast(bad):
    doc=proved()
    raw=doc["raw_by_source"]["salla_direct"]
    current=doc["salla_shipping_current"]; balances=raw["payment_actions"]["remaining_action"]
    state={**operating_state(),"movements":[],"openings":{}}
    if bad=="wrong_owner_key": doc["salla_order_id"]="other"
    if bad=="stale": doc["g47_salla_snapshot"]={"requires_authoritative_refresh":True}
    if bad=="late": raw["updated_at"]="2026-11-03T00:00:00Z"
    if bad=="old_observation": raw["updated_at"]="2026-10-30T11:00:00Z"
    if bad=="missing_paid": del balances["paid_amount"]
    if bad=="missing_due": del balances["remaining_amount"]
    if bad=="wrong_currency": balances["paid_amount"]["currency"]="QAR"
    if bad=="imbalanced": balances["remaining_amount"]["amount"]="100.00"
    if bad=="method_conflict": raw["payment"]={"method":"mada"}
    if bad=="refund": raw["payment_actions"]["refund_action"]={"refunded_transactions":[{"id":"refund"}]}
    if bad=="order_delivered": raw["status"]="delivered"
    if bad=="order_cancelled": raw["status"]="cancelled"
    if bad=="current_delivered": current["status"]="delivered"
    if bad=="shipment_delivered": raw["shipments"][0]["delivered_at"]=SEEN
    if bad=="shipping_delivered": raw["shipping"]={"status":"delivered"}
    if bad=="pending_watermark": doc["g47_salla_snapshot"]={"component_pending":True}
    if bad=="local_method": current["method"]="store_driver"
    if bad=="replaced": current["superseded_shipment_ids"]=[current["shipment_id"]]
    db=seeded_db(order_row=doc)
    if bad=="driver": db.data["store_delivery_assignments"]=[{"user_id":"owner","order_id":raw["id"],"order_number":"other","status":"out_for_delivery"}]
    if bad=="duplicate": db.data["unified_orders"].append(deepcopy(doc))
    if bad=="orphan_alias": db.data["unified_orders"].append({"user_id":"owner","order_number":"OLD"})
    if bad=="recorded_collection": state["engine"]["facts"]={"cod_collection:"+raw["id"]:{"amount":"77.00"}}
    if bad in ("saved_cod","booked_foreign"):
        state["engine"]["obligations"]["cod:"+raw["id"]]={"currency":"QAR" if bad=="booked_foreign" else "SAR"}
    if bad=="movement": state["movements"]=[{"allocations":[{"obligation_id":"cod:"+raw["id"]}]}]
    if bad=="saved_delivery": state["engine"]["facts"]={"shipping:"+raw["id"]:{"delivered_at":SEEN}}
    assert preview(db,state)["prestart_open_cod"]["by_currency"]=={}


def test_forecast_does_not_cross_snapshots_or_the_cutover_boundary():
    expected=preview(seeded_db(order_row=proved()),operating_state())
    state={**operating_state(),"movements":[],"openings":{}}
    for old in ({"as_of":SEEN},{"started_at":SEEN},{"engine_as_of":SEEN}):
        report=build_financial_position(state,cost_exposure={**expected,**old})
        for data in report["currencies"].values():
            table=next(t for t in data["tables"] if t["id"]=="cod_expected")
            assert not any(r["id"]=="prestart_customer_cod" for r in table["rows"])
    doc=proved()
    doc["raw_by_source"]["salla_direct"]["created_at"]=START
    assert not preview(seeded_db(order_row=doc))["prestart_open_cod"]["by_currency"]
