"""The noncash writer must work on logical maps and preserve compact history."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

import operational_driver_payment_review as service
from operational_balance_archive import (
    ISSUES, MARKER, SNAPSHOT, SNAPSHOT_MARK, compact_financial_snapshot, compact_issues,
    content_key, manifest, unpack_financial_snapshot,
)


def packed_state():
    event = {'id': 'immutable-history'}
    page = content_key('owner', 0, [event])
    state = {'_id': 'owner', 'owner_id': 'owner', 'revision': 4,
        'status': 'active', 'started_at': '2026-10-05T09:00:00+00:00',
        'openings': {'bank': {'amount': '1000.00'}}, 'movements': [], 'audit': [],
        MARKER: manifest(1, [{'id': page, 'count': 1}]),
        'engine': {'audit': {'tail': []}, 'as_of': '2026-10-07T12:00:00+00:00',
            'issues': [{'code': 'known-source-gap', 'message': 'retained' * 100}],
            'facts': {'saved': {'amount': '20.00', 'evidence': 'unchanged' * 1000}},
            'obligations': {'shipping:order': {'id': 'shipping:order', 'confirmed': '20.00'}}}}
    compact = compact_issues(state, 100_000)
    return compact_financial_snapshot(compact, 10_000), state


@pytest.mark.parametrize('method', ['bank_transfer', 'card_terminal'])
def test_review_repacking_keeps_every_fact_and_existing_archive_packet(method):
    original, expected = packed_state()
    pristine = deepcopy(original)
    state = unpack_financial_snapshot(original)
    assert state['engine']['obligations'] != SNAPSHOT_MARK
    if method == 'bank_transfer':
        state['movements'] = [{'id': 'approved-bank-transfer', 'amount': '200.00'}]
    else:
        state['engine']['obligations']['pos:approved'] = {'id': 'pos:approved', 'confirmed': '200.00'}
    encoded = service._encode_review_state(original, state)
    decoded = unpack_financial_snapshot(encoded)
    assert SNAPSHOT in encoded and encoded['engine']['obligations'] == SNAPSHOT_MARK
    assert decoded['engine']['facts'] == expected['engine']['facts']
    assert decoded['engine']['obligations']['shipping:order'] == expected['engine']['obligations']['shipping:order']
    assert encoded[ISSUES] == pristine[ISSUES] and encoded[MARKER] == pristine[MARKER]
    assert encoded['engine']['audit'] == pristine['engine']['audit']
    assert original == pristine


def test_an_unencodable_review_stays_a_failure_without_dropping_history(monkeypatch):
    original, _ = packed_state()
    state = unpack_financial_snapshot(original)
    state['movements'] = [{'id': 'too-large', 'amount': '200.00', 'reference': 'large' * 5000}]
    before = deepcopy(state)
    monkeypatch.setattr(service, 'MAX_STATE_BYTES', 2_000)
    with pytest.raises(HTTPException) as error:
        service._encode_review_state(original, state)
    assert error.value.detail['code'] == 'operational_capacity_requires_archive'
    assert state == before
