"""A delivered service is not payment collection, a rate guess or a new bank entry."""
from copy import deepcopy
from datetime import datetime
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


def legacy_driver_delivery(saved_at, delivered_at):
    data = fixture()
    data["store_delivery_assignments"][0].update(delivery_fee_snapshot="20.00", delivered_at=delivered_at)
    data["store_delivery_driver_earnings"][0].update(amount="20.00", earned_at=delivered_at)
    data["store_delivery_collections"] = []
    raw = data["unified_orders"][0]["raw_by_source"]["salla_direct"]
    raw.update(created_at="2026-10-06T01:00:00+00:00", total={"amount": "327.24", "currency": "SAR"},
               paid_amount="327.24", remaining_amount="0.00",
               payment={"method": "mada", "status": "paid", "paid_amount": "327.24",
                        "paid_at": "2026-10-06T02:00:00+00:00", "reference": "paid-order"})
    prior = active()
    prior["engine"] = {"as_of": NOW, "facts": {
        "shipping:100": {"id": "driver", "party_type": "store_driver", "cost": None,
                         "delivered_at": saved_at, "cod_amount": "0.00", "cod_evidence": {},
                         "component_mode": False},
        "cod_collection:100": {"amount": "0.00", "evidence": {}, "party_id": "driver"},
    }}
    return data, prior


@pytest.mark.parametrize("saved_at", [
    "2026-10-06T12:00:00+00:00", "2026-10-06T12:00:01+00:00", "2026-10-06T15:00:01+03:00",
])
def test_unpriced_legacy_second_precision_recognizes_driver_and_provider_once(saved_at):
    native_time = "2026-10-06T12:00:00.912175+00:00"
    data, prior = legacy_driver_delivery(saved_at, native_time)
    before = deepcopy(data)
    result = calculated(data, prior)
    assert fee_total(result) == 20
    assert result["engine"]["obligations"]["shipping:100:base_shipping"]["confirmed"] == "20.00"
    assert result["engine"]["obligations"]["provider:100"]["confirmed"] == "327.24"
    assert "cod:100" not in result["engine"]["obligations"]
    assert result["engine"]["facts"]["cod_collection:100"] == prior["engine"]["facts"]["cod_collection:100"]
    assert result["engine"]["facts"]["shipping:100"] == prior["engine"]["facts"]["shipping:100"]
    assert result["engine"]["facts"]["delivery_event:100"]["delivered_at"] == native_time
    assert result["engine"]["facts"]["native_driver_earning:100"]["delivered_at"] == native_time
    assert result["movements"] == prior["movements"] and result["openings"] == prior["openings"]
    assert not review(result)
    assert calculated(data, result) == result
    assert data == before


@pytest.mark.parametrize("saved_at,delivered_at", [
    ("2026-10-06T12:00:00.900000+00:00", "2026-10-06T12:00:00.912175+00:00"),
    ("2026-10-06T12:00:02+00:00", "2026-10-06T12:00:00.912175+00:00"),
    ("2026-10-06T12:00:01+00:00", "2026-10-06T12:00:00+00:00"),
    ("2026-10-07T00:00:00+00:00", "2026-10-06T23:59:59.912175+00:00"),
    ("2026-10-07T00:00:00+03:00", "2026-10-06T20:59:59.912175+00:00"),
])
def test_legacy_driver_time_conflicts_preserve_saved_facts(saved_at, delivered_at):
    data, prior = legacy_driver_delivery(saved_at, delivered_at)
    result = calculated(data, prior)
    assert fee_total(result) == 0
    assert review(result)[0]["reason"] == "driver_fee_saved_conflict"
    assert result["engine"]["facts"] == prior["engine"]["facts"]
    assert "provider:100" not in result["engine"]["obligations"]


@pytest.mark.parametrize("field,value", [
    ("cost", "20.00"), ("cost", "25.00"), ("component_mode", True),
    ("party_type", "courier"), ("id", "other-driver"),
])
def test_precision_compatibility_cannot_change_a_priced_fee_or_saved_driver(field, value):
    data, prior = legacy_driver_delivery("2026-10-06T12:00:01+00:00", "2026-10-06T12:00:00.912175+00:00")
    prior["engine"]["facts"]["shipping:100"][field] = value
    result = calculated(data, prior)
    assert fee_total(result) == 0
    assert review(result)[0]["reason"] == "driver_fee_saved_conflict"
    assert result["engine"]["facts"] == prior["engine"]["facts"]


@pytest.mark.parametrize("fact_key", ["shipping:100:base_shipping", "shipping_fee:100"])
def test_legacy_precision_holds_conflicting_saved_fee_facts_without_an_obligation(fact_key):
    data, prior = legacy_driver_delivery("2026-10-06T12:00:01+00:00", "2026-10-06T12:00:00.912175+00:00")
    prior["engine"]["facts"][fact_key] = {"cost": "25.00"}
    result = calculated(data, prior)
    assert fee_total(result) == 0
    assert review(result)[0]["reason"] == "driver_fee_saved_conflict"
    assert result["engine"]["facts"] == prior["engine"]["facts"]


def test_legacy_precision_replay_keeps_base_fee_and_cod_commission_separate():
    data = fixture(with_rate=True)
    native_time = "2026-10-06T12:00:00.912175+00:00"
    data["store_delivery_assignments"][0]["delivered_at"] = native_time
    data["store_delivery_driver_earnings"][0]["earned_at"] = native_time
    data["store_delivery_collections"][0]["collected_at"] = native_time
    priced = calculated(data)
    prior = active()
    prior["engine"] = {"as_of": NOW, "facts": {"shipping:100": {
        **priced["engine"]["facts"]["shipping:100"], "cost": None, "component_mode": False,
        "delivered_at": "2026-10-06T12:00:01+00:00"}}}
    result = calculated(data, prior)
    assert fee_total(result) == 12
    assert result["engine"]["facts"]["shipping:100:base_shipping"]["cost"] == "10.00"
    assert result["engine"]["facts"]["shipping_fee:100"]["cost"] == "12.00"
    assert not review(result)
    assert calculated(data, result) == result


@pytest.mark.parametrize("saved_at", [
    datetime(2026, 10, 6, 12, 0, 1), {"date": "2026-10-06 12:00:01", "timezone": "UTC"},
])
def test_legacy_precision_keeps_saved_timestamp_format_requirements(saved_at):
    data, prior = legacy_driver_delivery(saved_at, "2026-10-06T12:00:00.912175+00:00")
    result = calculated(data, prior)
    assert fee_total(result) == 0
    assert result["engine"]["facts"] == prior["engine"]["facts"]


@pytest.mark.parametrize("field,value", [
    ("assignment_id", "other-assignment"), ("order_id", "other-order"),
    ("driver_id", "other-driver"), ("earned_at", "2026-10-06T12:00:01+00:00"),
])
def test_legacy_precision_requires_the_same_native_assignment_and_earning(field, value):
    data, prior = legacy_driver_delivery("2026-10-06T12:00:01+00:00", "2026-10-06T12:00:00.912175+00:00")
    data["store_delivery_driver_earnings"][0][field] = value
    result = calculated(data, prior)
    assert fee_total(result) == 0
    assert review(result)[0]["reason"] == "driver_fee_evidence_conflict"
    assert result["engine"]["facts"] == prior["engine"]["facts"]


@pytest.mark.parametrize("status", ["posted", "pending_driver_confirmation"])
def test_legacy_precision_keeps_native_payout_overlap_under_review(status):
    data, prior = legacy_driver_delivery("2026-10-06T12:00:01+00:00", "2026-10-06T12:00:00.912175+00:00")
    data["store_delivery_driver_settlements"] = [row(
        driver_id="driver", settlement_type="earning_payment", status=status)]
    result = calculated(data, prior)
    assert fee_total(result) == 0
    assert review(result)[0]["reason"] == "driver_fee_payout_review_required"
    assert result["engine"]["facts"] == prior["engine"]["facts"]


def test_legacy_precision_keeps_an_existing_fee_allocation_without_a_matching_obligation_held():
    data, prior = legacy_driver_delivery("2026-10-06T12:00:01+00:00", "2026-10-06T12:00:00.912175+00:00")
    prior["movements"] = [{"id": "paid", "party_type": "store_driver", "party_id": "driver", "currency": "SAR",
                           "kind": "payment", "direction": "outgoing", "amount": "20.00",
                           "allocations": [{"obligation_id": "shipping:100:base_shipping", "amount": "20.00"}]}]
    result = calculated(data, prior)
    assert fee_total(result) == 0
    assert review(result)[0]["reason"] == "driver_fee_saved_conflict"
    assert result["movements"] == prior["movements"]
    assert result["engine"]["facts"] == prior["engine"]["facts"]


def test_legacy_precision_preserves_a_booked_driver_fee_and_its_payment_on_replay_and_price_change():
    data, prior = legacy_driver_delivery("2026-10-06T12:00:01+00:00", "2026-10-06T12:00:00.912175+00:00")
    booked = calculated(data, prior)
    booked["movements"] = [{"id": "paid", "party_type": "store_driver", "party_id": "driver", "currency": "SAR",
                            "kind": "payment", "direction": "outgoing", "amount": "20.00",
                            "allocations": [{"obligation_id": "shipping:100:base_shipping", "amount": "20.00"}]}]
    repeated = calculated(data, booked)
    assert not review(repeated)
    assert repeated["engine"]["obligations"] == booked["engine"]["obligations"]
    assert repeated["engine"]["facts"] == booked["engine"]["facts"]
    data["store_delivery_assignments"][0]["delivery_fee_snapshot"] = "25.00"
    data["store_delivery_driver_earnings"][0]["amount"] = "25.00"
    held = calculated(data, repeated)
    assert review(held)[0]["reason"] == "driver_fee_saved_conflict"
    assert held["engine"]["obligations"] == booked["engine"]["obligations"]
    assert held["engine"]["facts"] == booked["engine"]["facts"]
    assert held["movements"] == booked["movements"] and held["openings"] == booked["openings"]


@pytest.mark.parametrize("fact_key,field", [("delivery_event:100", "id"), ("native_driver_earning:100", "earning_id")])
def test_legacy_precision_preserves_existing_event_and_earning_provenance(fact_key, field):
    data, prior = legacy_driver_delivery("2026-10-06T12:00:01+00:00", "2026-10-06T12:00:00.912175+00:00")
    accepted = calculated(data, prior)
    prior["engine"]["facts"][fact_key] = {**accepted["engine"]["facts"][fact_key], field: "different-proof"}
    result = calculated(data, prior)
    assert fee_total(result) == 0
    assert all(result["engine"]["facts"][key] == value for key, value in prior["engine"]["facts"].items())
    assert any(issue["code"] == "operational_delivery_confirmed_evidence_changed" for issue in result["engine"]["issues"])


def test_previously_unlinked_poststart_earning_accrues_once_after_previous_calculation():
    d, prior = fixture(), active()
    prior["engine"] = {"as_of": NOW}
    result = calculated(d, prior)
    assert fee_total(result) == 10
    assert result["engine"]["obligations"]["cod:100"]["confirmed"] == "200.00"
    assert review(result) == []
    assert result["movements"] == prior["movements"]
    assert result["openings"] == prior["openings"]
    assert calculated(d, result) == result


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
    r.pop('status_changed_at')
    if not old:
        r['created_at']='2026-10-06T01:00:00+00:00'
    r['shipments'][0].pop('delivered_at'); r['shipping'].pop('delivered_at')
    state=calculated(d)
    assert fee_total(state)==0 and 'shipping:100' not in state['engine']['facts']
    table=next(t for t in build_financial_position(state)['currencies']['SAR']['tables'] if t['id']=='courier_fee_review')
    assert table['rows'][0]['samples']=='10'


def test_previously_unlinked_poststart_courier_fee_accrues_once_after_previous_calculation():
    prior=active(); prior['engine']={'as_of':NOW}
    result=calculated(courier_data(), prior)
    assert fee_total(result)==12
    assert result['engine']['obligations']['cod:100']['confirmed']=='200.00'
    assert not any(i['code']=='courier_fee_historical_review_required' for i in result['engine']['issues'])
    assert result['openings']==prior['openings'] and result['movements']==prior['movements']
    assert calculated(courier_data(), result)==result


@pytest.mark.parametrize('allocation', [None, 'shipping:100:base_shipping', 'unknown-payable'])
def test_courier_payout_overlap_holds_new_fee_without_rewriting_settlements(allocation):
    prior=active(); prior['engine']={'as_of':NOW}
    prior['movements']=[{'id':'paid', 'party_type':'courier', 'party_id':'carrier', 'currency':'SAR',
        'kind':'payment', 'direction':'outgoing', 'amount':'10.00',
        'allocations':[] if allocation is None else [{'obligation_id':allocation, 'amount':'10.00'}]}]
    result=calculated(courier_data(), prior)
    assert fee_total(result)==0
    assert result['movements']==prior['movements'] and result['openings']==prior['openings']
    assert any(i['code']=='courier_fee_historical_review_required' for i in result['engine']['issues'])


def test_courier_payment_allocated_to_opening_does_not_hide_new_poststart_fee():
    prior=active(); prior['engine']={'as_of':NOW}
    prior['openings']['old-courier-balance']={'id':'old-courier-balance', 'party_type':'courier',
        'party_id':'carrier', 'currency':'SAR', 'direction':'for_party', 'name':'Carrier', 'amount':'10.00'}
    prior['movements']=[{'id':'paid', 'party_type':'courier', 'party_id':'carrier', 'currency':'SAR',
        'kind':'payment', 'direction':'outgoing', 'amount':'10.00',
        'allocations':[{'obligation_id':'old-courier-balance', 'amount':'10.00'}]}]
    result=calculated(courier_data(), prior)
    assert fee_total(result)==12
    assert result['movements']==prior['movements'] and result['openings']==prior['openings']
    assert calculated(courier_data(), result)==result


@pytest.mark.parametrize('kind', ['store_driver', 'courier'])
def test_native_shipping_overlap_checked_before_first_fee_and_keeps_collection_evidence(monkeypatch, kind):
    inspected=[]
    async def conflict(db, owner, candidates, *, max_rows):
        assert owner=='owner' and max_rows>0
        inspected.extend(candidates)
        return {'10':'Existing native fee posting'}
    monkeypatch.setattr('operational_historical_shipping_fee.native_conflicts', conflict)
    data=fixture() if kind=='store_driver' else courier_data()
    prior=active(); prior['engine']={'as_of':NOW}
    result=calculated(data, prior)
    assert [(p['order_number'],p['party_type']) for p in inspected]==[('10',kind)]
    assert fee_total(result)==0
    assert result['engine']['obligations']['cod:100']['confirmed']=='200.00'
    assert 'native_driver_earning:100' not in result['engine']['facts']
    code='driver_fee_native_overlap' if kind=='store_driver' else 'courier_fee_native_overlap'
    assert any(i['code']==code for i in result['engine']['issues'])
    assert result['movements']==prior['movements'] and result['openings']==prior['openings']


@pytest.mark.parametrize('kind', ['store_driver', 'courier'])
def test_booked_fee_and_its_payment_survive_later_native_overlap_report(monkeypatch, kind):
    data=fixture() if kind=='store_driver' else courier_data()
    first=calculated(data)
    first['movements']=[{'id':'paid', 'party_type':kind,
        'party_id':'driver' if kind=='store_driver' else 'carrier', 'currency':'SAR',
        'kind':'payment', 'direction':'outgoing', 'amount':'10.00',
        'allocations':[{'obligation_id':'shipping:100:base_shipping', 'amount':'10.00'}]}]
    async def unexpected(*args, **kwargs):
        raise AssertionError('Already booked delivery must preserve its frozen obligation')
    monkeypatch.setattr('operational_historical_shipping_fee.native_conflicts', unexpected)
    again=calculated(data, first)
    assert again['engine']['obligations']==first['engine']['obligations']
    assert again['engine']['facts']==first['engine']['facts']
    assert again['movements']==first['movements']


@pytest.mark.parametrize('delivered_at', [
    '2026-10-31T23:50:00Z', '2026-11-01T09:04:35Z', '2026-11-02T10:17:42Z',
    '2026-11-02T11:04:39Z', '2026-11-02T09:18:23Z', '2026-10-31T23:40:45Z',
    '2026-10-31T21:30:01.284707Z',
])
def test_old_order_delivery_on_or_after_riyadh_start_accrues_native_driver_fee(delivered_at):
    start='2026-11-01T00:30:01.284707+03:00'
    now='2026-11-04T12:00:00+03:00'
    data=fixture()
    data['store_delivery_assignments'][0]['delivered_at']=delivered_at
    data['store_delivery_driver_earnings'][0]['earned_at']=delivered_at
    data['store_delivery_collections'][0]['collected_at']=delivered_at
    prior=active(); prior.update(started_at=start, engine={'as_of':now})
    src=run(collect_sources(HardBoundaryDB(data), 'owner', start, now, operational_state=prior))
    result=reconcile(prior, src, now)
    assert fee_total(result)==10
    assert result['engine']['obligations']['shipping:100:base_shipping']['party_id']=='driver'
    assert not review(result)
    assert result['movements']==prior['movements'] and result['openings']==prior['openings']
    assert reconcile(result, src, now)==result


@pytest.mark.parametrize('bad', ['wrong_waybill','timezone','conflicting_dates','other_shipment','status'])
def test_nested_shipment_cannot_replace_order_identity_or_supply_unproved_cod(bad):
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
    result = calculated(d)
    # A valid order transition can earn the service independently of a lagging
    # label or its invalid timestamp, but never assign another waybill's fee.
    assert fee_total(result) == (0 if bad in {'wrong_waybill', 'other_shipment'} else 10)
    assert 'cod:100' not in result['engine']['obligations']


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


def test_order_status_can_earn_without_awb_but_changed_native_driver_identity_cannot():
    d = courier_data(); raw = d['unified_orders'][0]['raw_by_source']['salla_direct']
    raw['shipping'].pop('tracking_number')
    raw['shipments'][0].pop('tracking_number')
    assert fee_total(calculated(d))==10
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
