"""Allocation provenance may reuse an original cash seal, never rewrite it."""
from copy import deepcopy
from decimal import Decimal

import pytest

import operational_financial_activity as activity
from operational_balance_inventory import save_purchase
from operational_balance_service import create_movement
from operational_balance_store import audit, digest, read
from operational_inventory_payment_allocation import allocate
from test_operational_balance_integration import NOW, movement, run
from test_operational_balance_inventory import command as purchase_command
from test_operational_inventory_payment_allocation import OWNER, command, party, pure_state, setup


EVIDENCE_FIELDS = (
    'account_balance_after', 'account_balance_status', 'counterparty_balance_after',
    'counterparty_balance_status', 'balance_as_of', 'recorded_at', 'activity_at',
    'employee_id', 'account_id', 'amount', 'currency',
)


@pytest.fixture(autouse=True)
def fixed_capture_clock(monkeypatch):
    monkeypatch.setattr(activity, 'now', lambda: NOW)


def evidence(state):
    rows, _ = activity.operational_rows(state)
    activity.attach_snapshots(state, rows)
    return {row['id']: {key: row[key] for key in EVIDENCE_FIELDS} for row in rows}


def sealed_state(*, account=True):
    state = pure_state(account=account)
    state['movements'][0]['actor_id'] = 'owner'
    audit(state, 'owner', 'movement_saved', None, state['movements'][0], '', 'mezan2', NOW)
    return state


def append_allocation(state, amount='50.00', *, stamp=NOW):
    movement = state['movements'][0]
    before = deepcopy(movement)
    used = sum((Decimal(row['amount']) for row in movement['allocations']), Decimal(0))
    total = used + Decimal(amount)
    movement['allocations'] = [{'obligation_id': 'invoice', 'amount': format(total, '.2f')}]
    result = {'id': 'allocation-' + str(len(state['audit'])), 'movement_id': movement['id'],
        'invoice_id': 'purchase', 'allocated_amount': amount,
        'available': format(Decimal(movement['amount']) - total, '.2f'),
        'financial_effect': 'allocation_only', 'cash_effect': '0.00', 'supplier_balance_effect': '0.00',
        'invoice': {'id': 'purchase', 'supplier_id': 'supplier', 'currency': 'SAR', 'obligation_id': 'invoice'}}
    audit(state, 'owner', activity.ALLOCATION_ACTION, {'movement': before},
          {'movement': deepcopy(movement), 'allocation': result}, '', 'inventory_purchase', stamp)
    return state['audit'][-1]


def reseal_proof(event):
    """An attacker recomputing a checksum still cannot bypass domain checks."""
    proof = event[activity.ALLOCATION_LINEAGE_FIELD]
    proof['event_hash'] = digest({key: event[key] for key in
        ('id', 'actor_id', 'at', 'action', 'before', 'after', 'source', 'reason')})
    proof['seal'] = digest({key: value for key, value in proof.items() if key != 'seal'})


@pytest.mark.parametrize('account', [True, False])
def test_allocation_keeps_original_sealed_cash_and_counterparty_balances(account):
    state = sealed_state(account=account)
    original_event, original_evidence = deepcopy(state['audit'][0]), evidence(state)
    assert original_evidence['movement:old-payment']['account_balance_after'] == '930.00'
    assert original_evidence['movement:old-payment']['counterparty_balance_after'] == '-30.00'
    event = append_allocation(state, stamp='2026-10-20T15:00:00+00:00')
    assert activity.ALLOCATION_LINEAGE_FIELD in event
    assert 'financial_activity_snapshots' not in event
    assert state['audit'][0] == original_event
    assert evidence(state) == original_evidence
    # Later changes to debt/cash cannot be misrepresented as the old after-balance.
    state['engine']['obligations']['invoice']['confirmed'] = '900.00'
    state['openings']['bank-opening']['amount'] = '2000.00'
    assert evidence(state) == original_evidence


def test_multiple_allocations_chain_to_one_unchanged_original_snapshot():
    state = sealed_state()
    original, expected = deepcopy(state['audit'][0]), evidence(state)
    first = deepcopy(append_allocation(state))
    second = append_allocation(state, '20.00')
    proof = second[activity.ALLOCATION_LINEAGE_FIELD]
    assert proof['previous_audit_id'] == first['id']
    assert proof['previous_seal'] == first[activity.ALLOCATION_LINEAGE_FIELD]['seal']
    assert proof['origin_audit_id'] == original['id']
    assert proof['origin_snapshot_seal'] == original['financial_activity_snapshots'][0]['seal']
    assert evidence(state) == expected
    assert state['audit'][0] == original and state['audit'][1] == first


@pytest.mark.parametrize('tamper', [
    'missing_proof', 'bad_seal', 'other_owner', 'other_previous_audit', 'other_origin_seal',
    'other_after_hash', 'other_supplier', 'nonzero_cash_effect', 'wrong_delta',
    'changed_current_amount', 'changed_current_source', 'missing_original_snapshot',
    'malformed_before',
])
def test_tampered_or_missing_allocation_lineage_cannot_reuse_a_cash_snapshot(tamper):
    state = sealed_state()
    event = append_allocation(state)
    proof = event[activity.ALLOCATION_LINEAGE_FIELD]
    if tamper == 'missing_proof':
        del event[activity.ALLOCATION_LINEAGE_FIELD]
    elif tamper == 'bad_seal':
        proof['seal'] = '0' * 64
    elif tamper == 'other_owner':
        proof['owner_id'] = 'foreign'
        reseal_proof(event)
    elif tamper == 'other_previous_audit':
        proof['previous_audit_id'] = 'unrelated'
        reseal_proof(event)
    elif tamper == 'other_origin_seal':
        proof['origin_snapshot_seal'] = '0' * 64
        reseal_proof(event)
    elif tamper == 'other_after_hash':
        proof['after_movement_hash'] = '0' * 64
        reseal_proof(event)
    elif tamper == 'other_supplier':
        event['after']['allocation']['invoice']['supplier_id'] = 'foreign'
        reseal_proof(event)
    elif tamper == 'nonzero_cash_effect':
        event['after']['allocation']['cash_effect'] = '5.00'
        reseal_proof(event)
    elif tamper == 'wrong_delta':
        event['after']['allocation']['allocated_amount'] = '49.00'
        reseal_proof(event)
    elif tamper == 'changed_current_amount':
        state['movements'][0]['amount'] = '75.00'
    elif tamper == 'changed_current_source':
        state['movements'][0]['bank_id'] = 'another-bank'
    elif tamper == 'missing_original_snapshot':
        del state['audit'][0]['financial_activity_snapshots']
    elif tamper == 'malformed_before':
        event['before'] = []
        reseal_proof(event)
    row = evidence(state)['movement:old-payment']
    assert row['account_balance_after'] is None and row['account_balance_status'] == 'unavailable'
    assert row['counterparty_balance_after'] is None and row['counterparty_balance_status'] == 'unavailable'


def test_recomputed_lineage_checksums_cannot_authorize_a_changed_cash_amount():
    state = sealed_state()
    event = append_allocation(state)
    changed = event['after']['movement']
    changed['amount'] = '75.00'
    state['movements'][0] = deepcopy(changed)
    proof = event[activity.ALLOCATION_LINEAGE_FIELD]
    proof['after_movement_hash'] = digest(changed)
    proof['after_basis'] = activity._movement_rows(changed, {})[0]['_basis']
    reseal_proof(event)
    assert evidence(state)['movement:old-payment']['account_balance_after'] is None


@pytest.mark.parametrize('tamper', ['missing_link', 'reordered', 'forked', 'restored_old_endpoint'])
def test_allocation_chain_must_be_complete_ordered_and_match_current_movement(tamper):
    state = sealed_state()
    first = deepcopy(append_allocation(state))
    append_allocation(state, '20.00')
    if tamper == 'missing_link':
        state['audit'].pop(1)
    elif tamper == 'reordered':
        state['audit'][1], state['audit'][2] = state['audit'][2], state['audit'][1]
    elif tamper == 'forked':
        state['audit'].append(first)
    elif tamper == 'restored_old_endpoint':
        state['movements'][0] = deepcopy(state['audit'][0]['after'])
    assert evidence(state)['movement:old-payment']['account_balance_after'] is None


def test_legacy_unsealed_history_is_not_backfilled_by_new_allocation():
    state = pure_state()
    assert evidence(state)['movement:old-payment']['account_balance_after'] is None
    event = append_allocation(state)
    assert activity.ALLOCATION_LINEAGE_FIELD not in event
    assert evidence(state)['movement:old-payment']['account_balance_after'] is None


def test_linked_payment_cannot_gain_lineage_from_an_allocation_shaped_audit():
    state = pure_state()
    state['movements'][0]['native_payment_operation_id'] = 'existing-native-payment'
    audit(state, 'owner', 'movement_saved', None, state['movements'][0], '', 'mezan2', NOW)
    event = append_allocation(state)
    assert activity.ALLOCATION_LINEAGE_FIELD not in event
    assert evidence(state)['movement:old-payment']['account_balance_after'] is None


def test_native_original_payment_replay_after_allocation_keeps_original_cash_result():
    async def scenario(db):
        invoice, payment = await setup(db)
        original_state = await read(db, 'owner')
        original_evidence = evidence(original_state)
        original_cash_audit = next(row for row in original_state['audit'] if row['action'] == 'movement_saved')
        await allocate(db, OWNER, invoice['id'], command(payment['id']), clock=NOW)
        allocated = await read(db, 'owner')
        assert evidence(allocated) == original_evidence
        assert next(row for row in allocated['audit'] if row['action'] == 'movement_saved') == original_cash_audit
        assert activity.ALLOCATION_LINEAGE_FIELD in next(row for row in allocated['audit'] if row['action'] == activity.ALLOCATION_ACTION)
        replayed = await create_movement(db, 'owner', 'owner', {**movement(), 'amount': '70.00'}, on_account=True, clock=NOW)
        assert replayed == payment and replayed['allocations'] == []
        after = await read(db, 'owner')
        assert after == allocated
        assert len(after['movements']) == 1
        assert after['movements'][0]['allocations'] == [{'obligation_id': invoice['obligation_id'], 'amount': '50.00'}]
    run(scenario)


def test_native_later_invoice_allocation_preserves_cash_history_in_real_feed():
    async def scenario(db):
        _, payment = await setup(db)
        original = evidence(await read(db, 'owner'))
        later = await save_purchase(db, 'owner', 'owner', purchase_command(actor='owner',
            request_id='later-invoice-for-allocation', invoice_number='LATER',
            lines=[{'kind': 'product', 'item_id': 'product', 'quantity': 5, 'unit_price': '20', 'tax': '0'}]), clock=NOW)
        await allocate(db, OWNER, later['id'], command(payment['id']), clock=NOW)
        await allocate(db, OWNER, later['id'], command(payment['id'], request_id='remaining-allocation', amount='20.00'), clock=NOW)
        state = await read(db, 'owner')
        assert party(state, 'supplier')['outstanding_payable'] == '130.00'
        assert evidence(state) == original
        feed = await activity.daily_financial_movements(db, owner='owner', actor={'id': 'owner', 'role': 'owner'},
            from_date=NOW[:10], to_date=NOW[:10])
        assert feed['total'] == 1
        assert feed['items'][0]['account_balance_after'] == '930.00'
        assert feed['items'][0]['counterparty_balance_after'] == '-30.00'
        assert feed['items'][0]['recorded_at'] == NOW
    run(scenario)
