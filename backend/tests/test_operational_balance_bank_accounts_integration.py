"""Isolated Mongo evidence for bank/terminal setup, opening and transfer."""
from copy import deepcopy
import pytest
from test_operational_balance_integration import run, opening, movement, START, NOW
from test_operational_balance_app_permissions import client, grant, WRITE
from operational_balance_service import save_opening, create_movement, report
from operational_balance_store import read


@pytest.mark.parametrize('mobile', [False, True])
def test_bank_identity_setup_owner_only_idempotent_without_balance_or_native_write(mobile):
    async def scenario(db):
        await grant(db, [WRITE])
        native_before = await db.mz2_financial_accounts.find({}).to_list(100)
        payload = {'name': 'جهاز الشبكة', 'currency': 'SAR', 'request_id': 'terminal-create-1'}
        async with client(db, 'staff', mobile=mobile) as c:
            assert (await c.post('/api/operational-balances/entities/bank', json=payload)).status_code == 403
        async with client(db, 'owner', mobile=mobile) as c:
            first = await c.post('/api/operational-balances/entities/bank', json=payload)
            assert first.status_code == 200, first.text
            second = await c.post('/api/operational-balances/entities/bank', json=payload)
            assert second.json() == first.json()
            assert first.json()['id'].startswith('operational-bank:')
            conflict = await c.post('/api/operational-balances/entities/bank', json={**payload, 'name': 'Changed'})
            assert conflict.status_code == 409
        assert await db.mz2_operational_bank_accounts.count_documents({'user_id': 'owner'}) == 1
        assert await db.mz2_financial_accounts.find({}).to_list(100) == native_before
        state = await read(db, 'owner')
        assert not state['openings'] and not state['movements'] and state['status'] == 'draft'
        assert await db.mz2_opening_facts_v2.count_documents({}) == 0
    run(scenario)


def test_terminal_opening_and_bank_transfer_conserve_liquidity_without_driver():
    async def scenario(db):
        bank = {'user_id': 'owner', 'id': 'rajhi', 'name': 'الراجحي', 'account_type': 'bank',
                'currency': 'SAR', 'status': 'active', 'opening_balance': 999999}
        await db.accounts.insert_one(deepcopy(bank))
        await db.mz2_operational_bank_accounts.insert_one({'user_id': 'owner', 'id': 'terminal',
            'name': 'الشبكة', 'account_type': 'bank', 'currency': 'SAR', 'status': 'active'})
        terminal_id = 'operational-bank:terminal'
        await save_opening(db, 'owner', 'owner', opening('bank', 'account:rajhi', '1000'), clock=START)
        await save_opening(db, 'owner', 'owner', opening('bank', terminal_id, '250', request='terminal-opening'), clock=START)
        await save_opening(db, 'owner', 'owner', {'request_id': 'finish-accounts'}, finish=True, clock=START)
        payload = {**movement(), 'party_type': 'bank', 'party_id': 'account:rajhi',
                   'bank_id': terminal_id, 'source_account_type': 'bank', 'kind': 'transfer', 'amount': '100'}
        await create_movement(db, 'owner', 'owner', payload, clock=NOW)
        await create_movement(db, 'owner', 'owner', payload, clock=NOW)
        state = await read(db, 'owner')
        result = report(state)
        balances = {p['party_id']: p['actual'] for p in result['parties']}
        assert balances['account:rajhi'] == '1100.00'
        assert balances[terminal_id] == '150.00'
        assert result['summary']['actual_liquidity'] == '1250.00'
        assert len(state['movements']) == 1
        assert (await db.accounts.find_one({'id': 'rajhi'}))['opening_balance'] == 999999
        assert await db.store_drivers.count_documents({}) == 0
        assert await db.mz2_opening_facts_v2.count_documents({}) == 0
    run(scenario)
