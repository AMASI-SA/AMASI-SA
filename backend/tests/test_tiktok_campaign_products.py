import pytest
from integrations_control_center import tiktok_campaign_products as target
from integrations_control_center.tiktok_native_reporting import TikTokReportingError

class Collection:
    def __init__(self, row=None): self.row=row; self.queries=[]
    async def find_one(self, query, projection=None, **kwargs):
        self.queries.append(query)
        if self.row and all(self.row.get(k)==v for k,v in query.items()): return dict(self.row)
        return None
class DB(dict): pass

def setup(monkeypatch, *, catalog_owner='owner', campaign_owner=True):
    db=DB(salla_products=Collection({'user_id':catalog_owner,'product_id':'123','name':'هودي','url':'https://amasi-sa.com/ar/hoodie/p123'}), mezan_campaign_product_links_v2=Collection())
    calls=[]
    async def workspace(db,user_id,**kwargs):
        return {'entities':[{'entity_id':'campaign','account_id':'account'}] if campaign_owner else []}
    async def attach(db,user_id,association,**kwargs):
        calls.append((user_id,association,kwargs)); return {'event_id':'event'}
    async def indexes(db): pass
    monkeypatch.setattr(target,'tiktok_workspace',workspace)
    monkeypatch.setattr(target,'attach_campaign_product',attach)
    monkeypatch.setattr(target,'ensure_campaign_product_association_indexes',indexes)
    return db,calls

def payload(**kw):
    return target.TikTokProductLinkInput(account_id='account',campaign_id='campaign',product_id='123',**kw)

@pytest.mark.asyncio
async def test_preview_does_not_write(monkeypatch):
    db,calls=setup(monkeypatch)
    r=await target.link_campaign_product(db,'owner',payload())
    assert r['status']=='preview' and r['product']['id']=='123'
    assert calls==[]
    assert db['salla_products'].queries==[{'user_id':'owner','product_id':'123'}]

@pytest.mark.asyncio
async def test_confirm_uses_verified_manual_provenance(monkeypatch):
    db,calls=setup(monkeypatch)
    r=await target.link_campaign_product(db,'owner',payload(confirm=True,idempotency_key='request-123'))
    assert r['status']=='linked'
    assert calls[0][0]=='owner'
    a=calls[0][1]
    assert a['provider']=='tiktok' and a['product_name']=='هودي'
    assert a['evidence']['source']=='manual'
    assert a['evidence']['verification_status']=='verified'

@pytest.mark.asyncio
@pytest.mark.parametrize('catalog_owner,campaign_owner',[('other',True),('owner',False)])
async def test_cross_scope_denied_before_write(monkeypatch,catalog_owner,campaign_owner):
    db,calls=setup(monkeypatch,catalog_owner=catalog_owner,campaign_owner=campaign_owner)
    with pytest.raises(TikTokReportingError):
        await target.link_campaign_product(db,'owner',payload(confirm=True,idempotency_key='request-123'))
    assert not calls

@pytest.mark.asyncio
async def test_idempotent_retry_no_second_event(monkeypatch):
    db,calls=setup(monkeypatch)
    db['mezan_campaign_product_links_v2'].row={'user_id':'owner','idempotency_key':'request-123','provider':'tiktok','account_id':'account','campaign_id':'campaign','product_id':'123','state':'active','event_id':'prior'}
    r=await target.link_campaign_product(db,'owner',payload(confirm=True,idempotency_key='request-123'))
    assert r['event_id']=='prior' and not calls

@pytest.mark.asyncio
async def test_idempotency_cannot_be_reused_for_other_product(monkeypatch):
    db,calls=setup(monkeypatch)
    db['mezan_campaign_product_links_v2'].row={'user_id':'owner','idempotency_key':'request-123','provider':'tiktok','account_id':'account','campaign_id':'campaign','product_id':'456','state':'active'}
    with pytest.raises(TikTokReportingError) as e:
        await target.link_campaign_product(db,'owner',payload(confirm=True,idempotency_key='request-123'))
    assert e.value.status_code==409 and not calls

@pytest.mark.asyncio
@pytest.mark.parametrize('url',['https://evil.example/p123','https://amasi-sa.com/ar/x/p999','https://amasi-sa.com/ar/x/p123?token=secret'])
async def test_untrusted_catalog_destination_denied(monkeypatch,url):
    db,calls=setup(monkeypatch)
    db['salla_products'].row['url']=url
    with pytest.raises(TikTokReportingError):
        await target.link_campaign_product(db,'owner',payload(confirm=True,idempotency_key='request-123'))
    assert not calls

@pytest.mark.asyncio
async def test_route_requires_owner_and_rejects_injected_tenant(monkeypatch):
    import httpx
    from fastapi import FastAPI,APIRouter,HTTPException
    db,calls=setup(monkeypatch)
    role=['employee']
    async def user(): return {'id':'owner','role':role[0]}
    def owner(u):
        if u['role']!='owner': raise HTTPException(403)
        return u
    app=FastAPI(); router=APIRouter(prefix='/api/integrations-v2')
    target.attach_tiktok_product_routes(router,db,user,owner); app.include_router(router)
    body={'account_id':'account','campaign_id':'campaign','product_id':'123'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        r=await c.post('/api/integrations-v2/tiktok_ads/campaign-product',json=body)
        assert r.status_code==403 and not db['salla_products'].queries
        role[0]='owner'
        r=await c.post('/api/integrations-v2/tiktok_ads/campaign-product',json={**body,'user_id':'other'})
        assert r.status_code==422
        r=await c.post('/api/integrations-v2/tiktok_ads/campaign-product',json=body)
        assert r.status_code==200 and r.json()['status']=='preview'
    assert not calls
