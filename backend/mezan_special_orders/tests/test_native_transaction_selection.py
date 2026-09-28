"""Transaction discovery uses actual native piece/file keys, never client tenants."""
import os
import pytest
from fastapi import APIRouter, Depends, FastAPI
import httpx
from mezan_special_orders.tests.test_financial_integration import run
from mezan_special_orders.tests.test_core import OWNER
from mezan_special_orders.transactional_routes import local_request, bind_local_mutations
from preparation_piece_operations import PIECES
from preparation_file_registry import REGISTRY

pytestmark = pytest.mark.skipif(not os.getenv('MEZAN_SPECIAL_TEST_REPLICA_URI'), reason='Dedicated replica set required')


@pytest.mark.parametrize('selection,employee', [('piece',False),('file',False),('piece',True),('file',True)])
def test_native_ids_resolve_tenant_without_requiring_order_number(selection,employee):
    async def scenario(h):
        order = await h.new_order(partial=False)
        await h.raw[PIECES].insert_one({'user_id': OWNER.tenant_id, 'piece_id': 'native-piece',
            'file_number':'PF-SYNTHETIC', 'order_number':order['order_number']})
        user = ({'id':'employee-test','merchant_id':OWNER.tenant_id,'role':'employee'} if employee
                else {'id':OWNER.tenant_id,'role':'owner'})
        fields={'piece_id':'native-piece'} if selection=='piece' else {'file_number':'PF-SYNTHETIC'}
        assert await local_request(h.db, {'user':user, **fields})
        foreign={'id':'other-merchant','role':'owner'}
        assert not await local_request(h.db, {'user':foreign, **fields})
    run(scenario)


def test_piece_only_native_wrapper_rolls_back_all_writes_and_releases_context():
    async def scenario(h):
        await h.raw.users.insert_one({'id':OWNER.tenant_id,'role':'owner'})
        order = await h.new_order(partial=False)
        await h.raw[PIECES].insert_one({'user_id':OWNER.tenant_id,'piece_id':'native-piece','order_number':order['order_number'],'state':'before'})
        router = APIRouter()
        def current():return {'id':OWNER.tenant_id,'role':'owner'}
        @router.post('/pieces/{piece_id}')
        async def native(piece_id:str,user:dict=Depends(current)):
            assert h.db.session is not None
            await h.db[PIECES].update_one({'piece_id':piece_id},{'$set':{'state':'after'}})
            await h.db['synthetic_native_events'].insert_one({'piece_id':piece_id})
            raise RuntimeError('synthetic native failure')
        app=FastAPI();app.include_router(bind_local_mutations(router,h.db))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test') as client:
            with pytest.raises(RuntimeError,match='synthetic native failure'):
                await client.post('/pieces/native-piece')
        assert h.db.session is None
        assert (await h.raw[PIECES].find_one({'piece_id':'native-piece'}))['state']=='before'
        assert await h.raw.synthetic_native_events.count_documents({})==0
    run(scenario)


def test_revoked_native_role_cannot_use_cached_owner_session():
    async def scenario(h):
        order=await h.new_order(partial=False)
        await h.raw.users.insert_one({'id':OWNER.tenant_id,'role':'owner','disabled':True})
        await h.raw[PIECES].insert_one({'user_id':OWNER.tenant_id,'piece_id':'revoked-piece','order_number':order['order_number']})
        router=APIRouter()
        @router.post('/pieces/{piece_id}')
        async def native(piece_id:str,user:dict=Depends(lambda:{'id':OWNER.tenant_id,'role':'owner'})):
            raise AssertionError('Revoked session must not reach the mutation')
        app=FastAPI();app.include_router(bind_local_mutations(router,h.db))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test') as c:
            response=await c.post('/pieces/revoked-piece')
            assert response.status_code==403,response.text
    run(scenario)
