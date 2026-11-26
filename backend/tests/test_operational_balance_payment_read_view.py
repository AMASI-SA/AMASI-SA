"""Pure read-only payment preparation preserves the existing financial result."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from operational_payment_read_view import settled_payment_read_view
from operational_provider_transaction_evidence import normalize_operational_payment
from salla_payment_collection import settled_payment_snapshot


STAMP = '2026-11-02T08:00:00+00:00'


def payload():
    return {
        'id': 71, 'reference_id': 825, 'status': 'under_review', 'currency': 'SAR',
        'payment_method': 'tamara_installment',
        'total': {'amount': '80.00', 'currency': 'SAR'}, 'updated_at': STAMP,
        'paid_amount': '12.00', 'remaining_amount': '68.00',
        'has_remaining_amount': True, 'checkout_url': 'old-checkout',
        'payment_actions': {'remaining_action': {
            'has_remaining_amount': False, 'paid_amount': '80.00',
            'remaining_amount': '68.00', 'checkout_url': 'old-checkout'}},
        'payment_methods': [{'payment_method': 'tamara_installment', 'amount': '80.00',
                             'transaction_reference': 'native-reference'}],
        'items': [{'id': 'line', 'options': [{'id': 'option', 'values': ['value']}]}],
        'customer': {'name': 'synthetic', 'metadata': {'tags': ['example']}},
        'shipping': {'company': 'synthetic', 'metadata': {'labels': ['example']}},
        'shipments': [{'id': 'shipment', 'metadata': {'tracking': ['example']}}],
    }


def recognize(raw, *, read_only=False, rows=None, source_changes=None):
    source = {'user_id': 'owner', 'order_id': '71', 'order_number': '825',
              'g47_salla_snapshot': {'component_pending': True, 'source_updated_at': STAMP},
              **(source_changes or {})}
    return normalize_operational_payment(
        raw, 'SAR', '80.00', '2026-11-01T01:00:00Z', '2026-11-03T09:00:00Z',
        owner='owner', order_number='825', provider_order_id='71',
        rows=[] if rows is None else rows, source=source, read_only=read_only,
    )


def test_read_view_keeps_all_fields_and_copies_every_financial_branch():
    raw = payload()
    before = deepcopy(raw)
    view = settled_payment_read_view(raw)
    assert view == settled_payment_snapshot(raw)
    assert set(view) == set(settled_payment_snapshot(raw))
    for name in ('items', 'customer', 'shipping', 'shipments'):
        assert view[name] is raw[name]
    assert view['payment_actions'] is not raw['payment_actions']
    assert view['payment_actions']['remaining_action'] is not raw['payment_actions']['remaining_action']
    assert view['payment_methods'] is not raw['payment_methods']
    assert view['total'] is not raw['total']
    assert raw == before


def test_public_snapshot_keeps_its_complete_independent_copy_contract():
    raw = payload()
    snapshot = settled_payment_snapshot(raw)
    for name in ('items', 'customer', 'shipping', 'shipments', 'payment_actions', 'payment_methods'):
        assert snapshot[name] is not raw[name]
    snapshot['items'][0]['options'][0]['values'].append('new')
    snapshot['customer']['metadata']['tags'].append('new')
    assert raw['items'][0]['options'][0]['values'] == ['value']
    assert raw['customer']['metadata']['tags'] == ['example']


@pytest.mark.parametrize('change', [
    lambda r: None,
    lambda r: r['payment_actions']['remaining_action'].update(remaining_amount=0),
    lambda r: r['payment_actions']['remaining_action'].update(paid_amount={'amount': '80.00', 'currency': 'SAR'}),
    lambda r: r['payment_actions']['remaining_action'].update(has_remaining_amount=True, paid_amount='12.00'),
    lambda r: r.update(payment={'method': 'tamara_installment', 'paid_amount': '80.00', 'remaining_amount': 0}),
    lambda r: r.update(payment_status='declined'),
    lambda r: r.update(is_pending_payment=True),
    lambda r: r.update(status={'slug': 'cancelled'}),
    lambda r: r['payment_actions'].update(refund_action={'refund_amount': {'amount': '5.00', 'currency': 'SAR'}}),
    lambda r: r['payment_actions']['remaining_action'].update(paid_amount={'amount': '80.00', 'currency': 'USD'}),
    lambda r: r.update(payment_methods=[{'payment_method': 'tamara_installment', 'amount': '12.00', 'transaction_reference': 'other'}]),
    lambda r: r.update(updated_at='2026-11-03T09:01:00Z'),
    lambda r: r.update(payment_actions=[]),
    lambda r: r['payment_actions'].update(remaining_action=[]),
])
def test_full_payment_result_and_input_are_identical_for_valid_and_conflicting_evidence(change):
    raw = payload()
    change(raw)
    before = deepcopy(raw)
    expected = recognize(raw)
    actual = recognize(raw, read_only=True)
    assert actual == expected
    assert raw == before


@pytest.mark.parametrize('source_changes', [
    {'user_id': 'other'}, {'order_id': 'other'}, {'order_number': 'other'},
    {'g47_salla_snapshot': {'cancelled': True}},
    {'g47_salla_snapshot': {'source_updated_at': '2026-11-02T08:00:01Z'}},
    {'last_salla_direct_status_source_updated_at': '2026-11-02T08:00:01Z'},
])
def test_view_does_not_change_owner_identity_or_freshness_admission(source_changes):
    raw = payload()
    expected = recognize(raw, source_changes=source_changes)
    assert recognize(raw, read_only=True, source_changes=source_changes) == expected
    assert expected[0] is None


@pytest.mark.parametrize('kind', ['payment', 'refund', 'borrowed_root'])
def test_shared_financial_containers_keep_one_independent_alias(kind):
    raw = payload()
    remaining = raw['payment_actions']['remaining_action']
    remaining['remaining_amount'] = 0
    if kind == 'payment':
        raw['payment'] = remaining
        other = 'payment'
    elif kind == 'refund':
        raw['payment_actions']['refund_action'] = remaining
        other = None
    else:
        raw['customer'] = remaining
        other = 'customer'
    expected = settled_payment_snapshot(raw)
    view = settled_payment_read_view(raw)
    copied = view['payment_actions']['remaining_action']
    assert copied is not remaining
    assert view == expected
    assert (view[other] if other else view['payment_actions']['refund_action']) is copied
    assert recognize(raw, read_only=True) == recognize(raw)


def test_root_and_financial_cycles_remain_independent_without_copying_opaque_items():
    raw = payload()
    raw['financial_cycle'] = raw
    raw['payment_actions']['self'] = raw['payment_actions']
    view = settled_payment_read_view(raw)
    assert view['financial_cycle'] is view
    assert view['payment_actions']['self'] is view['payment_actions']
    assert view['payment_actions'] is not raw['payment_actions']
    assert raw['financial_cycle'] is raw
    assert recognize(raw, read_only=True) == recognize(raw)


class CustomDict(dict):
    pass


@pytest.mark.parametrize('location', ['root', 'actions', 'remaining', 'payment', 'items'])
def test_custom_container_shapes_fall_back_to_full_independent_copy(location):
    raw = payload()
    if location == 'root':
        raw = CustomDict(raw)
    elif location == 'actions':
        raw['payment_actions'] = CustomDict(raw['payment_actions'])
    elif location == 'remaining':
        raw['payment_actions']['remaining_action'] = CustomDict(raw['payment_actions']['remaining_action'])
    elif location == 'payment':
        raw['payment'] = CustomDict(method='tamara_installment')
    else:
        raw['items'] = CustomDict(item=raw['items'][0])
    view = settled_payment_read_view(raw)
    assert view == settled_payment_snapshot(raw)
    assert view['items'] is not raw['items']
    assert type(view) is type(raw)


@pytest.mark.parametrize('value', [Decimal('80.00'), datetime(2026, 11, 2, tzinfo=timezone.utc), ('tuple',)])
def test_non_json_financial_values_keep_the_original_fallback(value):
    raw = payload()
    raw['financial_extension'] = value
    view = settled_payment_read_view(raw)
    assert view == settled_payment_snapshot(raw)
    assert view['items'] is not raw['items']


@pytest.mark.parametrize('value', [None, '', '0', '-1', 'NaN', 'Infinity', True, {}, []])
def test_ineligible_clear_keeps_the_exact_original_input(value):
    raw = payload()
    raw['payment_actions']['remaining_action']['paid_amount'] = value
    assert settled_payment_read_view(raw) is raw
    assert settled_payment_snapshot(raw) is raw


def test_normalized_output_cannot_mutate_borrowed_source_evidence():
    raw = payload()
    before = deepcopy(raw)
    payment, issues = recognize(raw, read_only=True)
    assert payment and not issues
    payment['order_page_balance']['paid'] = '0.00'
    payment['new'] = ['changed']
    assert raw == before


def test_large_unrelated_item_payload_is_not_deepcopied_by_the_read_only_normalizer():
    class ItemPayload:
        copies = 0

        def __deepcopy__(self, memo):
            type(self).copies += 1
            return ItemPayload()

    raw = payload()
    raw['items'] = [{'opaque_payload': ItemPayload()} for _ in range(256)]
    expected = recognize(raw)
    assert ItemPayload.copies == 256
    ItemPayload.copies = 0
    assert recognize(raw, read_only=True) == expected
    assert ItemPayload.copies == 0
