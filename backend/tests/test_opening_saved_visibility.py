import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch
import httpx
from fastapi import FastAPI, HTTPException
from operational_balance_routes import make_operational_balance_router
from operational_balance_service import save_opening

ROW = dict(id='one', party_type='employee', party_id='khaled', name='خالد', currency='SAR', amount='100.00', direction='for_party', actor_id='private-actor', approved_at='private-date')

def test_opening_context_details_require_manage_permission():
    async def scenario():
        for can_manage in [True, False]:
            async def principal(): return {'id':'owner'}
            async def scoped(db, user, permission):
                if permission == 'manage' and not can_manage: raise HTTPException(403)
                return {'id':'owner','role':'owner'}, 'owner', 'mezan2'
            app=FastAPI(); app.include_router(make_operational_balance_router(object(), principal))
            with patch('operational_balance_routes.scope', scoped), patch('operational_balance_routes.read', AsyncMock(return_value={'status':'draft','started_at':None,'openings':{'one':ROW}})):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                    result=await client.get('/operational-balances/context')
                    assert result.status_code == 200,result.text
                    data=result.json()
                    assert data['opening_count']==1
                    assert bool(data['openings']) == can_manage
                    if can_manage:
                        assert data['openings'][0]['name']=='خالد'
                        assert set(data['openings'][0])=={'id','party_type','party_id','name','currency','amount','direction'}
    asyncio.run(scenario())

def test_save_returns_confirmed_display_row_and_authoritative_count():
    async def scenario():
        state={'status':'draft','started_at':None,'openings':{}}
        async def mutate(db, owner, apply): return await apply(state)
        with patch('operational_balance_service.mutate',mutate), patch('operational_balance_service.replay',return_value=None), patch('operational_balance_service.accounting_inactive',AsyncMock()), patch('operational_balance_service.entity',AsyncMock(return_value={'name':'خالد'})), patch('operational_balance_service.audit'), patch('operational_balance_service.remember'):
            result=await save_opening(None,'owner','actor',dict(request_id='test-123',party_type='employee',party_id='khaled',currency='SAR',amount='100',direction='for_party'))
            assert result['opening_count']==1
            assert result['saved_opening']['name']=='خالد'
            assert result['saved_opening']['amount']=='100.00'
            assert 'actor_id' not in result['saved_opening']
    asyncio.run(scenario())
