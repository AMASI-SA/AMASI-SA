"""Explicit loan intents using existing operational cash; synthetic isolated Mongo."""
from copy import deepcopy
import asyncio
import pytest
import httpx
from fastapi import FastAPI, Header, HTTPException

from operational_balance_service import create_movement, report, save_opening, refresh
from operational_balance_store import read, STATES, digest
from operational_balance_routes import make_operational_balance_router
from operational_external_advances import advance_id, advance_obligations
from test_operational_balance_integration import run, opening, movement, START, NOW
from test_operational_balance_workspace import BASE, grant as workspace_grant, resource
from test_operational_balance_assigned_banks import workspace_command


async def seed(db):
    await db.mz2_external_persons_v2.insert_many([
        {'user_id':'owner','id':'person','name':'طرف تجريبي','currency':'SAR','status':'active'},
        {'user_id':'other','id':'foreign','name':'جهة أخرى','currency':'SAR','status':'active'}])
    await save_opening(db,'owner','owner',opening(amount='1000'),clock=START)
    await save_opening(db,'owner','owner',{'request_id':'start-loans'},finish=True,clock=START)


def payload(action,amount='100',key=None,**changes):
    direction,kind={'borrow':('incoming','collection'),'lend':('outgoing','payment'),
        'repay_borrowing':('outgoing','settlement'),'collect_lending':('incoming','settlement')}[action]
    p=movement();p.update(request_id=key or action,party_type='external_person',party_id='person',
        bank_id='bank',amount=amount,direction=direction,kind=kind,external_operation=action)
    p.update(changes);return p


def balances(state):
    r=report(state)
    person=next((p for p in r['parties'] if p['party_type']=='external_person' and p['party_id']=='person'),{})
    bank=next(p for p in r['parties'] if p['party_type']=='bank' and p['party_id']=='bank')
    return bank['actual'],person.get('outstanding_receivable','0.00'),person.get('outstanding_payable','0.00')


@pytest.mark.parametrize('start,repay,created,partial,closed',[
    ('borrow','repay_borrowing',('1100.00','0.00','100.00'),('1060.00','0.00','60.00'),('1000.00','0.00','0.00')),
    ('lend','collect_lending',('900.00','100.00','0.00'),('940.00','60.00','0.00'),('1000.00','0.00','0.00'))])
def test_four_directions_and_partial_full_repayment_are_exact(start,repay,created,partial,closed):
    async def scenario(db):
        await seed(db);baseline=deepcopy((await read(db,'owner'))['openings'])
        a=await create_movement(db,'owner','owner',payload(start),clock=NOW)
        state=await read(db,'owner');assert balances(state)==created
        assert a['allocations']==[]
        assert a['external_balance_snapshot']['before']=={'receivable_sar':'0.00','payable_sar':'0.00','account_balance_sar':'1000.00'}
        b=await create_movement(db,'owner','owner',payload(repay,'40'),clock=NOW)
        assert balances(await read(db,'owner'))==partial
        assert b['allocations']==[{'obligation_id':advance_id(a),'amount':'40.00'}]
        assert await create_movement(db,'owner','owner',payload(repay,'40'),clock=NOW)==b
        await create_movement(db,'owner','owner',payload(repay,'60','last-payment'),clock=NOW)
        final=await read(db,'owner');assert balances(final)==closed and len(final['movements'])==3
        assert final['openings']==baseline
        assert len(report(final)['obligations'])==1
        assert report(final)['obligations'][0]['outstanding']=='0.00'
        assert await db.general_ledger.count_documents({})==0
        assert await db.account_transactions.count_documents({})==0
    run(scenario)


def test_new_loan_does_not_settle_opposite_existing_loan_by_direction_alone():
    async def scenario(db):
        await seed(db)
        await create_movement(db,'owner','owner',payload('borrow'),clock=NOW)
        lending=await create_movement(db,'owner','owner',payload('lend','40'),clock=NOW)
        assert lending['allocations']==[]
        assert balances(await read(db,'owner'))==('1060.00','40.00','100.00')
        await create_movement(db,'owner','owner',payload('repay_borrowing','20'),clock=NOW)
        assert balances(await read(db,'owner'))==('1040.00','40.00','80.00')
    run(scenario)


@pytest.mark.parametrize('action,changes',[
    ('borrow',{'direction':'outgoing'}),('lend',{'kind':'collection'}),
    ('repay_borrowing',{'kind':'payment'}),('collect_lending',{'direction':'outgoing'}),
    ('borrow',{'party_type':'supplier','party_id':'supplier'}),('lend',{'external_operation':'unknown'}),
    ('lend',{'allocations':[{'obligation_id':'x','amount':'100'}]}),
    ('borrow',{'allocations':[{'obligation_id':'x','amount':'100'}]}),
    ('borrow',{'party_id':'foreign'}),('repay_borrowing',{'amount':'-1'}),
    ('repay_borrowing',{'amount':'100.001'}),('collect_lending',{'amount':'NaN'}),
])
def test_invalid_loan_intent_never_changes_openings_or_movements(action,changes):
    async def scenario(db):
        await seed(db);before=await read(db,'owner')
        with pytest.raises(HTTPException):
            await create_movement(db,'owner','owner',payload(action,**changes),clock=NOW)
        after=await read(db,'owner')
        assert after['movements']==before['movements'] and after['openings']==before['openings']
        assert balances(after)==('1000.00','0.00','0.00')
    run(scenario)


@pytest.mark.parametrize('action',['repay_borrowing','collect_lending'])
def test_no_debt_and_overpayment_are_not_converted_to_an_unintended_new_loan(action):
    async def scenario(db):
        await seed(db)
        with pytest.raises(HTTPException):
            await create_movement(db,'owner','owner',payload(action),clock=NOW)
        create='borrow' if action=='repay_borrowing' else 'lend'
        await create_movement(db,'owner','owner',payload(create,'50','create50'),clock=NOW)
        before=await read(db,'owner')
        with pytest.raises(HTTPException):
            await create_movement(db,'owner','owner',payload(action,'60','too-much'),clock=NOW)
        after=await read(db,'owner');assert after['movements']==before['movements']
    run(scenario)


def test_concurrent_repayment_cannot_overconsume_debt_and_exact_retry_replays():
    async def scenario(db):
        await seed(db)
        await create_movement(db,'owner','owner',payload('borrow','100'),clock=NOW)
        replies=await asyncio.gather(*[create_movement(db,'owner','owner',payload('repay_borrowing','70',f'payment-{i}'),clock=NOW)
                                      for i in range(2)],return_exceptions=True)
        assert sum(isinstance(r,HTTPException) for r in replies)==1
        assert balances(await read(db,'owner'))==('1030.00','0.00','30.00')
        winner=next(r for r in replies if not isinstance(r,Exception))
        assert await create_movement(db,'owner','owner',payload('repay_borrowing','70',winner['request_id']),clock=NOW)==winner
        with pytest.raises(HTTPException):
            await create_movement(db,'owner','owner',payload('repay_borrowing','60',winner['request_id']),clock=NOW)
        assert len((await read(db,'owner'))['movements'])==2
    run(scenario)


def test_saved_generic_advance_can_be_explicitly_repaid_without_rewriting_history():
    async def scenario(db):
        await seed(db)
        legacy=payload('borrow');legacy.pop('external_operation')
        original=await create_movement(db,'owner','owner',legacy,clock=NOW)
        before=await read(db,'owner');assert balances(before)==('1100.00','0.00','100.00')
        paid=await create_movement(db,'owner','owner',payload('repay_borrowing','40'),clock=NOW)
        after=await read(db,'owner')
        assert after['movements'][0]==original
        assert after['openings']==before['openings']
        assert balances(after)==('1060.00','0.00','60.00')
        assert paid['allocations'][0]['obligation_id']==advance_id(original)
        assert not report(after)['obligations'][0]['explicit_loan']
    run(scenario)


def test_old_opposite_generic_movements_are_not_silently_reinterpreted_as_repayment():
    async def scenario(db):
        await seed(db)
        for action,amount in [('borrow','100'),('lend','40')]:
            p=payload(action,amount);p.pop('external_operation')
            await create_movement(db,'owner','owner',p,clock=NOW)
        state=await read(db,'owner');before=deepcopy(state)
        assert balances(state)==('1060.00','40.00','100.00')
        assert state==before
    run(scenario)


@pytest.mark.parametrize('direction,action', [('for_party','repay_borrowing'),('for_us','collect_lending')])
def test_explicit_repayment_can_reduce_saved_opening_without_changing_it(direction,action):
    async def scenario(db):
        await db.mz2_external_persons_v2.insert_one({'user_id':'owner','id':'person','name':'طرف','currency':'SAR','status':'active'})
        await save_opening(db,'owner','owner',opening(),clock=START)
        await save_opening(db,'owner','owner',opening('external_person','person','100',direction,'old-person'),clock=START)
        await save_opening(db,'owner','owner',{'request_id':'activate'},finish=True,clock=START)
        before=await read(db,'owner')
        await create_movement(db,'owner','owner',payload(action,'40'),clock=NOW)
        after=await read(db,'owner');assert after['openings']==before['openings']
        assert balances(after)[1:]==(('0.00','60.00') if direction=='for_party' else ('60.00','0.00'))
    run(scenario)


def test_http_permission_session_scope_and_optional_field_do_not_break_old_retry():
    async def scenario(db):
        await seed(db)
        await db.users.update_one({'id':'viewer'},{'$set':{'operational_balance_access':{'owner_id':'owner','version':1,'enabled':True,'operations':[],'resources':[resource('external_person','person',use=False),resource('bank','bank',use=False)]}}})
        app=FastAPI()
        async def principal(x_test_user:str=Header(default='owner')):
            return {'id':x_test_user,**({'_session_client':'amasi_mobile'} if x_test_user=='viewer' else {})}
        app.include_router(make_operational_balance_router(db,principal),prefix='/api')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated') as client:
            base='/api/operational-balances'
            context=(await client.get(base+'/context')).json()
            p=payload('borrow',key='http-borrow');p['expected_session_scope']=context['session_scope']
            viewer_context=(await client.get(BASE+'/context',headers={'x-test-user':'viewer'})).json()
            denied=await client.post(base+'/movements',json={**p,'expected_session_scope':viewer_context['session_scope']},headers={'x-test-user':'viewer'})
            assert denied.status_code==403,denied.text
            first=await client.post(base+'/movements',json=p);assert first.status_code==200,first.text
            assert (await client.post(base+'/movements',json=p)).json()==first.json()
            assert first.json()['external_balance_snapshot']['account_label']=='الاستلام في'
            wrong={**p,'request_id':'bad-direction','direction':'outgoing'}
            assert (await client.post(base+'/movements',json=wrong)).status_code==422
            other={**p,'request_id':'other-party','party_id':'foreign'}
            assert (await client.post(base+'/movements',json=other)).status_code>=400
    run(scenario)


async def grant_external(db, can_report, operations=None, version=1):
    await workspace_grant(db, operations=operations if operations is not None else ['external.receive'],
        resources=[resource('external_person','person',balance=can_report),resource('bank','bank',balance=can_report)],version=version)


def workspace_external(**changes):
    return workspace_command(party_type='external_person',party_id='person',side='for_party',reason='receipt',amount='100',**changes)


@pytest.mark.parametrize('can_report',[False,True])
def test_mobile_write_grant_does_not_implicitly_disclose_loan_or_bank_balances(can_report):
    from test_operational_balance_app_permissions import client
    async def scenario(db):
        await seed(db);await grant_external(db,can_report)
        async with client(db) as c:
            context=(await c.get(BASE+'/context')).json()
            p=workspace_external(request_id='mobile-borrow');p['expected_session_scope']=context['session_scope']
            r=await c.post(BASE+'/entries',json=p)
            assert r.status_code==200,r.text
            assert set(r.json())=={'saved','movement_id','party_type','party_id'}
            ledger=(await c.get(BASE+'/ledger/external_person/person')).json()
            assert ledger['items'][0]['recorded_by']['id']=='staff'
            assert (ledger['resource']['balance'] is not None) is can_report
            if can_report:
                assert ledger['resource']['balance']['amount']=='100.00'
            assert (await c.post(BASE+'/entries',json=p)).json()==r.json()
        stored=await read(db,'owner')
        assert stored['movements'][0]['actor_id']=='staff'
        assert balances(stored)==('1100.00','0.00','100.00')
    run(scenario)


def test_native_reporting_revoked_after_commit_redacts_balance_but_keeps_exact_ack(monkeypatch):
    from test_operational_balance_app_permissions import client
    import operational_balance_workspace as workspace
    original=workspace.create_movement
    async def record_then_revoke(db,*a,**kw):
        result=await original(db,*a,**kw)
        await grant_external(db,False,version=2)
        return result
    monkeypatch.setattr(workspace,'create_movement',record_then_revoke)
    async def scenario(db):
        await seed(db);await grant_external(db,True)
        async with client(db) as c:
            p=workspace_external(request_id='revoked-read')
            r=await c.post(BASE+'/entries',json=p)
            assert r.status_code==200,r.text
            assert set(r.json())=={'saved','movement_id','party_type','party_id'}
            ledger=(await c.get(BASE+'/ledger/external_person/person')).json()
            assert ledger['resource']['balance'] is None
            assert len(ledger['items'])==1
            stored=await read(db,'owner')
            assert len(stored['movements'])==1
            assert balances(stored)==('1100.00','0.00','100.00')
            assert (await c.post(BASE+'/entries',json=p)).json()==r.json()
            assert len((await read(db,'owner'))['movements'])==1
    run(scenario)


def test_loan_write_revoked_at_persistence_is_not_recorded(monkeypatch):
    from test_operational_balance_app_permissions import client
    import operational_balance_workspace as workspace
    original=workspace.create_movement
    async def revoke_then_record(db,*a,**kw):
        await grant_external(db,True,operations=[],version=2)
        return await original(db,*a,**kw)
    monkeypatch.setattr(workspace,'create_movement',revoke_then_record)
    async def scenario(db):
        await seed(db);await grant_external(db,True)
        async with client(db) as c:
            r=await c.post(BASE+'/entries',json=workspace_external(request_id='revoked-write'))
            assert r.status_code==403,r.text
        assert balances(await read(db,'owner'))==('1000.00','0.00','0.00')
        assert not (await read(db,'owner'))['movements']
    run(scenario)


def test_refresh_does_not_duplicate_loan_or_recreate_paid_amount():
    from operational_balance_engine import reconcile
    async def scenario(db):
        await seed(db)
        await create_movement(db,'owner','owner',payload('borrow'),clock=NOW)
        await create_movement(db,'owner','owner',payload('repay_borrowing','40'),clock=NOW)
        state=await read(db,'owner');before=deepcopy(state)
        a=reconcile(state,{},NOW);b=reconcile(a,{},NOW)
        assert balances(a)==balances(b)==('1060.00','0.00','60.00')
        assert a['movements']==b['movements']==before['movements'] and state==before
    run(scenario)


def test_native_owner_change_after_commit_cannot_disclose_previous_owner_balance(monkeypatch):
    from test_operational_balance_app_permissions import client
    import operational_balance_workspace as workspace
    original=workspace.create_movement
    async def record_then_reassign(db,*a,**kw):
        result=await original(db,*a,**kw)
        await db.users.update_one({'id':'staff'},{'$set':{'created_by':'other','operational_balance_access.owner_id':'other'}})
        return result
    monkeypatch.setattr(workspace,'create_movement',record_then_reassign)
    async def scenario(db):
        await seed(db);await grant_external(db,True)
        async with client(db) as c:
            p=workspace_external(request_id='owner-change-after-commit')
            r=await c.post(BASE+'/entries',json=p)
            assert r.status_code==200,r.text
            assert set(r.json())=={'saved','movement_id','party_type','party_id'}
            current=(await c.get(BASE+'/context')).json()
            assert current['session_scope']==digest(['other','staff'])
            assert current['resources']==[]
            retried=await c.post(BASE+'/entries',json=p)
            assert retried.status_code==409 and retried.json()['detail']['code']=='operational_actor_scope_changed'
            assert len((await read(db,'owner'))['movements'])==1
            assert not (await read(db,'other'))['movements']
    run(scenario)
