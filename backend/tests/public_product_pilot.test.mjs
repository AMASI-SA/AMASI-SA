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

import {extractProductFacts} from '../browser_workers/public_product_pilot.mjs';
function extract(attribute) {
  const prior = globalThis.document;
  globalThis.document = {
    title:'Product',
    querySelector(selector) {
      if(selector === '#product-form') return {
        querySelectorAll:()=>[],
        querySelector:s=>s==='h2' ? {textContent:'139 SAR'} : attribute == null ? null : {getAttribute:()=>attribute},
      };
      return null;
    },
  };
  try {return extractProductFacts();} finally {globalThis.document=prior;}
}

test('embedded Salla definitions preserve zero and conditional options without returning images/input values',()=>{
  const facts=extract(JSON.stringify([
    {id:1,name:'size',type:'single-option',required:true,details:[
      {id:2,name:'child',additional_price:0,is_out:false,image:'https://private.invalid/media'},
      {id:3,name:'adult',additional_price:10,is_out:true},
    ]},
    {id:4,name:'message',type:'text',required:true,visibility_condition:{option:1,value:2},value:'CUSTOMER INPUT',details:[]},
  ]));
  assert.equal(facts.option_definition_status,'observed');
  assert.deepEqual(facts.option_definitions[0].values.map(v=>v.additional_price),[0,10]);
  assert.equal(facts.option_definitions[1].conditional,true);
  assert.equal(facts.displayed_price_text,'139 SAR');
  assert.doesNotMatch(JSON.stringify(facts),/CUSTOMER INPUT|private.invalid/);
  assert.equal(facts.option_definitions[0].values[1].unavailable,true);
});

test('absent and malformed definitions remain unknown rather than no options',()=>{
  assert.equal(extract(null).option_definition_status,'unavailable');
  for(const value of ['{bad', '{}', 'x'.repeat(65537)]) {
    const facts=extract(value);
    assert.equal(facts.option_definition_status,'invalid');
    assert.deepEqual(facts.option_definitions,[]);
  }
});

test('definitions and values are bounded and truncation is explicit',()=>{
  const facts=extract(JSON.stringify(Array.from({length:21},()=>({id:1,name:'size',details:Array.from({length:21},(_,i)=>({id:2,name:i===0?'a'.repeat(200):'size',additional_price:10}))}))));
  assert.equal(facts.option_definition_status,'truncated');
  assert.equal(facts.option_definitions.length,20);
  assert.equal(facts.option_definitions[0].values.length,20);
  assert.equal(facts.option_definitions[0].values[0].label.length,120);
});
