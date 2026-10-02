"""Real imported-bank evidence to native advertising journal acceptance."""
import asyncio
import io
import os
import uuid
from urllib.parse import urlsplit, parse_qs
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from openpyxl import Workbook

from accounting_atomic import atomic_owner
from accounting_advertising_contract import BankMovement, Expense, FxSnapshot, POSTINGS, FX
from accounting_advertising_bridge import bank_movement, post_spend
from accounting_advertising_setup import setup
from accounting_daily_movements import import_daily_movement_file, parse_daily_movement_xlsx
from accounting_ledger_v2 import query_entries_v2
from mz2_native_fixture import provision_native_opening
from test_mz2_advertising_v2 import Monitor, LEGACY, OWNER, seed, approved


async def imported(db, owner, bank, *, value='100.00', direction='out', reference='BANK-001', day='2026-01-02'):
    book = Workbook()
    sheet = book.active
    sheet.append(['date', 'direction', 'amount', 'description', 'reference'])
    sheet.append([day, direction, value, 'Synthetic native bank evidence', reference])
    output = io.BytesIO()
    book.save(output)
    content = output.getvalue()
    async def save(scoped):
        return await import_daily_movement_file(scoped, owner=owner, actor={'id': owner},
            bank_account_id=bank, filename='synthetic.xlsx', content=content, parsed=parse_daily_movement_xlsx(content))
    result = await atomic_owner(db, owner, save)
    return result['items'][0]


@pytest_asyncio.fixture
async def db():
    uri = os.environ['MZ2_TEST_MONGO_URI']
    parsed = urlsplit(uri)
    assert parsed.scheme == 'mongodb' and parsed.hostname in {'127.0.0.1','localhost','::1'}
    assert not parsed.username and not parsed.password and parse_qs(parsed.query).get('replicaSet')
    monitor = Monitor()
    client = AsyncIOMotorClient(uri, event_listeners=[monitor])
    assert (await client.admin.command('hello')).get('setName') == parse_qs(parsed.query)['replicaSet'][0]
    database = client['mz2_bank_adapters_' + uuid.uuid4().hex]
    await database.users.insert_one({'id': OWNER, 'role': 'owner', 'is_active': True})
    await provision_native_opening(database, owner=OWNER, bank_balances={'bank-proof': '1000'},
        zero_accounts=[('ad_account', 'meta-wallet', 'balance'), ('ad_account', 'meta-payable', 'debt')])
    try:
        yield database
        assert not LEGACY.intersection(monitor.collections), monitor.collections
    finally:
        await client.drop_database(database.name)
        client.close()


def payload(movement, **changes):
    data = dict(platform='meta', integration_account_id='meta-v2', kind='wallet_funding',
        bank_financial_account_id='bank-proof', bank_evidence_id=movement['id'], amount_sar='100.00',
        effective_at=datetime(2026,1,2,12,tzinfo=timezone.utc))
    data.update(changes)
    return BankMovement(**data)


async def ready(db, mode='prepaid', currency='SAR'):
    await seed(db, currency=currency)
    return await approved(db, mode=mode, currency=currency)


@pytest.mark.asyncio
async def test_imported_funding_atomic_replay_no_advertising_expense(db):
    await ready(db)
    row = await imported(db, OWNER, 'bank-proof')
    request = payload(row)
    results = await asyncio.gather(*[bank_movement(db, OWNER, request) for _ in range(3)])
    assert len({r['txn_group_id'] for r in results}) == 1
    legs = await query_entries_v2(db, user_id=OWNER, txn_group_id=results[0]['txn_group_id'])
    assert {(r['entity_type'],r['entity_id'],r['side'],r['amount']) for r in legs} == {
        ('bank','bank-proof','credit','100.00'), ('ad_account','meta-wallet','debit','100.00')}
    assert (await db.mz2_daily_movements.find_one({'id':row['id']}))['status'] == 'accounting_posted'
    with pytest.raises(HTTPException, match='ad_bank_idempotency_conflict'):
        await bank_movement(db, OWNER, payload(row, amount_sar='90.00'))


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',['manual','foreign','amount','bytes','bank','consumed','provider','duplicate'])
async def test_unverified_evidence_rejected_zero_effect(db, mutation):
    await ready(db)
    row = await imported(db, OWNER, 'bank-proof')
    if mutation == 'bytes':
        await db.accounting_source_files.update_one({'file_id':row['file_id']},{'$set':{'content':b'corrupt'}})
    elif mutation == 'duplicate':
        duplicate = await db.mz2_daily_movements.find_one({'id':row['id']})
        duplicate['_id'] = 'copy'; duplicate['id'] = 'copy'
        await db.mz2_daily_movements.insert_one(duplicate)
        row['id'] = 'copy'
    else:
        change = {'manual':{'source':'manual_accountant'},'foreign':{'user_id':'another-owner'},
            'amount':{'amount':'99.00'},'bank':{'bank_account_id':'other'},
            'consumed':{'status':'accounting_posted'},'provider':{'confirmed_provider':'salla'}}[mutation]
        await db.mz2_daily_movements.update_one({'id':row['id']},{'$set':change})
    before = await db.accounting_general_ledger_v2.count_documents({})
    with pytest.raises(HTTPException, match='native_bank_statement_evidence_required'):
        await bank_movement(db, OWNER, payload(row))
    assert await db.accounting_general_ledger_v2.count_documents({}) == before
    assert await db[POSTINGS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_funding_rollback_restores_evidence_and_ledger(db, monkeypatch):
    import accounting_advertising_bridge as bridge
    await ready(db)
    row = await imported(db, OWNER, 'bank-proof')
    original = bridge.post_journal_v2
    async def fail_after(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('synthetic-after-journal')
    monkeypatch.setattr(bridge, 'post_journal_v2', fail_after)
    before = await db.accounting_general_ledger_v2.count_documents({})
    with pytest.raises(RuntimeError, match='synthetic-after-journal'):
        await bank_movement(db, OWNER, payload(row))
    assert await db.accounting_general_ledger_v2.count_documents({}) == before
    assert (await db.mz2_daily_movements.find_one({'id':row['id']}))['status'] == 'unclassified'
    assert await db[POSTINGS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_bank_fee_has_separate_imported_evidence_and_expense(db):
    await ready(db)
    await setup(db, OWNER, Expense(purpose='bank_fee',entity_id='verified-bank-fee',evidence='Owner fee classification'))
    row = await imported(db, OWNER, 'bank-proof')
    fee = await imported(db, OWNER, 'bank-proof',value='5.00',reference='FEE-001')
    result = await bank_movement(db, OWNER, payload(row,bank_fee_sar='5.00',bank_fee_evidence=fee['id']))
    legs = await query_entries_v2(db,user_id=OWNER,txn_group_id=result['txn_group_id'])
    assert {(r['entity_type'],r['side'],r['amount']) for r in legs} == {
        ('ad_account','debit','100.00'),('bank','credit','105.00'),('expense','debit','5.00')}
    assert await db.mz2_daily_movements.count_documents({'status':'accounting_posted'}) == 2


@pytest.mark.asyncio
async def test_payable_settlement_does_not_double_spend_and_cannot_overpay(db):
    from accounting_advertising_contract import SpendPost
    fact = await ready(db,mode='postpaid')
    spend = await post_spend(db, OWNER, SpendPost(snapshot_id=fact['id']))
    row = await imported(db,OWNER,'bank-proof',value='30.00')
    result = await bank_movement(db,OWNER,payload(row,kind='payable_settlement',amount_sar='30.00'))
    legs = await query_entries_v2(db,user_id=OWNER,txn_group_id=result['txn_group_id'])
    assert all(r['entity_type'] != 'expense' for r in legs)
    row2 = await imported(db,OWNER,'bank-proof',value='1.00',reference='OVERPAY')
    with pytest.raises(HTTPException,match='ad_payable_over_settlement'):
        await bank_movement(db,OWNER,payload(row2,kind='payable_settlement',amount_sar='1.00'))


@pytest.mark.asyncio
async def test_foreign_wallet_funding_preserves_confirmed_original_units(db):
    await ready(db,currency='USD')
    fx = await setup(db, OWNER, FxSnapshot(currency='USD',business_date='2026-01-02',fx_rate_to_sar='4',
        fx_at=datetime(2026,1,2,tzinfo=timezone.utc),fx_source='Synthetic owner verified exchange',evidence='Synthetic bank exchange'))
    row = await imported(db,OWNER,'bank-proof')
    result = await bank_movement(db,OWNER,payload(row,original_wallet_currency_amount='25',wallet_currency='USD',fx_snapshot_id=fx['id']))
    move = await db.mz2_ad_wallet_movements_v2.find_one({'movement_type':'wallet_funding'})
    assert move['original_currency_amount'] == '25' and move['currency'] == 'USD'
    assert move['txn_group_id'] == result['txn_group_id'] and move['fx_snapshot_id'] == fx['id']

@pytest.mark.asyncio
@pytest.mark.parametrize('case',['insufficient','different_day','naive','paused','inactive_bank','reused_fee','fee_missing','fx_mismatch'])
async def test_financial_controls_remain_closed(db,case):
    await ready(db,currency='USD' if case == 'fx_mismatch' else 'SAR')
    row = await imported(db,OWNER,'bank-proof',value='1001.00' if case == 'insufficient' else '100.00')
    changes = {}
    expected = {'insufficient':'ad_bank_insufficient_balance','different_day':'ad_bank_effective_date_invalid',
        'naive':'timezone','paused':'paused','inactive_bank':'MZ2_LINK_REQUIRED',
        'reused_fee':'ad_bank_fee_separate_evidence_required','fee_missing':'native_bank_statement_evidence_required',
        'fx_mismatch':'ad_wallet_funding_fx_amount_mismatch'}[case]
    if case == 'insufficient': changes['amount_sar'] = '1001.00'
    if case == 'different_day': changes['effective_at'] = datetime(2026,1,3,tzinfo=timezone.utc)
    if case == 'naive': changes['effective_at'] = datetime(2026,1,2)
    if case == 'paused':
        await db.mz2_atomic_owners.update_one({'_id':OWNER},{'$set':{'writes_paused':True}})
    if case == 'inactive_bank':
        await db.mz2_financial_accounts.update_one({'id':'bank-proof'},{'$set':{'status':'inactive'}})
    if case in {'reused_fee','fee_missing'}:
        changes.update(bank_fee_sar='5.00',bank_fee_evidence=row['id'] if case == 'reused_fee' else 'absent-fee-proof')
    if case == 'fx_mismatch':
        fx = await setup(db,OWNER,FxSnapshot(currency='USD',business_date='2026-01-02',fx_rate_to_sar='4',
            fx_at=datetime(2026,1,2,tzinfo=timezone.utc),fx_source='Synthetic bank',evidence='Verified exchange'))
        changes.update(original_wallet_currency_amount='20',wallet_currency='USD',fx_snapshot_id=fx['id'])
    before = await db.accounting_general_ledger_v2.count_documents({})
    with pytest.raises(HTTPException,match=expected):
        await bank_movement(db,OWNER,payload(row,**changes))
    assert await db.accounting_general_ledger_v2.count_documents({}) == before
    assert (await db.mz2_daily_movements.find_one({'id':row['id']}))['status'] == 'unclassified'

@pytest.mark.asyncio
async def test_resealed_replay_pointer_to_other_valid_journal_rejected(db):
    from accounting_advertising_contract import digest
    await ready(db)
    first = await imported(db,OWNER,'bank-proof')
    second = await imported(db,OWNER,'bank-proof',reference='BANK-002')
    await bank_movement(db,OWNER,payload(first))
    result = await bank_movement(db,OWNER,payload(second))
    record = await db[POSTINGS].find_one({'_id':digest([OWNER,'ad-bank',first['id']])})
    record['txn_group_id'] = result['txn_group_id']
    record['seal'] = digest({k:v for k,v in record.items() if k != 'seal'})
    await db[POSTINGS].replace_one({'_id':record['_id']},record)
    before = await db.accounting_general_ledger_v2.count_documents({})
    with pytest.raises(HTTPException,match='ad_bank_posted_journal_mismatch'):
        await bank_movement(db,OWNER,payload(first))
    assert await db.accounting_general_ledger_v2.count_documents({}) == before
