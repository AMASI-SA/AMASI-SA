"""Actual pause barriers on disposable Mongo; no operational deployment claims."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import os

import pytest
from pydantic import ValidationError

from mezan_special_orders.binding import (Enablement, SpecialOrdersDatabase, transaction,
                                          _atomic_transaction)
from mezan_special_orders.domain import DomainError
from mezan_special_orders.write_control import (AUDIT, CONTROL, SCOPES, ControlChange,
    ScopeFlags, admit, capabilities, change, state)
from mezan_special_orders.tests.test_core import OWNER, request_data
from mezan_special_orders.tests.test_financial_integration import run, movement
from mezan_special_orders.tests.test_http_integration import api, create as http_create, policy

pytestmark = pytest.mark.skipif(not os.getenv('MEZAN_SPECIAL_TEST_REPLICA_URI'),
                               reason='Dedicated disposable replica set required')


def request(revision, *, enabled=None, **overrides):
    flags = {s: True for s in SCOPES} if enabled is None else dict(enabled)
    flags.update(overrides)
    return ControlChange(expected_revision=revision, enabled=ScopeFlags(**flags),
                         reason='Synthetic owner control acceptance')


async def owner(h):
    await h.raw.users.update_one({'id': OWNER.tenant_id},
        {'$set': {'role': 'owner', 'is_active': True, 'disabled': False}}, upsert=True)


async def set_flags(h, key='control-pause-001', **flags):
    await owner(h)
    current = await state(h.db, OWNER.tenant_id)
    return await change(h.db, {'id': OWNER.tenant_id},
                        request(current['revision'], enabled=current['enabled'], **flags), key)


def test_missing_control_never_bootstraps_business_writer_or_hides_existing_reads():
    async def scenario(h):
        o = await h.new_order(partial=False)
        await h.raw[CONTROL].delete_many({})
        before = await h.doc(o)
        with pytest.raises(DomainError, match='special_write_control_uninitialized'):
            await h.new_order(key='create-when-missing')
        assert await h.raw[CONTROL].count_documents({}) == 0
        assert await h.doc(o) == before
        assert (await h.service.get(OWNER, o['order_id']))['order_id'] == o['order_id']
        assert (await capabilities(h.db, OWNER.tenant_id))['effective'] == {s:False for s in SCOPES}
    run(scenario)


@pytest.mark.parametrize('corruption', [
    {'enabled.financial': 1}, {'enabled.workflow': None}, {'schema_version': True},
    {'write_epoch': False}, {'revision': 0}, {'activity_seq': -1}, {'changed_by': 'another-owner'},
    {'enabled': {}}, {'enabled.unknown': True}, {'last_command_id': None},
])
def test_malformed_control_fails_closed_without_business_state(corruption):
    async def scenario(h):
        await h.raw[CONTROL].update_one({'_id':OWNER.tenant_id}, {'$set':corruption})
        count = await h.raw.mezan_special_orders_v1.count_documents({})
        with pytest.raises(DomainError, match='special_write_control_invalid'):
            await h.new_order()
        assert await h.raw.mezan_special_orders_v1.count_documents({}) == count
    run(scenario)


def test_owner_initializes_only_paused_state_and_open_requires_separate_revision():
    async def scenario(h):
        await owner(h)
        await h.raw[CONTROL].delete_many({})
        with pytest.raises(DomainError, match='control_initialization_must_be_paused'):
            await change(h.db, {'id':OWNER.tenant_id}, request(0), 'invalid-open-initialize')
        assert await h.raw[CONTROL].count_documents({}) == 0
        paused = await change(h.db, {'id':OWNER.tenant_id},
            request(0, enabled={s:False for s in SCOPES}), 'initialize-paused')
        assert paused['control']['revision'] == 1 and not any(paused['control']['enabled'].values())
        with pytest.raises(DomainError, match='special_writes_paused'):
            await h.new_order()
        opened = await change(h.db, {'id':OWNER.tenant_id}, request(1), 'explicit-open-second')
        assert opened['control']['revision'] == 2 and opened['control']['write_epoch'] == 2
        assert (await h.new_order())['stage'] == 'pending_review'
        assert await h.raw[AUDIT].count_documents({}) == 2
    run(scenario)


def test_paused_static_instance_can_initialize_control_but_not_activate_static_gates():
    async def scenario(h):
        await owner(h)
        paused_db=SpecialOrdersDatabase(h.raw,Enablement(reads=True))
        await h.raw[CONTROL].delete_many({})
        await change(paused_db,{'id':OWNER.tenant_id},request(0,enabled={s:False for s in SCOPES}),'static-init-off')
        await change(paused_db,{'id':OWNER.tenant_id},request(1),'dynamic-on-not-static')
        assert not any((await capabilities(paused_db,OWNER.tenant_id))['effective'].values())
        async def forbidden(_): raise AssertionError('Static commands must remain OFF')
        with pytest.raises(DomainError,match='special_order_commands_disabled'):
            await transaction(paused_db,forbidden,tenant_id=OWNER.tenant_id,scopes=frozenset({'workflow'}))
    run(scenario)


@pytest.mark.parametrize('actor', [
    {'id':'ordinary-account','role':'employee','created_by':'test-store'},
    {'id':'ordinary-account','role':'accountant','created_by':'test-store'},
    {'id':'ordinary-account','role':'operations','created_by':'test-store'},
])
def test_control_cannot_be_approved_by_forged_owner_or_nonowner(actor):
    async def scenario(h):
        await owner(h);await h.raw.users.insert_one(actor)
        before = await h.raw[CONTROL].find_one({'_id':OWNER.tenant_id})
        with pytest.raises(DomainError,match='write_control_owner_required'):
            await change(h.db,{'id':actor['id'],'role':'owner','is_owner':True},request(1),'forged-owner-command')
        assert await h.raw[CONTROL].find_one({'_id':OWNER.tenant_id}) == before
        assert await h.raw[AUDIT].count_documents({}) == 0
    run(scenario)


def test_disabled_owner_cannot_resume_from_cached_session():
    async def scenario(h):
        await owner(h)
        await h.raw.users.update_one({'id':OWNER.tenant_id},{'$set':{'disabled':True}})
        with pytest.raises(DomainError,match='active_user_required'):
            await change(h.db,{'id':OWNER.tenant_id,'role':'owner'},request(1),'revoked-owner-command')
        assert await h.raw[AUDIT].count_documents({}) == 0
    run(scenario)


def test_creation_pause_keeps_existing_receipts_and_workflow_accessible():
    async def scenario(h):
        o = await h.new_order()
        before = await h.doc(o)
        await set_flags(h, creation=False)
        with pytest.raises(DomainError,match='special_writes_paused'):
            await h.new_order(key='forbidden-new-order')
        # An existing order can still receive documented payment evidence.
        updated, ev = await h.claim(o)
        assert updated['balances']['remaining_minor'] == 6000
        assert await h.evidence.get(OWNER.tenant_id,ev.object_id)
        assert (await h.doc(updated))['order_id'] == before['order_id']
        assert await h.raw.general_ledger.count_documents({}) == 0
    run(scenario)


def test_financial_pause_rolls_back_whole_posting_without_losing_receipt():
    async def scenario(h):
        o,ev=await h.claim(await h.new_order())
        before=await h.doc(o)
        await set_flags(h,financial=False)
        with pytest.raises(DomainError,match='special_writes_paused'):
            await h.fin_command(o,'bank_collection',{
                'receipt_claim_id':o['receipt_claims'][0]['claim_id'],'movement':movement(ev)},'financial-blocked')
        assert await h.doc(o) == before
        assert await h.raw.general_ledger.count_documents({}) == 0
        assert await h.raw.account_transactions.count_documents({}) == 0
        assert (await h.evidence.get(OWNER.tenant_id,ev.object_id))['sha256'] == ev.sha256
    run(scenario)


def test_pause_of_evidence_stops_upload_but_does_not_destroy_old_blob():
    async def scenario(h):
        await set_flags(h,evidence=False)
        before=await h.raw.mezan_special_order_evidence_v1.count_documents({})
        with pytest.raises(DomainError,match='special_writes_paused'):
            await h.evidence.upload(OWNER.tenant_id,OWNER.actor_id,kind='bank_receipt',content_type='image/png',data=h.png)
        assert await h.raw.mezan_special_order_evidence_v1.count_documents({}) == before
        assert await h.evidence.get(OWNER.tenant_id,h.policy_ev.object_id)
    run(scenario)


def test_unfenced_and_nested_scope_escalation_do_not_write():
    async def scenario(h):
        with pytest.raises(DomainError,match='special_order_unfenced_write_forbidden'):
            await h.db.mezan_special_unscoped_v1.insert_one({'tenant_id':OWNER.tenant_id})
        async def financial(_): raise AssertionError('Scope escalation executed')
        async def workflow(scoped):
            with pytest.raises(DomainError,match='nested_write_scope_escalation_forbidden'):
                await transaction(scoped,financial,tenant_id=OWNER.tenant_id,scopes=frozenset({'financial'}))
            with pytest.raises(DomainError,match='nested_write_admission_scope_mismatch'):
                await transaction(scoped,financial,tenant_id='another-store',scopes=frozenset({'workflow'}))
            with pytest.raises(DomainError,match='special_order_unfenced_write_forbidden'):
                await scoped[CONTROL].update_one({'_id':OWNER.tenant_id},{'$set':{'enabled.financial':True}})
        await transaction(h.db,workflow,tenant_id=OWNER.tenant_id,scopes=frozenset({'workflow'}))
        assert await h.raw.mezan_special_unscoped_v1.count_documents({}) == 0
    run(scenario)


def test_same_owner_control_request_is_idempotent_and_conflicting_body_rejected():
    async def scenario(h):
        await owner(h)
        req=request(1,creation=False)
        results=await asyncio.gather(*(change(h.db,{'id':OWNER.tenant_id},req,'same-control-replay') for _ in range(4)))
        assert {r['applied_revision'] for r in results} == {2}
        assert await h.raw[AUDIT].count_documents({}) == 1
        with pytest.raises(DomainError,match='idempotency_payload_conflict'):
            await change(h.db,{'id':OWNER.tenant_id},request(1,financial=False),'same-control-replay')
        assert (await state(h.db,OWNER.tenant_id))['revision'] == 2
    run(scenario)


def test_different_concurrent_control_requests_have_one_revision_winner():
    async def scenario(h):
        await owner(h)
        results=await asyncio.gather(change(h.db,{'id':OWNER.tenant_id},request(1,creation=False),'race-control-a'),
            change(h.db,{'id':OWNER.tenant_id},request(1,financial=False),'race-control-b'),return_exceptions=True)
        assert sum(isinstance(r,DomainError) for r in results) == 1,results
        assert await h.raw[AUDIT].count_documents({}) == 1
    run(scenario)


def test_committed_pause_waits_for_previously_admitted_writer_and_rejects_stale_worker():
    async def scenario(h):
        await owner(h)
        old_instance=SpecialOrdersDatabase(h.raw,Enablement(True,True,True,True))
        admitted,release=asyncio.Event(),asyncio.Event()
        async def writing(scoped):
            await scoped.mezan_special_orders_v1.insert_one({'tenant_id':OWNER.tenant_id,'id':'one'})
            admitted.set()
            await asyncio.wait_for(release.wait(),3)
        writer=asyncio.create_task(transaction(old_instance,writing,tenant_id=OWNER.tenant_id,scopes=frozenset({'workflow'})))
        await asyncio.wait_for(admitted.wait(),3)
        pause=asyncio.create_task(change(h.db,{'id':OWNER.tenant_id},request(1,workflow=False),'pause-race-barrier'))
        await asyncio.sleep(0.04)
        assert not pause.done(), 'Pause must not claim a barrier before admitted writes commit/abort'
        release.set();await asyncio.wait_for(writer,5);result=await asyncio.wait_for(pause,5)
        assert not result['control']['enabled']['workflow']
        before=await h.raw.mezan_special_orders_v1.count_documents({})
        async def forbidden(_):raise AssertionError('Cached static enablement bypassed central pause')
        with pytest.raises(DomainError,match='special_writes_paused'):
            await transaction(old_instance,forbidden,tenant_id=OWNER.tenant_id,scopes=frozenset({'workflow'}))
        assert before == await h.raw.mezan_special_orders_v1.count_documents({}) == 1
    run(scenario)


def test_aborted_inflight_transaction_leaves_no_half_state_before_pause():
    async def scenario(h):
        await owner(h)
        admitted,release=asyncio.Event(),asyncio.Event()
        async def failing(scoped):
            await scoped.mezan_special_orders_v1.insert_one({'tenant_id':OWNER.tenant_id,'id':'must-rollback'})
            await scoped.general_ledger.insert_one({'user_id':OWNER.tenant_id,'id':'must-rollback-too'})
            admitted.set();await release.wait();raise DomainError('synthetic_abort')
        writer=asyncio.create_task(transaction(h.db,failing,tenant_id=OWNER.tenant_id,scopes=frozenset({'workflow','financial'})))
        await asyncio.wait_for(admitted.wait(),3)
        pause=asyncio.create_task(change(h.db,{'id':OWNER.tenant_id},request(1,enabled={s:False for s in SCOPES}),'abort-then-pause'))
        await asyncio.sleep(0.03);release.set()
        with pytest.raises(DomainError,match='synthetic_abort'):await writer
        await asyncio.wait_for(pause,5)
        assert await h.raw.mezan_special_orders_v1.count_documents({}) == 0
        assert await h.raw.general_ledger.count_documents({}) == 0
    run(scenario)


def test_real_stale_snapshot_cannot_upgrade_into_admission_after_pause_commit():
    async def scenario(h):
        from pymongo.errors import OperationFailure
        from pymongo.read_concern import ReadConcern
        await owner(h)
        async with await h.raw.client.start_session() as session:
            session.start_transaction(read_concern=ReadConcern('snapshot'))
            await h.raw[CONTROL].find_one({'_id':OWNER.tenant_id},session=session)
            await change(h.db,{'id':OWNER.tenant_id},request(1,workflow=False),'pause-after-old-snapshot')
            with pytest.raises(OperationFailure) as error:
                await admit(h.db.in_session(session),OWNER.tenant_id,frozenset({'workflow'}))
            assert error.value.has_error_label('TransientTransactionError'),error.value
            await session.abort_transaction()
    run(scenario)


def test_reenabled_instance_rejects_prior_epoch_job_instead_of_replaying_it_blindly():
    async def scenario(h):
        await set_flags(h,workflow=False)
        await set_flags(h,key='reopen-new-epoch',workflow=True)
        current=await state(h.db,OWNER.tenant_id)
        async def write(scoped):
            await scoped.mezan_special_orders_v1.insert_one({'tenant_id':OWNER.tenant_id})
        with pytest.raises(DomainError,match='special_write_epoch_stale'):
            await transaction(h.db,write,tenant_id=OWNER.tenant_id,scopes=frozenset({'workflow'}),expected_epoch=1)
        await transaction(h.db,write,tenant_id=OWNER.tenant_id,scopes=frozenset({'workflow'}),expected_epoch=current['write_epoch'])
        assert await h.raw.mezan_special_orders_v1.count_documents({}) == 1
    run(scenario)


def test_uninitialized_http_control_reports_closed_and_config_commands_cannot_bypass_pause():
    async def scenario(h):
        async with api(h) as c:
            existing=await http_create(c,h)
            r=await c.put('/api/special-orders-v1/write-control',json=request(1,enabled={s:False for s in SCOPES}).model_dump(mode='json'),headers={'Idempotency-Key':'http-pause-all'})
            assert r.status_code == 200,r.text
            cap=await c.get('/api/special-orders-v1/capabilities')
            assert cap.status_code == 200 and not cap.json()['commands_enabled']
            read=await c.get('/api/special-orders-v1/'+existing['order_id'])
            assert read.status_code == 200,read.text
            write=await c.post('/api/special-orders-v1/accounting-policies',json=policy(h))
            assert write.status_code == 423,write.text
            upload=await c.post('/api/special-orders-v1/evidence',data={'kind':'bank_receipt'},files={'file':('x.png',h.png,'image/png')})
            assert upload.status_code == 423,upload.text
            assert await h.raw.general_ledger.count_documents({}) == 0
    run(scenario)


def test_deleted_control_with_audit_cannot_reset_epoch_and_revalidate_old_jobs():
    async def scenario(h):
        await set_flags(h,workflow=False)
        await h.raw[CONTROL].delete_many({})
        with pytest.raises(DomainError,match='write_control_history_without_state'):
            await change(h.db,{'id':OWNER.tenant_id},request(0,enabled={s:False for s in SCOPES}),'dangerous-epoch-reset')
        assert await h.raw[CONTROL].count_documents({}) == 0
    run(scenario)


def test_collection_guard_rejects_read_shaped_mutations_and_unknown_writers():
    async def scenario(h):
        with pytest.raises(DomainError,match='write_aggregation_forbidden'):
            h.db.mezan_special_orders_v1.aggregate([{'$out':'another_collection'}])
        with pytest.raises(AttributeError):
            h.db.mezan_special_orders_v1.find_one_and_delete({'tenant_id':OWNER.tenant_id})
        with pytest.raises(AttributeError):
            h.db.mezan_special_orders_v1.drop()
        async def admitted(scoped):
            with pytest.raises(DomainError,match='special_collection_scope_not_admitted'):
                await scoped.mezan_special_unknown_future_collection.insert_one({'tenant_id':OWNER.tenant_id})
            with pytest.raises(DomainError,match='special_collection_scope_not_admitted'):
                await scoped.mezan_special_order_financial_events_v1.insert_one({'tenant_id':OWNER.tenant_id})
        await transaction(h.db,admitted,tenant_id=OWNER.tenant_id,scopes=frozenset({'workflow'}))
    run(scenario)


def test_native_supplier_close_obeys_pause_for_entire_mixed_invoice():
    async def scenario(h):
        from mezan_special_orders.tests.test_native_supplier_integration import (reviewed_order,seed_receiving,native_client)
        from supplier_receiving_routes import SESSIONS, SUPPLIER_INVOICES, PIECES
        o=await reviewed_order(h)
        session,body=await seed_receiving(h,o,ordinary=True)
        before=await h.raw[SESSIONS].find_one({'id':session['id']})
        piece_before=await h.raw[PIECES].find({'supplier_receiving_session_id':session['id']}).sort('piece_id',1).to_list(10)
        await set_flags(h,financial=False)
        async with native_client(h.db) as c:
            response=await c.post('/supplier-receiving-v1/sessions/'+session['id']+'/close',json=body)
            assert response.status_code == 423,response.text
        assert await h.raw[SESSIONS].find_one({'id':session['id']}) == before
        assert await h.raw[PIECES].find({'supplier_receiving_session_id':session['id']}).sort('piece_id',1).to_list(10) == piece_before
        assert await h.raw[SUPPLIER_INVOICES].count_documents({}) == 0
        assert await h.raw.general_ledger.count_documents({}) == 0
    run(scenario)


def test_native_stream_is_recreated_for_actual_driver_transaction_retry():
    async def scenario(h):
        import httpx
        from fastapi import APIRouter, FastAPI, UploadFile, File, Depends
        from pymongo.errors import OperationFailure
        from mezan_special_orders.transactional_routes import bind_local_mutations
        from preparation_piece_operations import PIECES
        await owner(h)
        o=await h.new_order(partial=False)
        await h.raw[PIECES].insert_one({'user_id':OWNER.tenant_id,'piece_id':'retry-piece','order_number':o['order_number']})
        seen=[]
        router=APIRouter()
        @router.post('/pieces/{piece_id}/upload')
        async def upload(piece_id:str,file:UploadFile=File(...),user:dict=Depends(lambda:{'id':OWNER.tenant_id,'role':'owner'})):
            body=await file.read();await file.close();seen.append(body)
            await h.db.mezan_special_orders_v1.update_one({'tenant_id':OWNER.tenant_id,'order_id':o['order_id']},{'$set':{'synthetic_retry_body':body.decode()}})
            if len(seen)==1:
                raise OperationFailure('Injected transient transaction failure',code=112,details={'errorLabels':['TransientTransactionError']})
            return {'ok':True}
        app=FastAPI();app.include_router(bind_local_mutations(router,h.db))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test') as c:
            r=await c.post('/pieces/retry-piece/upload',files={'file':('test.txt',b'bounded-original-bytes','text/plain')})
            assert r.status_code==200,r.text
        assert seen==[b'bounded-original-bytes',b'bounded-original-bytes']
        assert (await h.doc(o))['synthetic_retry_body']=='bounded-original-bytes'
    run(scenario)


def test_every_transaction_call_in_the_special_package_declares_tenant_and_scopes():
    import ast
    from pathlib import Path
    package=Path(__file__).resolve().parents[1]
    call_count=0
    for path in package.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='transaction':
                assert {'tenant_id','scopes'}.issubset(k.arg for k in node.keywords),(path.name,node.lineno)
                call_count+=1
    assert call_count>=15


def test_admitted_writer_cannot_delete_financial_or_order_history():
    async def scenario(h):
        o=await h.new_order(partial=False)
        before=await h.doc(o)
        async def attempt(scoped):
            for method in ('delete_one','delete_many'):
                with pytest.raises(DomainError,match='special_history_deletion_forbidden'):
                    await getattr(scoped.mezan_special_orders_v1,method)({'tenant_id':OWNER.tenant_id})
        await transaction(h.db,attempt,tenant_id=OWNER.tenant_id,scopes=frozenset({'workflow'}))
        assert await h.doc(o)==before
    run(scenario)
