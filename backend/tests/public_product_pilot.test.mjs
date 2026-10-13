import test from 'node:test';
import assert from 'node:assert/strict';
import worker, {TARGET} from '../browser_workers/public_product_pilot.mjs';

function fixture({status=200,mime='text/html',size=100,url=TARGET,subresource=false}={}) {
  const calls=[]; let listener; let close=false; let scriptDisabled=false;
  const facts={title:'test',heading:'test',description:'',options:[],options_truncated:false};
  let settle;
  const completed=new Promise(r=>settle=r);
  const cdp={on:(name,fn)=>{listener=fn;},send:async(name,args)=>{
    calls.push([name,args]);
    if(name==='Page.getFrameTree') return {frameTree:{frame:{id:'main'}}};
    if(name==='Fetch.continueRequest') {
      listener({requestId:'doc',responseStatusCode:status,responseHeaders:[{name:'Content-Type',value:mime}],request:{url:new URL(url).href}});
    }
    if(name==='Fetch.takeResponseBodyAsStream') return {stream:'body'};
    if(name==='IO.read') return {data:'x'.repeat(size),eof:true};
    if(name==='Fetch.fulfillRequest' || (name==='Fetch.failRequest' && args.requestId==='doc')) settle();
    return {};
  }};
  const page={setJavaScriptEnabled:async enabled=>{scriptDisabled=!enabled;},setBypassServiceWorker:async()=>{},setCacheEnabled:async()=>{},createCDPSession:async()=>cdp,
    goto:async()=>{assert.equal(scriptDisabled,true);listener({requestId:'doc',frameId:'main',resourceType:'Document',request:{url:new URL(url).href,method:'GET'}}); if(subresource) listener({requestId:'media',frameId:'main',resourceType:'Image',request:{url:'https://example.com/image.png',method:'GET'}});await completed;},
    url:()=>new URL(url).href,evaluate:async()=>facts};
  const browser={createBrowserContext:async()=>({newPage:async()=>page,close:async()=>{close=true;}})};
  return {browser,calls,closed:()=>close};
}

test('one HTML document, no media, deny-all CSP, context closed',async()=>{
  const f=fixture({subresource:true}); const r=await worker({page:{browser:()=>f.browser},context:{nonce:'n'}});
  assert.equal(r.status,'observed');assert.equal(r.blocked_requests,1);assert.equal(f.closed(),true);
  assert.equal(f.calls.filter(([n])=>n==='Fetch.continueRequest').length,1);
  const response=f.calls.find(([n])=>n==='Fetch.fulfillRequest')[1];
  assert.match(response.responseHeaders.find(h=>h.name==='Content-Security-Policy').value,/default-src 'none'/);
  assert.equal(f.calls.find(([n,a])=>n==='Fetch.failRequest' && a.requestId==='media')[1].errorReason,'BlockedByClient');
});
for(const opts of [{status:302},{mime:'video/mp4'},{size:1000001},{url:'https://127.0.0.1/'}]) {
  test(`reject ${JSON.stringify(opts)}`,async()=>{
    const f=fixture(opts);const r=await worker({page:{browser:()=>f.browser},context:{nonce:'n'}});
    assert.notEqual(r.status,'observed');assert.equal(r.facts,null);assert.equal(f.closed(),true);
    assert.equal(f.calls.filter(([n])=>n==='Fetch.fulfillRequest').length,0);
  });
}
