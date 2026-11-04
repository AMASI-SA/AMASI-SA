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
