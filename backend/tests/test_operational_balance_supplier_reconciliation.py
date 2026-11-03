"""Pure comparison checks; fixtures are synthetic and perform no DB writes."""
from copy import deepcopy
from decimal import Decimal
import pytest
from operational_supplier_reconciliation import compare_supplier_invoices

START = '2026-11-01T00:30:01+03:00'
AT = '2026-11-01T01:00:00+03:00'
NOW = '2026-11-02T02:00:00+03:00'


def fixture():
    original = {'owner_id': 'owner', 'status': 'active', 'started_at': START,
        'openings': {'supplier': {'party_id': 's', 'party_type': 'supplier', 'currency': 'SAR',
                                  'direction': 'against_us', 'amount': '100.00'}},
        'movements': [], 'engine': {'facts': {}, 'obligations': {}, 'as_of': AT}}
    proposed = deepcopy(original)
    invoice = {'user_id': 'owner', 'id': 'invoice', 'invoice_number': 'SI-SYNTHETIC',
               'supplier_id': 's', 'currency': 'SAR', 'approved_at': AT,
               'status': 'payable_posted', 'total_halalas': 8000,
               'lines': [{'piece_ids': ['p1', 'p2'], 'quantity': 2,
                          'product_price_authority': 'mezan_v2'}]}
    for n in (1, 2):
        rid = f'receipt:invoice:p{n}:product'
        key = f'supplier:old-order:p{n}:' + rid
        fact = {'order_id': 'old-order', 'item_id': f'p{n}', 'supplier_id': 's',
                'amount': '40.00', 'gross_amount': '40.00', 'net_amount': '40.00',
                'tax_amount': '0.00', 'accepted_at': AT}
        obligation = {'id': key, 'kind': 'supplier', 'party_type': 'supplier', 'party_id': 's',
            'currency': 'SAR', 'direction': 'payable', 'confirmed': '40.00', 'expected': '0.00',
            'gross_amount': '40.00', 'source_invoice_id': 'invoice', 'evidence_ids': [rid]}
        proposed['engine']['facts'][rid] = fact
        proposed['engine']['obligations'][key] = obligation
    return original, proposed, [invoice]


def call(states):
    a, b, invoices = states
    saved = deepcopy(states)
    result = compare_supplier_invoices(a, b, invoices, owner_id='owner', as_of=NOW)
    assert states == saved
    assert result['posting_allowed'] is False
    assert result['financial_correction_approved'] is False
    assert result['opening_coverage_verified'] is False
    return result


def copy_first(states):
    a, b, _ = states
    rid = next(iter(b['engine']['facts']))
    key = next(iter(b['engine']['obligations']))
    a['engine']['facts'][rid] = deepcopy(b['engine']['facts'][rid])
    a['engine']['obligations'][key] = deepcopy(b['engine']['obligations'][key])
    return rid, key


def test_new_invoice_is_a_proposal_not_a_financial_write():
    result = call(fixture())
    assert result['arithmetic_complete'] is True
    assert result['totals'] == {'invoice_gross': '80.00', 'difference': '80.00'}
    row = result['rows'][0]
    assert row['reason'] == 'missing_invoice'
    assert row['current']['confirmed'] == '0.00'
    assert row['proposed']['confirmed'] == '80.00'
    assert row['opening_coverage'] == 'requires_owner_review'


def test_partial_recording_preserves_allocations_and_excludes_opening():
    states = fixture(); rid, key = copy_first(states)
    a, b, _ = states
    movement = {'id': 'payment', 'amount': '25.00',
                'allocations': [{'obligation_id': key, 'amount': '25.00'}]}
    a['movements'] = [movement]; b['movements'] = deepcopy(a['movements'])
    row = call(states)['rows'][0]
    assert row['reason'] == 'partially_counted_invoice'
    assert row['current']['outstanding'] == '15.00'
    assert row['proposed']['outstanding'] == '55.00'
    assert row['difference'] == '40.00'
    assert row['current']['settled'] == row['proposed']['settled'] == '25.00'


def test_legacy_metadata_uses_exact_same_obligation_not_invoice_prefix():
    states = fixture(); _, key = copy_first(states)
    del states[0]['engine']['obligations'][key]['source_invoice_id']
    result = call(states)
    assert result['arithmetic_complete']
    assert result['rows'][0]['difference'] == '40.00'


def test_already_matched_does_not_propose_addition_again():
    _, b, inv = fixture()
    result = call((deepcopy(b), b, inv))
    assert result['rows'][0]['reason'] == 'already_matched'
    assert result['totals']['difference'] == '0.00'


def test_existing_supplier_return_and_excess_payment_are_not_lost():
    states = fixture(); rid, key = copy_first(states)
    for s in states[:2]:
        s['engine']['facts']['return:r'] = {'receipt_id': rid, 'amount': '30.00'}
        s['engine']['obligations'][key]['confirmed'] = '10.00'
        s['movements'] = [{'id': 'm', 'allocations': [{'obligation_id': key, 'amount': '25.00'}]}]
    row = call(states)['rows'][0]
    assert row['current']['confirmed'] == '10.00'
    assert row['proposed']['confirmed'] == '50.00'
    assert row['current']['overpayment'] == row['proposed']['overpayment'] == '15.00'
    assert row['proposed']['outstanding'] == '40.00'
    assert row['difference'] == '40.00'


@pytest.mark.parametrize('field', ['openings', 'movements'])
def test_changed_financial_history_is_rejected(field):
    states = fixture()
    states[1][field] = {} if field == 'openings' else [{'id': 'unexpected'}]
    with pytest.raises(ValueError, match=field + '_changed'):
        call(states)


def test_changed_frozen_receipt_is_rejected():
    states = fixture(); rid, _ = copy_first(states)
    states[1]['engine']['facts'][rid]['amount'] = '45.00'
    with pytest.raises(ValueError, match='confirmed_facts_changed'):
        call(states)


@pytest.mark.parametrize('side', [0, 1])
def test_cross_owner_snapshot_is_rejected(side):
    states = fixture(); states[side]['owner_id'] = 'someone-else'
    with pytest.raises(ValueError, match='owner_mismatch'):
        call(states)


@pytest.mark.parametrize('status', ['draft', 'frozen', 'accounting_active'])
def test_inactive_system_cannot_be_presented_as_active(status):
    states = fixture(); states[0]['status'] = status
    with pytest.raises(ValueError, match='not_active'):
        call(states)


@pytest.mark.parametrize('case', ['cutoff', 'naive', 'stale'])
def test_snapshot_dates_are_explicit(case):
    states = fixture()
    if case == 'cutoff': states[1]['started_at'] = AT
    elif case == 'naive': states[0]['started_at'] = '2026-11-01'
    else: states[1]['engine']['as_of'] = '2026-11-03T00:00:00+03:00'
    with pytest.raises(ValueError): call(states)


@pytest.mark.parametrize('bad', [True, None, 'NaN', 'Infinity', -1, '100.5', 100.0, '100000000000000'])
def test_invalid_invoice_header_never_becomes_zero(bad):
    states = fixture(); states[2][0]['total_halalas'] = bad
    result = call(states)
    assert not result['arithmetic_complete']
    assert result['totals'] is None


@pytest.mark.parametrize('case', ['header', 'missing_piece', 'wrong_piece', 'wrong_party', 'wrong_currency', 'approval', 'tax', 'quantity'])
def test_incomplete_or_conflicting_invoice_is_not_a_complete_delta(case):
    states = fixture(); a, b, invoices = states
    first = next(iter(b['engine']['obligations']))
    rid = b['engine']['obligations'][first]['evidence_ids'][0]
    if case == 'header': invoices[0]['total_halalas'] = 9000
    elif case == 'missing_piece': b['engine']['obligations'].pop(first)
    elif case == 'wrong_piece': invoices[0]['lines'][0]['piece_ids'][0] = 'foreign'
    elif case == 'wrong_party': invoices[0]['supplier_id'] = 'other'
    elif case == 'wrong_currency': invoices[0]['currency'] = 'QAR'
    elif case == 'approval': b['engine']['facts'][rid]['accepted_at'] = START
    elif case == 'tax': b['engine']['facts'][rid]['tax_amount'] = '1.00'
    else: invoices[0]['lines'][0]['quantity'] = 3
    result = call(states)
    assert not result['arithmetic_complete']
    assert result['totals'] is None


@pytest.mark.parametrize('case', ['invoice', 'component', 'movement', 'allocation'])
def test_duplicates_are_never_summed_as_new_money(case):
    states = fixture(); a, b, invoices = states
    if case == 'invoice': invoices.append(deepcopy(invoices[0]))
    elif case == 'component': invoices[0]['lines'][0]['piece_ids'].append('p1')
    else:
        key = next(iter(b['engine']['obligations']))
        m = {'id': 'm', 'allocations': [{'obligation_id': key, 'amount': '1.00'}]}
        a['movements'] = [m, deepcopy(m)] if case == 'movement' else [m]
        if case == 'allocation': m['allocations'].append(deepcopy(m['allocations'][0]))
        b['movements'] = deepcopy(a['movements'])
    if case in {'movement', 'allocation'}:
        with pytest.raises(ValueError, match='duplicate'): call(states)
    else:
        result = call(states); assert result['totals'] is None


@pytest.mark.parametrize('patch', [{'approved_at': None}, {'status': 'draft'}, {'experiment_mode': True},
    {'approved_at': '2026-10-31T23:00:00+03:00'}, {'approved_at': '2026-11-03T00:00:00+03:00'}])
def test_ineligible_invoice_does_not_authorize_proposed_debt(patch):
    states = fixture(); states[2][0].update(patch)
    result = call(states)
    assert not result['arithmetic_complete']
    assert any(i['code'] == 'supplier_review_change_without_eligible_invoice' for i in result['issues'])


def test_other_financial_changes_are_disclosed_not_hidden_in_supplier_preview():
    states = fixture()
    states[1]['engine']['obligations']['provider:other'] = {
        'kind': 'provider', 'confirmed': '100', 'party_id': 'p', 'currency': 'SAR'}
    result = call(states)
    assert result['totals'] is None
    assert any(i['code'] == 'supplier_review_unrelated_financial_change' for i in result['issues'])


def test_shipping_warning_alone_does_not_invalidate_complete_supplier_invoice():
    states = fixture()
    states[1]['engine']['issues'] = [{'code': 'shipping_identity_or_rate_incomplete', 'source_id': 'unrelated'}]
    assert call(states)['arithmetic_complete'] is True


def test_unexplained_disappearance_is_not_a_negative_correction():
    _, b, invoices = fixture(); a = deepcopy(b)
    b['engine']['obligations'].clear()
    result = call((a, b, invoices))
    assert result['totals'] is None
    assert any(i['code'] == 'supplier_review_existing_invoice_disappeared' for i in result['issues'])


def test_private_foreign_invoice_is_rejected_without_copying_its_data():
    states = fixture(); states[2].append({'user_id': 'other', 'id': 'private-invoice'})
    result = call(states)
    assert result['totals'] is None
    assert 'private-invoice' not in str(result)


def test_cutoff_day_approval_is_inclusive():
    states = fixture(); states[2][0]['approved_at'] = START
    for f in states[1]['engine']['facts'].values(): f['accepted_at'] = START
    assert call(states)['totals']['difference'] == '80.00'
