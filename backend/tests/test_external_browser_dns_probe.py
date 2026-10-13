import json
import httpx
import pytest
from external_browser_dns_probe import probe_dns_policy, RULE


@pytest.mark.asyncio
@pytest.mark.parametrize('present,blocked,status',[(True,True,'verified'),(False,True,'unverified'),(True,False,'unverified')])
async def test_requires_rule_and_observed_dns_failure(present,blocked,status):
    def respond(request):
        data=json.loads(request.content)
        assert json.loads(request.url.params['launch'])['args']==[RULE]
        assert 'secret' not in str(request.url)
        assert set(data['context'])=={'nonce'}
        return httpx.Response(200,json=dict(protocol='mezan-dns-probe-v1',nonce=data['context']['nonce'],rule_present=present,dns_blocked=blocked,command_line_available=True))
    assert (await probe_dns_policy('secret',transport=httpx.MockTransport(respond)))['status']==status


@pytest.mark.asyncio
async def test_provider_rejection_sanitizes_body():
    result=await probe_dns_policy('secret',transport=httpx.MockTransport(lambda r:httpx.Response(400,text='secret')))
    assert result=={'status':'provider_rejected','http_status':400}
