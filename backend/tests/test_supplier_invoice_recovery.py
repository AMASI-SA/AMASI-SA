"""Real replica-set transactions with synthetic invoices and real ledger rows."""
import asyncio
import uuid
import pytest
from tests.test_supplier_invoice_financial_integrity import env, seed, close, unchanged
import supplier_receiving_routes as r
from supplier_native_invoice_v2 import close_payload_hash
from tests.test_supplier_native_invoice_v2 import env as native_env
from tests import test_supplier_native_invoice_v2 as native

pytestmark = pytest.mark.asyncio

def attempt(payload, fence=0):
    return {**payload, "client_request_id": uuid.uuid4().hex, "close_fence": fence}

async def check(http, session, payload, seal=False):
    path = f'/supplier-receiving-v1/sessions/{session["id"]}'
    if seal:
        return await http.post(path + '/close-reconcile', json={k: payload[k] for k in ('client_request_id', 'close_fence')})
    return await http.get(path + '/close-operation', params={'client_request_id': payload['client_request_id']})

async def counts(db, invoices=1):
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == invoices
    assert await db.general_ledger.count_documents({}) == invoices * 2
    assert await db[r.RECEIVING_EVENTS].count_documents({'event_type':'supplier_receiving_session_closed'}) == invoices

async def test_open_is_unknown_then_seal_excludes_original_and_allows_explicit_new_attempt(env):
    db,http,_=env; s,p=await seed(db, (2100,3200), services=True); p=attempt(p)
    assert (await check(http,s,p)).json()['state']=='UNKNOWN'
    sealed=await check(http,s,p,True); assert sealed.status_code==200, sealed.text
    proof=(await check(http,s,p)).json()
    assert proof['state']=='NOT_COMMITTED' and proof['retry_allowed'] is True
    assert proof['seal']['previous_fence']==0 and proof['close_fence']==1
    assert (await close(http,s,p)).status_code==409
    await unchanged(db,s)
    new=attempt(p,1); saved=await close(http,s,new); assert saved.status_code==200,saved.text
    assert saved.json()['supplier_invoice']['total_halalas']==p['confirmed_total_halalas']
    await counts(db)

async def test_lost_success_ack_recovers_same_invoice_and_financial_commit_event(env):
    db,http,_=env; s,p=await seed(db); p=attempt(p)
    saved=await close(http,s,p); assert saved.status_code==200,saved.text
    # Client does not consume the response; it only consults original attempt GET.
    read=await check(http,s,p); result=read.json()
    assert result['state']=='COMMITTED' and result['retry_allowed'] is False
    assert result['commit']['client_request_id']==p['client_request_id']
    assert result['commit']['invoice_id']==saved.json()['supplier_invoice']['id']
    assert result['commit']['event_id']
    read_invoice=await http.get('/supplier-receiving-v1/invoices/'+result['commit']['invoice_id'])
    assert read_invoice.json()['supplier_invoice']['financial_integrity_verified'] is True
    assert (await check(http,s,p,True)).json()['supplier_invoice']['id']==result['commit']['invoice_id']
    await counts(db)

async def test_repeated_save_and_reconcile_never_duplicate_invoice_or_journal(env):
    db,http,_=env; s,p=await seed(db); p=attempt(p)
    saved=await asyncio.gather(*(close(http,s,p) for _ in range(4)))
    assert all(x.status_code==200 for x in saved),[x.text for x in saved]
    assert len({x.json()['supplier_invoice']['id'] for x in saved})==1
    checks=await asyncio.gather(*(check(http,s,p,True) for _ in range(4)))
    assert all(x.status_code==200 and x.json()['state']=='COMMITTED' for x in checks)
    await counts(db)

async def test_repeated_seal_is_idempotent_and_does_not_change_draft(env):
    db,http,_=env; s,p=await seed(db); p=attempt(p)
    before=await db[r.RECEIVING_EVENTS].find({}).to_list(100)
    responses=await asyncio.gather(*(check(http,s,p,True) for _ in range(4)))
    assert all(x.status_code==200 for x in responses),[x.text for x in responses]
    assert {x.json()['close_fence'] for x in responses}=={1}
    assert before==await db[r.RECEIVING_EVENTS].find({}).to_list(100)
    await unchanged(db,s)

async def test_old_attempt_cannot_read_a_new_attempt_as_its_success(env):
    db,http,_=env;s,p=await seed(db);old=attempt(p)
    await check(http,s,old,True);new=attempt(p,1)
    assert (await close(http,s,new)).status_code==200
    assert (await check(http,s,old)).status_code==409
    assert (await check(http,s,old,True)).status_code==409
    await counts(db)

async def test_legacy_stuck_draft_recovery_fences_delayed_legacy_post(env):
    db,http,_=env;s,p=await seed(db);recovery=attempt(p)
    assert (await check(http,s,recovery,True)).json()['state']=='NOT_COMMITTED'
    # Old client has no attempt identity, even if it arrives AFTER the seal.
    assert (await close(http,s,p)).status_code==409
    assert (await close(http,s,attempt(p,1))).status_code==200
    await counts(db)

async def test_old_clients_unaffected_until_their_session_is_explicitly_sealed(env):
    db,http,_=env;s,p=await seed(db)
    saved=await close(http,s,p);assert saved.status_code==200,saved.text
    assert (await close(http,s,p)).json()['supplier_invoice']['id']==saved.json()['supplier_invoice']['id']
    proof=(await check(http,s,attempt(p))).json()
    assert proof['state']=='COMMITTED' and proof['commit']['legacy'] is True
    await counts(db)

async def test_seal_wins_against_request_admitted_before_transaction(env,monkeypatch):
    db,http,_=env;s,p=await seed(db);p=attempt(p)
    entered=asyncio.Event();release=asyncio.Event();original=r.transition_state
    async def paused(*args,**kwargs):
        entered.set();await release.wait();return await original(*args,**kwargs)
    monkeypatch.setattr(r,'transition_state',paused)
    task=asyncio.create_task(close(http,s,p));await asyncio.wait_for(entered.wait(),5)
    try:
        assert (await check(http,s,p,True)).json()['state']=='NOT_COMMITTED'
    finally:release.set()
    result=await task;assert result.status_code==409,result.text
    await unchanged(db,s)

async def test_transaction_failure_remains_unknown_until_explicit_seal(env,monkeypatch):
    db,http,_=env;s,p=await seed(db);p=attempt(p);original=r._post_supplier_invoice_ledger
    async def fail(*args,**kwargs):
        await original(*args,**kwargs);raise RuntimeError('synthetic failure after financial insert')
    monkeypatch.setattr(r,'_post_supplier_invoice_ledger',fail)
    assert (await close(http,s,p)).status_code==503
    assert (await check(http,s,p)).json()['state']=='UNKNOWN'
    assert (await check(http,s,p,True)).json()['state']=='NOT_COMMITTED'
    await unchanged(db,s)
    monkeypatch.setattr(r,'_post_supplier_invoice_ledger',original)
    assert (await close(http,s,attempt(p,1))).status_code==200
    await counts(db)

async def test_invoice_commit_wins_reconcile_returns_commit_not_false_negative(env,monkeypatch):
    db,http,_=env;s,p=await seed(db);p=attempt(p)
    entered=asyncio.Event();release=asyncio.Event();original=r.verify_persisted_supplier_invoice
    async def paused(*args,**kwargs):
        result=await original(*args,**kwargs)
        if kwargs.get('mongo_session') is not None and not entered.is_set():
            entered.set();await release.wait()
        return result
    monkeypatch.setattr(r,'verify_persisted_supplier_invoice',paused)
    saving=asyncio.create_task(close(http,s,p));await asyncio.wait_for(entered.wait(),5)
    checking=asyncio.create_task(check(http,s,p,True))
    await asyncio.sleep(.05);release.set()
    saved,checked=await asyncio.gather(saving,checking)
    assert saved.status_code==200,saved.text
    assert checked.status_code==200,checked.text
    assert checked.json()['state']=='COMMITTED' and checked.json()['retry_allowed'] is False
    await counts(db)

async def test_other_employee_cannot_read_or_seal_attempt(env):
    db,http,user=env;s,p=await seed(db);p=attempt(p)
    user['_mobile_actor_id']='other-employee'
    assert (await check(http,s,p)).status_code in (403,404)
    assert (await check(http,s,p,True)).status_code in (403,404)
    assert not (await db[r.SESSIONS].find_one({'id':s['id']})).get('close_seal')

async def test_changed_payload_same_attempt_rejected(env):
    db,http,_=env;s,p=await seed(db);p=attempt(p)
    assert (await close(http,s,p)).status_code==200
    assert (await close(http,s,{**p,'confirmed_total_halalas':2200})).status_code==409
    await counts(db)

async def test_transport_fields_do_not_change_historic_native_hash():
    p=r.SupplierReceivingSessionCloseRequest(confirmed_total_halalas=2100,expected_supplier_id='supplier')
    new=p.model_copy(update={'client_request_id':uuid.uuid4().hex,'close_fence':2})
    assert close_payload_hash(p)==close_payload_hash(new)


@pytest.mark.parametrize('seal_before_retry', [False, True])
async def test_native_v2_readback_and_fenced_retry_keep_one_journal(native_env, seal_before_retry):
    import httpx
    from fastapi import FastAPI
    from copy import deepcopy
    db, commands = native_env
    await native.identities(db); await native.mapping(db)
    session, payload = await native.receiving_session(db)
    payload = attempt(payload)
    async def current(): return deepcopy(native.ACTOR)
    app = FastAPI(); app.include_router(r.make_supplier_receiving_router(db, current))
    commands.collections.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic.test') as http:
        if seal_before_retry:
            assert (await check(http, session, payload, True)).json()['state'] == 'NOT_COMMITTED'
            assert (await close(http, session, payload)).status_code == 409
            payload = attempt(payload, 1)
        saved = await close(http, session, payload)
        assert saved.status_code == 200, saved.text
        read = await check(http, session, payload)
        assert read.status_code == 200, read.text
        proof = read.json()
        assert proof['state'] == 'COMMITTED' and proof['commit']['event_id']
        assert proof['supplier_invoice']['id'] == saved.json()['supplier_invoice']['id']
        assert proof['supplier_invoice']['financial_integrity_verified'] is True
        assert (await check(http, session, payload, True)).json()['state'] == 'COMMITTED'
        assert (await close(http, session, payload)).json()['supplier_invoice']['id'] == proof['supplier_invoice']['id']
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 1
    assert len(await native.legs(db)) == 2
    assert await db.accounting_journal_groups_v2.count_documents({'txn_type':'supplier_invoice'}) == 1
    assert not native.LEGACY.intersection(commands.collections)
