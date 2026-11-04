"""A committed, versioned payment does not wait for fulfillment component reconciliation."""
import pytest

from operational_provider_transaction_evidence import normalize_operational_payment
from salla_payment_collection import order_page_source_current

STAMP = '2026-11-02T08:00:00+00:00'


def recognize(*, document=None, watermark=None, canonical=None):
    raw = {'id': 71, 'reference_id': 825, 'status': 'under_review', 'currency': 'SAR',
           'payment_method': 'tamara_installment', 'total': {'amount': '80.00', 'currency': 'SAR'},
           'updated_at': {'date': '2026-11-02 11:00:00', 'timezone': 'Asia/Riyadh'},
           'payment_actions': {'remaining_action': {'has_remaining_amount': False, 'paid_amount': '80.00'}},
           'payment_methods': [{'payment_method': 'tamara_installment', 'amount': '80.00',
                                'transaction_reference': 'native-reference'}], **(canonical or {})}
    source = {'user_id': 'owner', 'order_id': '71', 'order_number': '825', 'g47_salla_snapshot': {
        'component_pending': True, 'source_updated_at': STAMP, **(watermark or {})}, **(document or {})}
    result = normalize_operational_payment(raw, 'SAR', '80.00', '2026-11-01T01:00:00Z',
        '2026-11-03T09:00:00Z', owner='owner', order_number='825', provider_order_id='71', rows=[], source=source)
    return result, source, raw


@pytest.mark.parametrize('version', [STAMP, '2026-11-02T07:59:59Z'])
def test_referenced_paid_order_is_current_while_fulfillment_callback_is_pending(version):
    (payment, issues), source, raw = recognize(watermark={'source_updated_at': version})
    assert not issues and payment['order_page_balance']['paid'] == '80.00'
    assert payment['order_page_balance']['source_updated_at'] == STAMP
    assert 'captures' not in payment
    assert not order_page_source_current(source, raw)  # Shipping/COD still require fulfillment evidence.


@pytest.mark.parametrize('changes', [
    {'document': {'user_id': 'other'}}, {'document': {'order_id': '999'}},
    {'document': {'order_number': '999'}}, {'canonical': {'reference_id': 999}},
    {'watermark': {'cancelled': True}}, {'document': {'order_status_slug': 'cancelled'}},
    {'watermark': {'requires_authoritative_refresh': True}}, {'watermark': {'source_updated_at': None}},
    {'watermark': {'source_updated_at': '2026-11-02T08:00:01Z'}},
    {'document': {'last_salla_direct_status_source_updated_at': '2026-11-02T08:00:01Z'}},
    {'canonical': {'updated_at': None}}, {'canonical': {'payment_methods': []}},
    {'canonical': {'payment_methods': [{'payment_method': 'tamara_installment', 'amount': '80.00'}]}},
])
def test_component_pending_never_weakens_owner_identity_version_or_native_proof(changes):
    (payment, problems), _, _ = recognize(**changes)
    assert payment is None and problems


def foreign_payment(*, order=100, paid='99.98', tender=None, changes=None, source_changes=None, rows=()):
    raw = {'id': 71, 'reference_id': 825, 'status': {'slug': 'in_progress'}, 'currency': 'KWD',
           'payment_method': 'credit_card', 'total': {'amount': order, 'currency': 'KWD'}, 'updated_at': STAMP,
           'payment_actions': {'remaining_action': {'has_remaining_amount': False, 'remaining_amount': None,
                                                  'paid_amount': {'amount': paid, 'currency': 'KWD'}},
                               'refund_action': {'paid_amount': {'amount': paid, 'currency': 'KWD'},
                                                 'refund_amount': {'amount': 0, 'currency': 'KWD'},
                                                 'refunded_transactions': []}},
           'payment_methods': [{'payment_method': 'credit_card', 'amount': tender or paid,
                                'transaction_reference': 'native-cc-reference'}]}
    if changes:
        changes(raw)
    return normalize_operational_payment(raw, 'KWD', order, '2026-11-01T01:00:00Z', '2026-11-03T09:00:00Z',
        owner='owner', order_number='825', provider_order_id='71', rows=rows,
        source={'user_id':'owner', 'order_number':'825', 'order_id':'71', **(source_changes or {})})


@pytest.mark.parametrize('amount', ['99.98', '99.99'])
def test_closed_foreign_tender_keeps_paid_amount_separate_from_mismatching_order_total(amount):
    from operational_provider_report_coverage import provider_customer_payments

    payment, issues = foreign_payment(paid=amount)
    assert payment is not None and not issues and not payment.get('captures')
    assert payment['gross'] == amount
    observation = payment['order_page_balance']
    assert (observation['paid'], observation['gross']) == (amount, '100.00')
    assert observation['remaining'] is None and 'captured_at' not in observation
    source = {'details':{'facts':{'provider_order_balance:71':observation}},'obligations':[],'issues':[]}
    for _ in range(2):
        pages, totals = provider_customer_payments(source)
        assert pages['KWD'][0]['remaining'] is None
        assert totals['KWD'] == [{'provider':'salla','name':'سلة','orders':1,'paid':amount}]


@pytest.mark.parametrize('kwargs', [
    {'paid':'30.00'}, {'tender':'99.96'}, {'changes':lambda r:r['payment_methods'][0].pop('transaction_reference')},
    {'changes':lambda r:r['payment_actions']['remaining_action'].update(has_remaining_amount=True)},
    {'changes':lambda r:r['payment_actions']['refund_action'].update(paid_amount={'amount':'99.98','currency':'SAR'})},
    {'changes':lambda r:r['payment_actions']['refund_action'].update(refund_amount={'amount':5,'currency':'KWD'})},
    {'changes':lambda r:r['payment_actions']['refund_action'].update(pending_refund_amount={'amount':5,'currency':'KWD'})},
    {'changes':lambda r:r.update(is_pending_payment=True)},
    {'changes':lambda r:r.update(payment_status='declined')},
    {'changes':lambda r:r.update(status={'slug':'restored'})},
    {'changes':lambda r:r.update(updated_at='2026-11-03T09:01:00Z')},
])
def test_unproved_tender_never_enters_customer_paid_detail(kwargs):
    payment, issues = foreign_payment(**kwargs)
    assert not payment or 'order_page_balance' not in payment
    assert not any(i.get('paid_tender_observation') for i in issues)


def test_foreign_observation_never_counts_a_frozen_different_provider_or_reference_owner_twice():
    from operational_provider_report_coverage import provider_customer_payments

    issue = {'code':'provider_closed_tender_total_mismatch', 'provider':'salla',
             'paid_tender_observation': {'order_id':'71', 'provider':'salla', 'currency':'KWD',
                 'reference':'native-cc-reference', 'paid':'99.98', 'gross':'100.00'}}
    observed = {**issue, 'scope':'provider_payment', 'source_id':'71'}
    frozen = {'order_id':'71', 'provider':'tamara', 'currency':'KWD',
              'paid':'99.98','gross':'99.98','remaining':'0.00'}
    projection = {'details':{'facts':{'provider_order_balance:71':frozen}}, 'obligations':[], 'issues':[observed]}
    assert provider_customer_payments(projection)[1]['KWD'] == [
        {'provider':'tamara','name':'تمارا','orders':1,'paid':'99.98'}]
    projection['details']['facts'] = {'capture:native-cc-reference':{'order_id':'different', 'provider':'salla', 'amount':'99.98'}}
    assert not provider_customer_payments(projection)[1]
