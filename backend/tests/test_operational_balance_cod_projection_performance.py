"""COD report semantics and bounded history passes; no database or live state."""
from copy import deepcopy

from operational_balance_service import report
from operational_balance_store import initial


def cod_fixture(movements=5000, claims=100):
    state = initial('synthetic-report-owner')
    state.update(status='active', started_at='2026-11-01T00:00:00+00:00')
    state['engine'] = {'as_of': '2026-11-08T12:00:00+00:00', 'obligations': {}, 'cod_reports': {}}
    state['openings'] = {'bank': {'id': 'opening-bank', 'party_type': 'bank', 'party_id': 'bank',
        'currency': 'SAR', 'name': 'Synthetic bank', 'amount': '0.00', 'direction': 'for_us'}}
    for i in range(claims):
        identity = f'cod:{i}'
        state['engine']['obligations'][identity] = {'id': identity, 'kind': 'cod',
            'party_type': 'store_driver', 'party_id': 'driver', 'currency': 'SAR',
            'confirmed': '100.00', 'expected': '0.00', 'direction': 'receivable',
            'business_date': '2026-11-08'}
        state['engine']['cod_reports'][identity] = {'order_id': str(i), 'currency': 'SAR'}
    for i in range(movements):
        state['movements'].append({'id': f'm:{i}', 'kind': 'payment', 'direction': 'incoming',
            'amount': '1.00', 'currency': 'SAR', 'bank_kind': 'bank', 'bank_id': 'bank',
            'party_type': 'store_driver', 'party_id': 'driver',
            'allocations': [{'obligation_id': f'cod:{i % claims}', 'amount': '1.00'}]})
    return state


class CountedMovements(list):
    def __init__(self, rows, counter):
        super().__init__(rows)
        self.counter = counter

    def __iter__(self):
        self.counter['passes'] += 1
        return super().__iter__()

    def __deepcopy__(self, memo):
        return CountedMovements(deepcopy(list.__getitem__(self, slice(None)), memo), self.counter)


def passes(claims):
    state = cod_fixture(movements=40, claims=claims)
    counter = {'passes': 0}
    state['movements'] = CountedMovements(state['movements'], counter)
    report(state)
    return counter['passes']


def test_cod_projection_does_not_scan_all_movements_per_claim():
    assert passes(100) <= passes(10) + 1


def test_cod_projection_keeps_incoming_only_duplicate_allocations_and_unknown_claim():
    state = cod_fixture(movements=0, claims=2)
    prototype = cod_fixture(movements=1, claims=2)['movements'][0]
    state['movements'] = [
        {**prototype, 'id': 'incoming', 'amount': '7.00', 'allocations': [
            {'obligation_id': 'cod:0', 'amount': '2.00'},
            {'obligation_id': 'cod:0', 'amount': '3.00'},
            {'obligation_id': 'not-a-cod-report', 'amount': '2.00'}]},
        {**prototype, 'id': 'outgoing', 'direction': 'outgoing', 'amount': '4.00',
         'allocations': [{'obligation_id': 'cod:0', 'amount': '4.00'}]},
    ]
    before = deepcopy(state)
    output = report(state)
    assert output['details']['cod_reports']['cod:0']['settled'] == '5.00'
    assert output['details']['cod_reports']['cod:0']['outstanding'] == '95.00'
    assert output['details']['cod_reports']['cod:1']['settled'] == '0.00'
    assert set(output['details']['cod_reports']) == {'cod:0', 'cod:1'}
    assert next(row for row in output['parties'] if row['party_type'] == 'bank')['actual'] == '3.00'
    assert state == before


def test_cod_projection_empty_and_overpaid_claim_remain_defined():
    state = cod_fixture(movements=0, claims=1)
    state['engine']['cod_reports']['cod:missing'] = {'currency': 'SAR'}
    prototype = cod_fixture(movements=1, claims=1)['movements'][0]
    state['movements'] = [{**prototype, 'amount': '101.00',
                          'allocations': [{'obligation_id': 'cod:0', 'amount': '101.00'}]}]
    output = report(state)['details']['cod_reports']
    assert output['cod:0']['settled'] == '101.00'
    assert output['cod:0']['outstanding'] == '0.00'
    assert output['cod:missing']['confirmed_custody'] == '0.00'
    assert output['cod:missing']['settled'] == '0.00'
    state['engine']['cod_reports'] = {}
    assert report(state)['details']['cod_reports'] == {}
