"""Operator-only hosted resolver capability probe, no private network targets.

A successful negative probe is required before designing generic collection.
Uses one public owner-authorized target with DNS deliberately denied.
"""
import asyncio
import json
import secrets
import httpx
from external_browser_connection import ENDPOINT

RULE = '--host-resolver-rules=MAP * ^NOTFOUND'
CODE = '''export default async ({page: initialPage,context}) => {
  const browser = initialPage.browser();
  const isolated = await browser.createBrowserContext();
  try {
  const page = await isolated.newPage();
  const cdp = await page.createCDPSession();
  let rulePresent = false;
  let commandAvailable = false;
  try { const result = await cdp.send('Browser.getBrowserCommandLine');
    commandAvailable = true;
    rulePresent = result.arguments.includes('--host-resolver-rules=MAP * ^NOTFOUND');
  } catch {}
  await page.setJavaScriptEnabled(false);
  await page.setBypassServiceWorker(true);
  await page.setCacheEnabled(false);
  const {frameTree} = await cdp.send('Page.getFrameTree');
  let accepted = false;
  cdp.on('Fetch.requestPaused', async event => {
    const allow = !accepted && event.frameId === frameTree.frame.id && event.resourceType === 'Document'
      && event.request.method === 'GET' && event.request.url === 'https://amasi-sa.com/ar';
    if (allow) accepted = true;
    try { await cdp.send(allow ? 'Fetch.continueRequest' : 'Fetch.failRequest',
      allow ? {requestId:event.requestId} : {requestId:event.requestId,errorReason:'BlockedByClient'}); } catch {}
  });
  await cdp.send('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
  let dnsBlocked = false;
  try { await page.goto('https://amasi-sa.com/ar',{waitUntil:'domcontentloaded',timeout:10000}); }
  catch(error) {dnsBlocked = String(error).includes('ERR_NAME_NOT_RESOLVED');}
  return {protocol:'mezan-dns-probe-v1',nonce:context.nonce,rule_present:rulePresent,dns_blocked:dnsBlocked,command_line_available:commandAvailable};
  } finally {await isolated.close();}
};'''


async def probe_dns_policy(token, *, transport=None):
    if not token or token != token.strip() or any(ord(c)<33 or ord(c)>126 for c in token):
        return {'status':'not_configured'}
    nonce = secrets.token_hex(16)
    try:
        async with asyncio.timeout(25):
            async with httpx.AsyncClient(transport=transport,timeout=20,follow_redirects=False,trust_env=False) as client:
                async with client.stream('POST',ENDPOINT,
                    params={'timeout':'15000','launch':json.dumps({'record':False,'replay':False,'args':[RULE]})},
                    headers={'Authorization':f'Bearer {token}','Accept-Encoding':'identity'},
                    json={'code':CODE,'context':{'nonce':nonce}}) as response:
                    if response.status_code != 200:
                        return {'status':'provider_rejected','http_status':response.status_code}
                    if response.headers.get('content-type','').split(';')[0] != 'application/json' or response.headers.get('content-encoding','identity')!='identity':
                        return {'status':'invalid_response'}
                    body=bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=1024):
                        if len(body)+len(chunk)>8192: return {'status':'invalid_response'}
                        body.extend(chunk)
                    data=json.loads(body)
                    if isinstance(data,dict) and set(data)=={'data','type'} and data['type']=='application/json': data=data['data']
                    if not isinstance(data,dict) or set(data)!={'protocol','nonce','rule_present','dns_blocked','command_line_available'} or data['protocol']!='mezan-dns-probe-v1' or data['nonce']!=nonce or type(data['rule_present']) is not bool or type(data['dns_blocked']) is not bool or type(data['command_line_available']) is not bool:
                        return {'status':'invalid_response'}
                    return {'status':'verified' if data['rule_present'] and data['dns_blocked'] else 'unverified',
                            'rule_present':data['rule_present'],'dns_blocked':data['dns_blocked'],'command_line_available':data['command_line_available']}
    except (TimeoutError,httpx.HTTPError,ValueError):
        return {'status':'unavailable'}
