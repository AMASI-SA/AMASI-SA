"""Allocation is invoice attribution of existing money, never another payment."""
import asyncio
from copy import deepcopy
from decimal import Decimal

import pytest
from fastapi import HTTPException

from operational_balance_inventory import save_purchase
from operational_balance_service import create_movement, freeze, report
from operational_balance_store import digest, initial, mutate, read
from operational_inventory_payment_allocation import _candidates, allocate, candidates
from operational_supplier_adjustments import save_adjustment
from test_operational_balance_integration import NOW, movement, run
from test_operational_balance_inventory import command as purchase_command, seed
from test_operational_balance_app_permissions import client


OWNER = {'id': 'owner'}


def command(movement_id, **changes):
    return {'request_id': 'allocate-existing-1', 'expected_session_scope': digest(['owner', 'owner']),
            'movement_id': movement_id, 'amount': '50.00', 'business_date': NOW[:10], **changes}


def pure_state(*, account=True, amount='70.00'):
    state = initial('owner')
    state.update(status='active', started_at='2026-10-05T09:00:00+00:00')
    state['openings']['bank-opening'] = {'id': 'bank-opening', 'party_type': 'bank', 'party_id': 'bank',
        'name': 'Bank', 'currency': 'SAR', 'direction': 'for_us', 'amount': '1000.00'}
    state['engine']['obligations'] = {'invoice': {'id': 'invoice', 'kind': 'supplier', 'party_type': 'supplier',
        'party_id': 'supplier', 'name': 'Supplier', 'currency': 'SAR', 'direction': 'payable',
        'expected': '0.00', 'confirmed': '100.00'}}
    state['movements'] = [{'id': 'old-payment', 'party_type': 'supplier', 'party_id': 'supplier',
        'name': 'Supplier', 'currency': 'SAR', 'direction': 'outgoing', 'kind': 'payment',
        'bank_id': 'bank', 'bank_kind': 'bank', 'bank_name': 'Bank', 'amount': amount,
        'allocations': [], 'occurred_at': NOW, 'actual_fee_amount': '0.00',
        **({'settlement_scope': 'account'} if account else {})}]
    return state


def party(state, kind):
    return next(row for row in report(state)['parties'] if row['party_type'] == kind)


def net(row):
    return Decimal(row['outstanding_payable']) - Decimal(row['outstanding_receivable'])


def test_supplier_account_payment_attaches_invoice_without_second_balance_deduction():
    state = pure_state()
    before = report(state)
    state['movements'][0]['allocations'] = [{'obligation_id': 'invoice', 'amount': '50.00'}]
    after = report(state)
    assert next(row for row in after['parties'] if row['party_type'] == 'supplier') == next(
        row for row in before['parties'] if row['party_type'] == 'supplier')
    assert after['summary'] == before['summary']
    claim = next(row for row in after['obligations'] if row['id'] == 'invoice')
    assert (claim['settled'], claim['outstanding']) == ('50.00', '50.00')
    assert party(state, 'bank')['actual'] == '930.00'


def test_legacy_unallocated_cash_reduces_advance_and_invoice_equally():
    state = pure_state(account=False)
    before = party(state, 'supplier')
    state['movements'][0]['allocations'] = [{'obligation_id': 'invoice', 'amount': '50.00'}]
    after = party(state, 'supplier')
    assert (before['outstanding_payable'], before['outstanding_receivable']) == ('100.00', '70.00')
    assert (after['outstanding_payable'], after['outstanding_receivable']) == ('50.00', '20.00')
    assert net(before) == net(after) == Decimal('30.00')
    assert after['actual'] == before['actual'] == '70.00'
    assert party(state, 'bank')['actual'] == '930.00'


def test_empty_account_allocations_preserve_ordinary_payment_projection():
    state = pure_state()
    before = report(state)
    del state['movements'][0]['allocations']
    assert report(state) == before
    assert party(state, 'supplier')['outstanding_payable'] == '30.00'


@pytest.mark.parametrize('patch', [
    {'actual_fee_amount': '5.00'}, {'bank_fee_amount': '5.00'},
    {'bank_amount': '75.00'}, {'bank_currency': 'USD'},
    {'native_payment_operation_id': 'native-id'}, {'status': 'reversed'},
    {'automatic_order_bank': True}, {'bank_id': None},
    {'allocations': [{'obligation_id': 'invoice', 'amount': '75.00'}]},
])
def test_ambiguous_fee_fx_or_linked_principal_is_not_offered(patch):
    state = pure_state()
    state['movements'][0].update(patch)
    result = _candidates(state, 'supplier')
    assert result['items'] == [] and result['excluded']


def test_candidates_isolate_supplier_and_reject_duplicate_payment_identity():
    state = pure_state()
    assert _candidates(state, 'another-supplier')['items'] == []
    state['movements'].append(deepcopy(state['movements'][0]))
    result = _candidates(state, 'supplier')
    assert result['items'] == [] and result['excluded'][0]['count'] == 2


async def setup(db, *, amount='70.00'):
    await seed(db)
    invoice = await save_purchase(db, 'owner', 'owner', purchase_command(actor='owner',
        lines=[{'kind': 'product', 'item_id': 'product', 'quantity': 5, 'unit_price': '20', 'tax': '0'}]), clock=NOW)
    payment = await create_movement(db, 'owner', 'owner', {**movement(), 'amount': amount}, on_account=True, clock=NOW)
    return invoice, payment


def test_native_owner_allocation_concurrent_replay_keeps_cash_and_original_identity():
    async def scenario(db):
        invoice, payment = await setup(db)
        before = await read(db, 'owner')
        available = await candidates(db, OWNER, invoice['id'])
        assert available['items'][0]['available'] == '70.00'
        assert available['invoice']['outstanding'] == '100.00'
        assert (await candidates(db, OWNER, supplier_id='supplier'))['items'] == available['items']
        requests = await asyncio.gather(*[allocate(db, OWNER, invoice['id'], command(payment['id']), clock=NOW)
                                          for _ in range(5)])
        assert all(result == requests[0] for result in requests)
        result = requests[0]
        assert result['cash_effect'] == result['supplier_balance_effect'] == '0.00'
        assert result['invoice']['outstanding'] == '50.00'
        after = await read(db, 'owner')
        assert len(after['movements']) == len(before['movements']) == 1
        assert {k: v for k, v in after['movements'][0].items() if k != 'allocations'} == {
            k: v for k, v in before['movements'][0].items() if k != 'allocations'}
        assert party(after, 'supplier') == party(before, 'supplier')
        assert party(after, 'bank') == party(before, 'bank')
        assert len([event for event in after['audit'] if event['action'] == 'inventory_payment_allocated']) == 1
        assert (await candidates(db, OWNER, invoice['id']))['items'][0]['available'] == '20.00'
        assert await db.general_ledger.count_documents({}) == 0
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
    run(scenario)


def test_native_competing_allocations_cannot_overconsume_original_payment():
    async def scenario(db):
        invoice, payment = await setup(db)
        results = await asyncio.gather(*[
            allocate(db, OWNER, invoice['id'], command(payment['id'], request_id='competing-' + str(i)), clock=NOW)
            for i in range(2)], return_exceptions=True)
        assert sum(isinstance(result, HTTPException) for result in results) == 1
        state = await read(db, 'owner')
        assert state['movements'][0]['allocations'] == [{'obligation_id': invoice['obligation_id'], 'amount': '50.00'}]
        assert party(state, 'bank')['actual'] == '930.00'
        assert party(state, 'supplier')['outstanding_payable'] == '30.00'
    run(scenario)


def test_native_partial_then_remaining_allocation_merges_same_invoice_once():
    async def scenario(db):
        invoice, payment = await setup(db)
        await allocate(db, OWNER, invoice['id'], command(payment['id']), clock=NOW)
        result = await allocate(db, OWNER, invoice['id'], command(payment['id'], amount='20.00', request_id='allocate-rest-2'), clock=NOW)
        assert result['invoice']['outstanding'] == '30.00'
        state = await read(db, 'owner')
        assert state['movements'][0]['allocations'] == [{'obligation_id': invoice['obligation_id'], 'amount': '70.00'}]
        assert (await candidates(db, OWNER, invoice['id']))['items'] == []
        assert party(state, 'supplier')['outstanding_payable'] == '30.00'
        assert party(state, 'bank')['actual'] == '930.00'
    run(scenario)


@pytest.mark.parametrize('patch,expected', [
    ({'amount': '70.01'}, 'inventory_payment_allocation_exceeded'),
    ({'amount': '0'}, 'operational_amount_invalid'),
    ({'business_date': '2026-10-06'}, 'inventory_payment_date_invalid'),
    ({'business_date': '2026-10-08'}, 'inventory_payment_date_invalid'),
    ({'expected_session_scope': digest(['other', 'owner'])}, 'operational_actor_scope_changed'),
])
def test_native_bad_amount_date_or_session_cannot_mutate_allocations(patch, expected):
    async def scenario(db):
        invoice, payment = await setup(db)
        before = await read(db, 'owner')
        with pytest.raises(HTTPException) as caught:
            await allocate(db, OWNER, invoice['id'], command(payment['id'], **patch), clock=NOW)
        assert caught.value.detail['code'] == expected
        assert (await read(db, 'owner')) == before
    run(scenario)


def test_native_invoice_remaining_and_supplier_scope_are_revalidated():
    async def scenario(db):
        invoice, payment = await setup(db)
        await create_movement(db, 'owner', 'owner', {**movement(), 'request_id': 'another-payment', 'amount': '30.00',
            'allocations': [{'obligation_id': invoice['obligation_id'], 'amount': '30.00'}]}, clock=NOW)
        await allocate(db, OWNER, invoice['id'], command(payment['id']), clock=NOW)
        with pytest.raises(HTTPException) as caught:
            await allocate(db, OWNER, invoice['id'], command(payment['id'], request_id='too-much-invoice', amount='20.01'), clock=NOW)
        # Payment residual is independently validated first.
        assert caught.value.detail['code'] == 'inventory_payment_allocation_exceeded'
        await save_adjustment(db, 'owner', 'owner', {
            'request_id': 'accepted-discount', 'expected_session_scope': digest(['owner', 'owner']),
            'invoice_id': invoice['id'], 'kind': 'discount', 'amount': '25.00', 'lines': [],
            'accepted': True, 'reference': 'accepted-credit', 'business_date': NOW[:10]}, clock=NOW)
        with pytest.raises(HTTPException) as caught:
            await allocate(db, OWNER, invoice['id'], command(payment['id'], request_id='over-invoice', amount='10.00'), clock=NOW)
        assert caught.value.detail['code'] == 'inventory_payment_invoice_exceeded'
    run(scenario)


def test_native_nonowner_crossowner_and_changed_request_cannot_allocate():
    async def scenario(db):
        invoice, payment = await setup(db)
        for user in [{'id': 'staff'}, {'id': 'viewer'}, {'id': 'owner', '_mobile_actor_id': 'staff'}]:
            with pytest.raises(HTTPException) as caught:
                await allocate(db, user, invoice['id'], command(payment['id']), clock=NOW)
            assert caught.value.status_code == 403
            with pytest.raises(HTTPException):
                await candidates(db, user, invoice['id'])
        with pytest.raises(HTTPException):
            await candidates(db, {'id': 'other'}, invoice['id'])
        await allocate(db, OWNER, invoice['id'], command(payment['id']), clock=NOW)
        with pytest.raises(HTTPException) as caught:
            await allocate(db, OWNER, invoice['id'], command(payment['id'], amount='49.00'), clock=NOW)
        assert caught.value.detail['code'] == 'operational_request_scope_conflict'
        assert len((await read(db, 'owner'))['movements']) == 1
    run(scenario)


def test_native_payment_cannot_be_attached_to_another_supplier_invoice():
    async def scenario(db):
        invoice, payment = await setup(db)
        await db.mezan_suppliers_v2.insert_one({'id': 'second-supplier', 'user_id': 'owner',
                                              'name': 'Second supplier', 'status': 'active'})
        other = await save_purchase(db, 'owner', 'owner', purchase_command(actor='owner',
            request_id='second-supplier-invoice', supplier_id='second-supplier', invoice_number='SECOND',
            lines=[{'kind': 'product', 'item_id': 'product', 'quantity': 5, 'unit_price': '20', 'tax': '0'}]), clock=NOW)
        before = await read(db, 'owner')
        assert (await candidates(db, OWNER, other['id']))['items'] == []
        with pytest.raises(HTTPException) as caught:
            await allocate(db, OWNER, other['id'], command(payment['id']), clock=NOW)
        assert caught.value.detail['code'] == 'inventory_payment_unavailable'
        assert await read(db, 'owner') == before
    run(scenario)


def test_native_closed_operation_and_active_accounting_reject_new_allocation():
    async def scenario(db):
        invoice, payment = await setup(db)
        await freeze(db, 'owner', 'owner', {'request_id': 'close-operation', 'reason': 'test close'}, clock=NOW)
        with pytest.raises(HTTPException) as caught:
            await allocate(db, OWNER, invoice['id'], command(payment['id']), clock=NOW)
        assert caught.value.detail['code'] == 'operational_not_active'
        async def activate(state):
            state['status'] = 'active'
        await mutate(db, 'owner', activate)
        await db.mz2_atomic_owners.insert_one({'_id': 'owner', 'ledger_backend_state': 'v2_active'})
        with pytest.raises(HTTPException) as caught:
            await allocate(db, OWNER, invoice['id'], command(payment['id'], request_id='blocked-cutover'), clock=NOW)
        assert caught.value.detail['code'] == 'operational_accounting_active'
        assert (await read(db, 'owner'))['movements'][0]['allocations'] == []
    run(scenario)


def test_native_same_request_with_conflicting_contents_commits_one_allocation():
    async def scenario(db):
        invoice, payment = await setup(db)
        replies = await asyncio.gather(*[
            allocate(db, OWNER, invoice['id'], command(payment['id'], amount=value), clock=NOW)
            for value in ['49.00', '50.00']], return_exceptions=True)
        failures = [result for result in replies if isinstance(result, HTTPException)]
        assert len(failures) == 1 and failures[0].detail['code'] == 'operational_request_scope_conflict'
        winner = next(result for result in replies if not isinstance(result, Exception))
        state = await read(db, 'owner')
        assert state['movements'][0]['allocations'] == [
            {'obligation_id': invoice['obligation_id'], 'amount': winner['allocated_amount']}]
        assert party(state, 'supplier')['outstanding_payable'] == '30.00'
        assert party(state, 'bank')['actual'] == '930.00'
    run(scenario)


def test_native_original_cash_source_can_close_without_repaying_or_retargeting():
    async def scenario(db):
        invoice, payment = await setup(db)
        await db.mz2_financial_accounts.update_one({'user_id': 'owner', 'id': 'bank'},
                                                  {'$set': {'status': 'disabled', 'name': 'Renamed closed bank'}})
        result = await allocate(db, OWNER, invoice['id'], command(payment['id']), clock=NOW)
        state = await read(db, 'owner')
        assert result['cash_effect'] == '0.00'
        assert state['movements'][0]['bank_id'] == payment['bank_id']
        assert state['movements'][0]['bank_name'] == payment['bank_name']
        assert party(state, 'bank')['actual'] == '930.00'
    run(scenario)


def test_native_owner_revocation_at_final_commit_preserves_original_allocation(monkeypatch):
    import operational_inventory_payment_allocation as service
    original = service.prepare_in_thread

    async def scenario(db):
        invoice, payment = await setup(db)
        before = await read(db, 'owner')

        async def revoke(function, *args, **kwargs):
            result = await original(function, *args, **kwargs)
            if function is service.audit:
                await db.users.update_one({'id': 'owner'}, {'$set': {'disabled': True}})
            return result

        monkeypatch.setattr(service, 'prepare_in_thread', revoke)
        with pytest.raises(HTTPException) as caught:
            await allocate(db, OWNER, invoice['id'], command(payment['id']), clock=NOW)
        assert caught.value.status_code == 403
        assert await read(db, 'owner') == before
    run(scenario)


def test_native_http_owner_scoped_candidates_allocation_retry_and_forbidden_fields():
    async def scenario(db):
        invoice, payment = await setup(db)
        base = '/api/operational-balances'
        url = base + '/inventory-purchases/' + invoice['id'] + '/allocate-payment'
        async with client(db, 'owner', mobile=False) as c:
            available = await c.get(base + '/inventory-purchase-payment-candidates', params={'supplier_id': 'supplier'})
            assert available.status_code == 200, available.text
            assert available.json()['items'][0]['available'] == '70.00'
            by_invoice = await c.get(base + '/inventory-purchases/' + invoice['id'] + '/payment-candidates')
            assert by_invoice.status_code == 200 and by_invoice.json()['invoice']['outstanding'] == '100.00'
            payload = command(payment['id'])
            bad = await c.post(url, json={**payload, 'bank_id': 'replacement-bank'})
            assert bad.status_code == 422
            first = await c.post(url, json=payload)
            assert first.status_code == 200, first.text
            assert (await c.post(url, json=payload)).json() == first.json()
            assert first.json()['invoice']['outstanding'] == '50.00'
        for actor, mobile in [('staff', False), ('viewer', False), ('owner', True)]:
            async with client(db, actor, mobile=mobile) as c:
                assert (await c.get(base + '/inventory-purchase-payment-candidates', params={'supplier_id': 'supplier'})).status_code == 403
                assert (await c.post(url, json=command(payment['id'], request_id='forbidden-http'))).status_code == 403
        assert len((await read(db, 'owner'))['movements']) == 1
    run(scenario)
