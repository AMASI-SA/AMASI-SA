"""Explicit original-currency zero proof, then existing funding and spend."""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from fastapi import HTTPException

from test_mz2_bank_evidence_adapters import db, imported, payload
from test_mz2_advertising_v2 import OWNER, seed, approved
from accounting_advertising_contract import WalletOpening, FxSnapshot, SpendPost, digest
from accounting_advertising_setup import setup, confirmed_binding
from accounting_advertising_bridge import bank_movement, post_spend
from accounting_advertising_wallet import wallet_position, OPENINGS, MOVEMENTS
from accounting_ledger_v2 import query_entries_v2


async def prepare(db):
    await seed(db,currency='USD',amount=10)
    fact = await approved(db,mode='prepaid',currency='USD')
    fx = await setup(db,OWNER,FxSnapshot(currency='USD',business_date='2026-01-02',fx_rate_to_sar='4',
        fx_at=datetime(2026,1,2,tzinfo=timezone.utc),fx_source='Synthetic verified bank exchange',evidence='Original currency conversion statement'))
    return fact,fx


async def zero_payload(db,**changes):
    cut = (await db.settings.find_one({'user_id':OWNER}))['mezan2_financial_cutover']
    fields = dict(platform='meta',integration_account_id='meta-v2',currency='USD',
        original_currency_amount='0',opening_sar_amount='0.00',zero_original_confirmed=True,
        opening_txn_group_id=cut['opening_active_txn_group_id'],effective_at=cut['cutover_at'],
        evidence='Original provider wallet statement confirms USD zero at cutover')
    fields.update(changes)
    return WalletOpening(**fields)


async def fund(db,fx):
    row = await imported(db,OWNER,'bank-proof')
    return await bank_movement(db,OWNER,payload(row,original_wallet_currency_amount='25',wallet_currency='USD',fx_snapshot_id=fx['id'],effective_at='2026-01-02T00:00:00+03:00'))


@pytest.mark.asyncio
async def test_explicit_zero_then_funding_spend_replay_preserves_original_units(db):
    fact,fx = await prepare(db)
    request = await zero_payload(db)
    opening = await setup(db,OWNER,request)
    assert (await setup(db,OWNER,request))['id'] == opening['id']
    assert opening['original_currency_amount'] == '0' and opening['fx_snapshot_id'] is None
    await fund(db,fx)
    result = await post_spend(db,OWNER,SpendPost(snapshot_id=fact['id'],fx_snapshot_id=fx['id']))
    assert (await post_spend(db,OWNER,SpendPost(snapshot_id=fact['id'],fx_snapshot_id=fx['id'])))['txn_group_id'] == result['txn_group_id']
    binding = await confirmed_binding(db,OWNER,'meta','meta-v2')
    position = await wallet_position(db,OWNER,binding)
    assert position['posted_original_balance'] == '15' and position['opening_materialized']
    markers = await db[MOVEMENTS].find({'movement_type':'opening_zero'}).to_list(2)
    assert len(markers) == 1 and markers[0]['fx_snapshot_id'] is None
    legs = await query_entries_v2(db,user_id=OWNER,entity_type='ad_account',entity_id='meta-wallet',sub_account='balance')
    assert sum(Decimal(r['amount']) * (1 if r['side']=='debit' else -1) for r in legs) == 60
    assert all(r['entry_type'] != 'opening_balance' for r in legs)


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['flag','source','fx','positive_sar','currency','group','date','manifest_missing','manifest_foreign_wallet','manifest_wronggroup','manifest_blank_evidence'])
async def test_zero_requires_explicit_owner_evidence_and_exact_manifest(db,case):
    _,fx = await prepare(db)
    changes = {'flag':{'zero_original_confirmed':False},'source':{'evidence':'   '},
        'fx':{'fx_snapshot_id':fx['id']},'positive_sar':{'opening_sar_amount':'1'},
        'currency':{'currency':'EUR'},'group':{'opening_txn_group_id':'foreign-group'},
        'date':{'effective_at':'2021-01-01T00:00:00Z'}}.get(case,{})
    if case.startswith('manifest_'):
        state = (await db.settings.find_one({'user_id':OWNER}))['mezan2_financial_cutover']
        zeros = state['opening_balance_zero_accounts']
        target = next(z for z in zeros if z['entity_id']=='meta-wallet')
        if case == 'manifest_missing': zeros.remove(target)
        elif case == 'manifest_foreign_wallet': target['entity_id']='another-wallet'
        elif case == 'manifest_wronggroup': target['opening_balance_txn_group_id']='foreign-opening'
        else: target['evidence_ref']=''
        await db.settings.update_one({'user_id':OWNER},{'$set':{'mezan2_financial_cutover.opening_balance_zero_accounts':zeros}})
    before = await db.accounting_general_ledger_v2.count_documents({})
    with pytest.raises((HTTPException,ValueError)):
        await setup(db,OWNER,await zero_payload(db,**changes))
    assert await db[OPENINGS].count_documents({}) == 0
    assert await db.accounting_general_ledger_v2.count_documents({}) == before


@pytest.mark.asyncio
async def test_funding_does_not_infer_original_zero_without_confirmation(db):
    fact,fx = await prepare(db)
    await fund(db,fx)
    with pytest.raises(HTTPException,match='ad_wallet_original_opening_evidence_required'):
        await post_spend(db,OWNER,SpendPost(snapshot_id=fact['id'],fx_snapshot_id=fx['id']))
    assert await db[MOVEMENTS].count_documents({'movement_type':'opening_zero'}) == 0


@pytest.mark.asyncio
async def test_spend_failure_rolls_back_zero_marker_and_journal(db,monkeypatch):
    import accounting_advertising_bridge as bridge
    fact,fx = await prepare(db)
    await setup(db,OWNER,await zero_payload(db))
    await fund(db,fx)
    before = await db.accounting_general_ledger_v2.count_documents({})
    original = bridge.post_journal_v2
    async def fail_after(*args,**kwargs):
        await original(*args,**kwargs)
        raise RuntimeError('synthetic-spend-failure')
    monkeypatch.setattr(bridge,'post_journal_v2',fail_after)
    with pytest.raises(RuntimeError,match='synthetic-spend-failure'):
        await post_spend(db,OWNER,SpendPost(snapshot_id=fact['id'],fx_snapshot_id=fx['id']))
    assert await db.accounting_general_ledger_v2.count_documents({}) == before
    assert await db[MOVEMENTS].count_documents({'movement_type':{'$in':['opening_zero','spend']}}) == 0
    assert await db[MOVEMENTS].count_documents({'movement_type':'wallet_funding'}) == 1


@pytest.mark.asyncio
async def test_foreign_owner_zero_confirmation_cannot_fund_spend(db):
    fact,fx = await prepare(db)
    await setup(db,OWNER,await zero_payload(db))
    await db[OPENINGS].update_one({'user_id':OWNER},{'$set':{'user_id':'foreign-owner'}})
    await fund(db,fx)
    with pytest.raises(HTTPException,match='ad_wallet_original_opening_evidence_required'):
        await post_spend(db,OWNER,SpendPost(snapshot_id=fact['id'],fx_snapshot_id=fx['id']))

def test_positive_opening_still_requires_fx_at_input_boundary():
    with pytest.raises(ValueError,match='opening_fx_snapshot_required'):
        WalletOpening(platform='meta',integration_account_id='meta-v2',currency='USD',
            original_currency_amount='1',opening_sar_amount='4',opening_txn_group_id='native-group',
            effective_at='2020-01-01T00:00:00Z',evidence='Positive original wallet statement')

@pytest.mark.asyncio
async def test_zero_read_capability_still_forbids_all_financial_writes(db):
    from accounting_advertising_setup import _SetupDatabase, ZERO_OPENING_READ_COLLECTIONS
    async with await db.client.start_session() as session:
        state = {'failed':False}
        scoped = _SetupDatabase(db,session,state,opening_reads=True)
        for name in ZERO_OPENING_READ_COLLECTIONS:
            with pytest.raises(HTTPException,match='ad_setup_operation_forbidden'):
                scoped[name].insert_one({'unsafe':True})
        with pytest.raises(HTTPException,match='ad_setup_collection_forbidden'):
            scoped['mz2_atomic_owners']
        assert state['failed'] is True
