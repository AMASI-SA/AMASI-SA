"""Sealed driver bank/POS audit capture parity and repeated-history regression."""
from copy import deepcopy
from decimal import Decimal
import operational_financial_activity as activity
from operational_balance_service import report
from operational_balance_store import digest
from test_operational_balance_activity_cash_projection import state_fixture, receipt, STAMP


def reviewed(state, index, method):
    key = digest([state['owner_id'], 'operational-driver-review', str(index), 1])
    fact = {'id': key, 'financial_source': 'operational', 'review_id': str(index), 'review_revision': 1,
        'order_id': 'order-'+str(index), 'driver_id': 'driver', 'payment_method': method,
        'amount': '20.00', 'currency': 'SAR', 'destination_id': 'bank' if method == 'bank_transfer' else 'pos',
        'destination_type': 'bank' if method == 'bank_transfer' else 'pos_receivable', 'destination_name': 'destination',
        'approved_at': STAMP, 'approved_by': 'reviewer', 'receipt_hash': 'synthetic-'+str(index),
        'movement_id': None, 'obligation_id': None}
    if method == 'bank_transfer':
        fact['movement_id'] = 'review-'+str(index)
        state['movements'].append(receipt(fact['movement_id'], amount='20.00', order_id=fact['order_id'],
            driver_review_event_id=key, receipt_hash=fact['receipt_hash'], kind='collection'))
    else:
        fact['obligation_id'] = 'pos-'+str(index)
        state['engine']['obligations'][fact['obligation_id']] = {'id':fact['obligation_id'], 'kind':'driver_payment_review',
            'party_type':'pos_receivable','party_id':'pos','name':'destination','currency':'SAR','direction':'receivable',
            'confirmed':'20.00','expected':'0.00','order_id':fact['order_id'],'driver_review_event_id':key}
    fact['seal'] = digest(fact)
    state.setdefault('driver_payment_review_facts', {})[key] = fact
    return {'id': 'audit-'+str(index), 'action':'driver_payment_review_approved', 'after':fact}


def fixture(history=0):
    state = state_fixture()
    state['openings']['driver'] = {'id':'driver-opening','party_type':'store_driver','party_id':'driver',
        'name':'driver','currency':'SAR','direction':'for_party','amount':'100.00'}
    state['movements'] = [receipt('history-'+str(i)) for i in range(history)]
    return state


def test_driver_bank_pos_balances_match_full_report_through_settlement_correction_and_debt_changes(monkeypatch):
    monkeypatch.setattr(activity, 'now', lambda: STAMP)
    state = fixture(50)
    for index in range(40):
        method = 'bank_transfer' if index % 2 == 0 else 'card_terminal'
        if index % 4 == 0:
            state['engine']['obligations']['driver-fee'] = {'id':'driver-fee','kind':'shipping','party_type':'store_driver',
                'party_id':'driver','name':'driver','currency':'SAR','direction':'payable','confirmed':str(index+10),
                'expected':'100.00','incomplete':index == 8}
        if index % 5 == 0:
            state['movements'].append(receipt('pay-'+str(index), party_type='store_driver',party_id='driver',
                amount='15.00',kind='payment',direction='outgoing',allocations=[{'obligation_id':'driver-fee','amount':'5.00'}]))
        if index % 7 == 0:
            state['movements'].append(receipt('correct-'+str(index),party_type='store_driver',party_id='driver',
                amount='3.00',kind='correction',direction='incoming'))
        if index == 15:
            state['movements'].append(receipt('pos-settle',party_type='pos_receivable',party_id='pos',
                amount='25.00',kind='settlement',allocations=[{'obligation_id':'pos-1','amount':'25.00'}]))
        event=reviewed(state,index,method)
        before=deepcopy(state)
        activity.capture_after_balances(state,event)
        snap,=event['financial_activity_snapshots']
        projected=report(state)
        rows={(p['party_type'],p['party_id']):p for p in projected['parties']}
        party=rows['store_driver','driver']
        if index in range(8,12):
            assert snap['counterparty_balance_after'] is None
        else:
            assert snap['counterparty_balance_after']==format(Decimal(party['outstanding_receivable'])-Decimal(party['outstanding_payable']),'.2f')
        account=rows[('bank','bank') if method=='bank_transfer' else ('pos_receivable','pos')]
        expected=account['actual'] if method=='bank_transfer' else format(Decimal(account['outstanding_receivable'])-Decimal(account['outstanding_payable']),'.2f')
        assert snap['account_balance_after']==expected
        assert state==before


def benchmark(method, count=100, history=5000):
    from time import perf_counter
    state=fixture(history)
    values=[]
    start=perf_counter()
    for i in range(count):
        event=reviewed(state,i,method)
        activity.capture_after_balances(state,event)
        values.append(event['financial_activity_snapshots'])
    return perf_counter()-start,values


def test_hundred_real_sealed_bank_and_pos_captures_do_not_compute_full_reports(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Driver capture must not compute unrelated full report')
    monkeypatch.setattr('operational_balance_service.report', forbidden)
    for method in ('bank_transfer','card_terminal'):
        seconds, batches=benchmark(method)
        assert len(batches)==100
        for index, (snap,) in enumerate(batches):
            assert snap['counterparty_balance_after']=='-100.00'
            assert snap['counterparty_balance_basis']=='confirmed_obligations'
            assert snap['account_balance_after']==format(Decimal(15000 if method=='bank_transfer' else 0)+Decimal(20)*(index+1),'.2f')


def test_driver_projection_observes_edited_debt_and_pos_allocations_without_mutating_prior_snapshot():
    state=fixture()
    event=reviewed(state,1,'card_terminal')
    activity.capture_after_balances(state,event)
    first=deepcopy(event['financial_activity_snapshots'])
    state['openings']['driver']['amount']='250.00'
    state['movements'].append(receipt('pos-paid',party_type='pos_receivable',party_id='pos',amount='7.00',
        kind='settlement',allocations=[{'obligation_id':'pos-1','amount':'7.00'}]))
    event2=reviewed(state,2,'card_terminal')
    activity.capture_after_balances(state,event2)
    assert event2['financial_activity_snapshots'][0]['account_balance_after']=='33.00'
    assert event2['financial_activity_snapshots'][0]['counterparty_balance_after']=='-250.00'
    assert event['financial_activity_snapshots']==first


def test_special_credit_models_retain_full_report_fallback():
    state=fixture()
    event=reviewed(state,1,'card_terminal')
    state['engine']['obligations']['special']={'id':'special','party_type':'store_driver','party_id':'driver',
        'currency':'SAR','kind':'supplier','confirmed':'10.00','direction':'payable','business_date':'2026-11-01'}
    assert activity._driver_review_projection(state,[activity._pos_row(event['after'])]) is None
