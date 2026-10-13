import json
import httpx
import pytest
from external_storefront_pilot import collect_passive_pilot, TARGET


def payload(request):
    return dict(protocol='mezan-passive-product-v1',nonce=json.loads(request.content)['context']['nonce'],
        status='observed',bytes_read=600,blocked_requests=2,facts=dict(title='Product',heading='Name',description='',options=[],options_truncated=False))


@pytest.mark.asyncio
async def test_fixed_public_pilot_sends_no_account_metrics_or_url_parameter():
    def respond(request):
        data=json.loads(request.content)
        assert set(data['context'])=={'nonce'}
        assert request.headers['authorization']=='Bearer secret'
        assert 'secret' not in str(request.url)
        assert TARGET in data['code']
        assert 'setJavaScriptEnabled(false)' in data['code']
        return httpx.Response(200,json=payload(request))
    result=await collect_passive_pilot('secret',transport=httpx.MockTransport(respond))
    assert result['status']=='observed'
    assert result['dynamic_options_checked'] is False
    assert result['tracking_delivery_checked'] is False
    assert result['media_returned'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['nonce','extra','too_long','too_many','missing_facts','huge','mime','gzip','redirect'])
async def test_invalid_evidence_is_rejected_without_retry(bad):
    calls=[]
    def respond(request):
        calls.append(request)
        data=payload(request)
        if bad=='nonce': data['nonce']='wrong'
        if bad=='extra': data['cookies']='private'
        if bad=='too_long': data['facts']['options']=[dict(label='size',values=['x'*121])]
        if bad=='too_many': data['facts']['options']=[dict(label='size',values=[])]*11
        if bad=='missing_facts': data['facts']=None
        if bad=='huge': return httpx.Response(200,content=b' '*65537,headers={'content-type':'application/json'})
        if bad=='mime': return httpx.Response(200,text='login')
        if bad=='gzip': return httpx.Response(200,content=b'',headers={'content-type':'application/json','content-encoding':'gzip'})
        if bad=='redirect': return httpx.Response(302,headers={'location':'https://example.com/secret'})
        return httpx.Response(200,json=data)
    result=await collect_passive_pilot('secret',transport=httpx.MockTransport(respond))
    assert result['status'] in ('unexpected_response','service_unavailable')
    assert len(calls)==1
    assert 'secret' not in repr(result)


@pytest.mark.asyncio
async def test_no_token_no_session():
    def respond(request): pytest.fail('no session allowed')
    assert (await collect_passive_pilot('',transport=httpx.MockTransport(respond)))['status']=='not_configured'


@pytest.mark.asyncio
@pytest.mark.parametrize('code,status',[(400,'request_rejected'),(401,'access_denied'),(403,'access_denied'),(408,'timeout'),(429,'capacity_unavailable')])
async def test_provider_failures_have_safe_actionable_classification(code,status):
    result=await collect_passive_pilot('secret',transport=httpx.MockTransport(lambda request:httpx.Response(code,text='secret error')))
    assert result['status']==status
    assert 'secret' not in repr(result)
