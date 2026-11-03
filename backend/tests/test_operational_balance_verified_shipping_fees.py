"""A delivered service is not payment collection, a rate guess or a new bank entry."""
from copy import deepcopy
import pytest

from test_operational_balance_delivery_cutover import (
    active, driver_data, courier_data, fee_total, DELIVERED, START, NOW, HardBoundaryDB, run, row
)
from operational_balance_sources import collect_sources
from operational_balance_engine import reconcile
from operational_financial_position import build_financial_position


def fixture(*, with_rate=False):
    d = driver_data("cash")
    a = d["store_delivery_assignments"][0]
    a.update(delivery_fee_snapshot=10, financial_source="store_delivery_operational",
             financial_handoff_status="pending_mz2_driver_balance_link", accounting_status="operational_only")
    d["store_delivery_driver_earnings"] = [row(
        id="earned-one", assignment_id=a["id"], driver_id=a["driver_id"], order_id=a["order_id"],
        order_number=a["order_number"], amount="10.00", earned_at=DELIVERED, status="due",
        financial_source="store_delivery_operational", accounting_status="operational_only",
        financial_handoff_status="pending_mz2_driver_balance_link")]
    d["store_delivery_driver_settlements"] = []
    if not with_rate:
        d["mz2_shipping_setup_v2"][0]["contracts"] = []
    return d


def calculated(d, prior=None):
    state = prior or active()
    src = run(collect_sources(HardBoundaryDB(d), "owner", START, NOW, operational_state=state))
    return reconcile(state, src, NOW)


def review(result):
    return build_financial_position(result)["currencies"]["SAR"].get("driver_fee_review", [])


def test_previously_unlinked_earning_visible_separate_from_debt_and_cash():
    d, prior = fixture(), active()
    prior["engine"] = {"as_of": NOW}
    result = calculated(d, prior)
    assert fee_total(result) == 0
    assert result["engine"]["obligations"]["cod:100"]["confirmed"] == "200.00"
    assert [(r["order_number"],r["candidate_amount"],r["reason"]) for r in review(result)] == [
        ("10", "10.00", "driver_fee_history_review_required")]
    assert result["movements"] == prior["movements"]
    assert result["openings"] == prior["openings"]
    assert review(calculated(d, result)) == review(result)


def test_nested_exact_waybill_official_delivery_time_without_update_date_fallback():
    d = courier_data()
    raw = d["unified_orders"][0]["raw_by_source"]["salla_direct"]
    nested = raw.pop("shipments")[0]
    for name in ("delivered_at", "tracking_number", "shipment_id"):
        raw["shipping"].pop(name)
    raw["shipping"]["shipment"] = nested
    result = calculated(d)
    assert fee_total(result) == 10
    assert "cod:100" not in result["engine"]["obligations"]
    assert "shipping:100:cod_commission" not in result["engine"]["obligations"]
    assert any(i["code"]=="courier_cod_collection_proof_required" for i in result["engine"]["issues"])
    assert result["engine"]["facts"]["delivery_event:100"]["delivered_at"] == DELIVERED


def test_new_fresh_matching_contract_reconciles_existing_id_once_and_keeps_cod_separate():
    d = fixture(with_rate=True)
    state = calculated(d)
    fees = state["engine"]["obligations"]
    assert fees["shipping:100:base_shipping"]["confirmed"] == "10.00"
    assert fees["shipping:100:cod_commission"]["confirmed"] == "2.00"
    assert fees["cod:100"]["confirmed"] == "200.00"
    assert state["engine"]["facts"]["native_driver_earning:100"]["earning_id"] == "earned-one"
    again = calculated(d, state)
    assert again["engine"]["facts"] == state["engine"]["facts"]
    assert again["engine"]["obligations"] == state["engine"]["obligations"]
    assert review(again) == []


@pytest.mark.parametrize('field,value', [('amount','10.001'),('driver_id','wrong'),('order_number','other'),
    ('order_id','mistyped'),('currency','KWD'),('status','cancelled'),('accounting_status','posted'),
    ('earned_at',NOW),('ledger_txn_group_id','posted-a'),('financial_handoff_status','synced')])
def test_earning_invalid_never_creates_service_debt_or_rewrites_custody(field,value):
    d = fixture(with_rate=True)
    d["store_delivery_driver_earnings"][0][field] = value
    state = calculated(d)
    assert fee_total(state) == 0
    assert review(state)[0]["candidate_amount"] is None
    assert state["engine"]["obligations"]["cod:100"]["confirmed"] == "200.00"


@pytest.mark.parametrize('status,kind,expected',[
    ('posted','earning_payment',True),('pending_driver_confirmation','net_settlement',True),
    ('rejected','earning_payment',False),('posted','cod_remittance',False)])
def test_pooled_or_pending_payout_is_not_per_order_fee_allocation(status,kind,expected):
    d=fixture(with_rate=True); d["store_delivery_driver_settlements"]=[row(driver_id='driver',
        settlement_type=kind,status=status)]
    result=calculated(d)
    assert bool(review(result)) is expected
    assert result['movements'] == []
    if expected:
        assert fee_total(result)==0 and review(result)[0]['reason']=='driver_fee_payout_review_required'


def test_conflicting_rate_duplicate_and_existing_allocation_hold_without_repricing():
    d=fixture(with_rate=True); original=calculated(d)
    original['movements']=[{'id':'paid','amount':'10','kind':'payment','direction':'outgoing',
        'currency':'SAR','bank_id':'bank','bank_kind':'bank','party_id':'driver','party_type':'store_driver',
        'allocations':[{'obligation_id':'shipping:100:base_shipping','amount':'10.00'}]}]
    d['store_delivery_driver_earnings'][0]['amount']=12
    d['store_delivery_assignments'][0]['delivery_fee_snapshot']=12
    again=calculated(d, original)
    assert again['movements']==original['movements']
    assert again['engine']['obligations']['shipping:100:base_shipping']==original['engine']['obligations']['shipping:100:base_shipping']
    assert review(again)[0]['reason']=='driver_fee_saved_conflict'
    d=fixture(with_rate=True); d['mz2_shipping_setup_v2'][0]['contracts'][0]['delivery_fee']='16'
    assert review(calculated(d))[0]['reason']=='driver_fee_amount_conflict'
    d=fixture(with_rate=True); d['store_delivery_driver_earnings']*=2
    assert review(calculated(d))[0]['candidate_amount'] is None


def test_ambiguous_tariff_candidates_held_in_report_table_without_debt():
    d = fixture(with_rate=True)
    rates = d['mz2_shipping_setup_v2'][0]['contracts']
    rates.append(dict(rates[0], id='second'))
    state = calculated(d)
    data=build_financial_position(state)['currencies']['SAR']
    table=next(t for t in data['tables'] if t['id']=='driver_fee_review')
    assert table['section']=='fulfillment' and table['totals']=={}
    assert table['rows'][0]['candidate_amount']=='10.00'
    assert table['rows'][0]['reason']=='driver_fee_policy_review_required'
    assert fee_total(state)==0


def test_native_driver_price_without_shipping_contract_recognized_once():
    d = fixture()
    state = calculated(d)
    assert fee_total(state) == 10
    assert state["engine"]["obligations"]["shipping:100:base_shipping"]["confirmed"] == "10.00"
    assert "shipping:100:cod_commission" not in state["engine"]["obligations"]
    assert state["engine"]["obligations"]["cod:100"]["confirmed"] == "200.00"
    assert state["engine"]["facts"]["native_driver_earning:100"]["earning_id"] == "earned-one"
    assert not review(state) and not state["movements"]
    assert state["openings"] == active()["openings"]
    assert calculated(d, state) == state


def test_multiple_shipping_setups_cannot_silently_hide_a_conflicting_driver_rate():
    d = fixture(with_rate=True)
    d["mz2_shipping_setup_v2"].append(deepcopy(d["mz2_shipping_setup_v2"][0]))
    state = calculated(d)
    assert fee_total(state) == 0
    assert review(state)[0]["reason"] == "driver_fee_policy_review_required"


@pytest.mark.parametrize('old',[True,False])
def test_no_carrier_confirmation_from_order_updated_or_workflow_timestamp(old):
    d=courier_data(); r=d['unified_orders'][0]['raw_by_source']['salla_direct']
    if not old:
        r['created_at']='2026-10-06T01:00:00+00:00'
    r['shipments'][0].pop('delivered_at'); r['shipping'].pop('delivered_at')
    state=calculated(d)
    assert fee_total(state)==0 and 'shipping:100' not in state['engine']['facts']
    table=next(t for t in build_financial_position(state)['currencies']['SAR']['tables'] if t['id']=='courier_fee_review')
    assert table['rows'][0]['samples']=='10'


def test_historical_courier_new_proof_never_creates_opening_overlap():
    prior=active(); prior['engine']={'as_of':NOW}
    result=calculated(courier_data(), prior)
    assert fee_total(result)==0 and 'cod:100' not in result['engine']['obligations']
    held=next(i for i in result['engine']['issues'] if i['code']=='courier_fee_historical_review_required')
    assert held['courier_fee_review']['candidate_amount'] is not None
    assert result['openings']==prior['openings'] and result['movements']==prior['movements']


@pytest.mark.parametrize('bad', ['wrong_waybill','timezone','conflicting_dates','other_shipment','status'])
def test_nested_shipment_invalid_never_recognizes_fees(bad):
    d=courier_data(); raw=d['unified_orders'][0]['raw_by_source']['salla_direct']
    nested=raw.pop('shipments')[0]
    for name in ('delivered_at','tracking_number','shipment_id'):
        raw['shipping'].pop(name)
    raw['shipping']['shipment']=nested
    if bad=='wrong_waybill': nested['tracking_number']='WRONG'
    if bad=='timezone': nested['delivered_at']='2026-10-06T12:00:00'
    if bad=='conflicting_dates': raw['shipping']['delivered_at']=NOW
    if bad=='other_shipment': nested['id']='replaced'
    if bad=='status': nested['status']='shipped'
    assert fee_total(calculated(d))==0


def test_orphan_or_duplicate_native_earning_cannot_fall_back_to_a_courier():
    d = courier_data()
    d['store_delivery_driver_earnings'] = fixture()['store_delivery_driver_earnings']
    state = calculated(d)
    assert fee_total(state) == 0
    assert 'cod:100' not in state['engine']['obligations']
    assert any(i['code']=='driver_fee_evidence_conflict' for i in state['engine']['issues'])
    d = fixture(with_rate=True)
    earning = d['store_delivery_driver_earnings'][0]
    d['store_delivery_driver_earnings'].append(dict(earning, id='other', assignment_id='orphan'))
    state = calculated(d)
    assert fee_total(state) == 0
    assert state['engine']['obligations']['cod:100']['confirmed'] == '200.00'


def test_recent_order_current_waybill_is_frozen_and_changed_waybill_cannot_reprice_it():
    d = courier_data()
    source = d['unified_orders'][0]
    raw = source['raw_by_source']['salla_direct']
    raw['created_at']='2026-10-06T01:00:00+00:00'
    first = calculated(d)
    assert first['engine']['facts']['delivery_event:100']['waybill']=='TEST-AWB'
    source['salla_shipping_current']['tracking_number']='OTHER-AWB'
    raw['shipping']['tracking_number']=raw['shipments'][0]['tracking_number']='OTHER-AWB'
    again = calculated(d, first)
    assert again['engine']['facts']['delivery_event:100']==first['engine']['facts']['delivery_event:100']
    assert again['engine']['obligations']==first['engine']['obligations']
    assert any(i['code']=='operational_delivery_confirmed_evidence_changed' for i in again['engine']['issues'])


def test_recent_external_shipment_label_does_not_confirm_fee_while_order_disagrees():
    d = courier_data()
    source = d['unified_orders'][0]; raw=source['raw_by_source']['salla_direct']
    raw.update(created_at='2026-10-06T01:00:00+00:00', status='processing')
    result = calculated(d)
    assert fee_total(result)==0 and not result['movements']
    assert any(i['code']=='operational_delivery_order_shipment_status_conflict' and i.get('courier_fee_review')
               for i in result['engine']['issues'])


def test_dated_shipment_without_matching_awb_and_changed_native_driver_identity_never_confirm():
    d = courier_data(); raw = d['unified_orders'][0]['raw_by_source']['salla_direct']
    raw['shipping'].pop('tracking_number')
    raw['shipments'][0].pop('tracking_number')
    assert fee_total(calculated(d))==0
    d = fixture(with_rate=True); raw = d['unified_orders'][0]['raw_by_source']['salla_direct']
    raw['created_at']='2026-10-06T01:00:00+00:00'
    d['store_delivery_assignments'][0]['order_id']='other-order-id'
    state = calculated(d)
    assert 'cod:100' not in state['engine']['obligations']
    assert fee_total(state)==0 and not state['movements']


def test_wrong_order_native_assignment_cannot_supply_cash_when_earning_is_missing():
    d=fixture(with_rate=True); order=d['unified_orders'][0]['raw_by_source']['salla_direct']
    order['created_at']='2026-10-06T01:00:00+00:00'
    d['store_delivery_assignments'][0]['order_id']='other-order-id'
    d['store_delivery_driver_earnings'].clear()
    result=calculated(d)
    assert 'cod:100' not in result['engine']['obligations']
    assert 'shipping:100' not in result['engine']['facts']


@pytest.mark.parametrize("posted,patch,expected_fee", [
    (START, {}, 10), (DELIVERED, {}, 0), (NOW, {}, 0),
    (START, {"driver_decision_by": "different-driver"}, 0),
    (START, {"financial_source": "legacy"}, 0),
    (START, {"status": "pending_driver_confirmation"}, 0),
    (START, {"delivery_fee_settled_amount": "20"}, 0),
    (START, {"driver_decision_at": NOW}, 0),
    (START, {"created_at": NOW}, 0),
    (START, {"settlement_type": "net_settlement", "earning_offset": "15"}, 10),
    (START, {"settlement_type": "cod_remittance"}, 0),
    (START, {"status": "rejected"}, 0),
])
def test_closed_payout_only_affects_deliveries_that_existed_at_posting(posted, patch, expected_fee):
    d = fixture()
    payout = row(id="unique-payout", driver_id="driver", status="posted", posting_scope="operational_balance",
                 settlement_type="earning_payment", accounting_status="operational_only",
                 financial_source="store_delivery_operational", financial_handoff_status="pending_mz2_driver_balance_link",
                 driver_decision_by="driver", driver_decision="approved", amount="15",
                 delivery_fee_settled_amount="15", posted_at=posted, driver_decision_at=posted, created_at=START)
    payout.update(patch)
    d["store_delivery_driver_settlements"] = [payout]
    result = calculated(d)
    assert fee_total(result) == expected_fee
    assert result["engine"]["obligations"]["cod:100"]["confirmed"] == "200.00"
    assert result["movements"] == []
    if not expected_fee:
        assert review(result)[0]["reason"] == "driver_fee_payout_review_required"
