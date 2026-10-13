import json
import httpx
import pytest
from external_amasi_storefront import canonical_amasi_product_url, collect_amasi_product

@pytest.mark.parametrize('url', ['https://evil.com/p123','https://amasi-sa.com.evil.com/p123',
    'https://user@amasi-sa.com/p123','http://amasi-sa.com/p123','https://amasi-sa.com:443/p123',
    'https://amasi-sa.com/p123?redirect=x','https://amasi-sa.com/p123#x',
    'https://amasi-sa.com/ar/%2e%2e/p123','https://amasi-sa.com/ar/%252f/p123',
    'https://amasi-sa.com/ar/name/p124','https://amasi-sa.com/cart',
    'https://amasi-sa.com/ar/a%0Ab/p123'])
def test_reject_untrusted_or_mismatched_target(url):
    assert canonical_amasi_product_url(url, '123') is None

def test_accept_owner_catalog_product_not_arbitrary_pages():
    assert canonical_amasi_product_url('https://amasi-sa.com/ar/منتج/p123', '123') == 'https://amasi-sa.com/ar/%D9%85%D9%86%D8%AA%D8%AC/p123'

@pytest.mark.asyncio
async def test_scope_denied_before_network(monkeypatch):
    monkeypatch.setenv('MEZAN_AMASI_BROWSER_OWNER_ID','owner')
    def forbidden(request): pytest.fail('network forbidden')
    for owner,url,pid in [('other','https://amasi-sa.com/p123','123'),('owner','https://evil.com/p123','123'),('owner','https://amasi-sa.com/p124','123')]:
        result=await collect_amasi_product(owner,pid,url,token='secret',transport=httpx.MockTransport(forbidden))
        assert result['performed'] is False

@pytest.mark.asyncio
async def test_only_url_nonce_leave_server_and_result_is_bounded(monkeypatch):
    monkeypatch.setenv('MEZAN_AMASI_BROWSER_OWNER_ID','owner')
    def respond(request):
        data=json.loads(request.content)
        assert set(data['context']) == {'nonce','target'}
        assert data['context']['target']=='https://amasi-sa.com/p123'
        assert 'owner' not in json.dumps(data['context'])
        return httpx.Response(200,json=dict(protocol='mezan-passive-product-v1',nonce=data['context']['nonce'],
            status='observed',bytes_read=100,blocked_requests=0,facts=dict(title='P',heading='P',description='',options=[],options_truncated=False)))
    result=await collect_amasi_product('owner','123','https://amasi-sa.com/p123',token='secret',transport=httpx.MockTransport(respond))
    assert result['performed'] is True
    assert result['dynamic_options_checked'] is False
    assert result['tracking_delivery_checked'] is False
    assert result['media_returned'] is False
    assert result['product_id']=='123'
    assert result['observed_at']

@pytest.mark.asyncio
async def test_enrichment_has_one_visit_and_no_cross_product_success(monkeypatch):
    import campaign_amasi_research as research
    monkeypatch.setenv('MEZAN_AMASI_BROWSER_OWNER_ID','owner')
    calls=[]
    async def collect(user,pid,url):
        calls.append((user,pid,url))
        return dict(status='observed',performed=True,product_id=pid)
    monkeypatch.setattr(research,'collect_amasi_product',collect)
    first={'product_id':'123','canonical_product_url':'https://amasi-sa.com/p123'}
    second={'product_id':'124','canonical_product_url':'https://amasi-sa.com/p124'}
    pack={'entities':{'campaign1':{'products':[first]},'campaign2':{'products':[second]}}}
    await research.enrich_amasi_products('owner',pack)
    assert len(calls)==1
    assert research.research_status_for_products([first])['performed'] is True
    assert research.research_status_for_products([second])['performed'] is False
    assert research.research_status_for_pack(pack)['products_observed']==1

@pytest.mark.asyncio
async def test_disabled_owner_never_enriches(monkeypatch):
    import campaign_amasi_research as research
    monkeypatch.setenv('MEZAN_AMASI_BROWSER_OWNER_ID','owner')
    async def forbidden(*args): pytest.fail('no cross-owner collection')
    monkeypatch.setattr(research,'collect_amasi_product',forbidden)
    pack={'entities':{'a':{'products':[{'product_id':'123','canonical_product_url':'https://amasi-sa.com/p123'}]}}}
    await research.enrich_amasi_products('other',pack)
    assert research.research_status_for_pack(pack)['performed'] is False

def test_compaction_retains_observation():
    from campaign_ai_evidence_v3 import _compact_product
    evidence={'status':'observed','performed':True,'product_id':'123'}
    assert _compact_product({'storefront_observation':evidence})['storefront_observation']==evidence
