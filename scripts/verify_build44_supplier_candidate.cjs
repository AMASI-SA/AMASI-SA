'use strict';
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.join(process.argv[2], 'frontend'), base = process.argv[3], sid = process.argv[4];
assert.equal(new URL(base).hostname, '127.0.0.1');
const ts = require(path.join(root, 'node_modules/typescript'));
class ApiError extends Error { constructor(status, data) { super('synthetic lost response'); this.status=status; this.data=data; } }
let posts = [], allowRecovery = false, lose = true, recoveryFailures = 0;
const api = {
  post: async (url, body) => {
    posts.push(body.client_request_id);
    const response = await fetch(base+url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new ApiError(response.status,data);
    if (posts.length === 26 && lose) { lose=false; throw new ApiError(0,null); }
    return data;
  },
  get: async url => {
    if (!allowRecovery) { recoveryFailures++; throw new ApiError(0,null); }
    const response = await fetch(base+url), data = await response.json();
    if (!response.ok) throw new ApiError(response.status,data);
    return data;
  },
};
function load(file) {
  const m={exports:{}};
  const req = name => {
    if(name==='@/src/api/client') return {apiClient:api,ApiError};
    if(name==='@/src/config/runtime') return {API_MODE:'real'};
    if(name==='expo-file-system') return {File:class{},Paths:{}};
    if(name.startsWith('./')) return load('src/features/myProducts/'+name.slice(2)+'.ts');
    throw Error('unexpected module '+name);
  };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root,file),'utf8'),{
    fileName:file,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}
  }).outputText,{exports:m.exports,module:m,require:req,Error,Map,Set,Date,Math,Object,Array,Number,String,Boolean,RegExp,JSON,Promise});
  return m.exports;
}
(async()=>{
  const R=load('src/features/myProducts/repository.ts'), {ReceivingQueue}=load('src/features/myProducts/receivingQueue.ts');
  let disk=null, seq=0;
  const deps={read:async()=>disk,write:async value=>{disk=value;},id:()=>`integrated-${++seq}`,
    request:(e,recoverOnly)=>R.scanReceivingPiece(sid,e.barcode,e.quantity,e.reassignment,
      {clientRequestId:e.id,clientExpiresAt:e.clientExpiresAt,recoverOnly}),
    pending:error=>error instanceof R.ReceivingScanPendingError};
  let q=new ReceivingQueue(deps); await q.hydrate(); q.setActive(true);
  const {SingleTargetScanGate} = load('src/components/mobile/scanning/scanGate.ts');
  const scanner = new SingleTargetScanGate(); scanner.ready(900);
  for(let n=1;n<=50;n++) {
    const code='MEZAN-PIECE:'+n.toString(16).padStart(32,'0');
    const event={type:'qr',data:code,raw:code}; // supported public Expo piece event
    const at=1000+n*1000;
    const decision=scanner.observe(event,at,{x:50,y:50,width:200,height:200},false,'supplier');
    assert.equal(decision.ticket?.value,code);
    await q.enqueue(decision.ticket.value,1);
    scanner.finish(decision.ticket);
    assert.equal(scanner.observe(event,at+100,{x:50,y:50,width:200,height:200},false,'supplier').ticket,undefined,'duplicate camera frame rejected');
  }
  for(let n=0;n<26;n++) await q.tick();
  assert.equal(q.snapshot().entries.filter(e=>e.state==='confirmed').length,25);
  assert.equal(q.snapshot().entries.filter(e=>e.state==='sent').length,1);
  assert.equal(q.snapshot().entries.filter(e=>e.state==='queued').length,24);
  assert.equal(q.unsettled(),true);
  assert.ok(recoveryFailures >= 1, 'offline read recovery exercised');
  const originalIds=q.snapshot().entries.map(e=>e.id);
  q.setActive(false);
  q=new ReceivingQueue(deps); await q.hydrate(); q.setActive(true); allowRecovery=true;
  for(let n=0;n<25;n++) assert.equal(await q.tick(),true);
  assert.equal(q.unsettled(),false);
  assert.equal(JSON.stringify(q.snapshot().entries.map(e=>e.id)),JSON.stringify(originalIds));
  assert.equal(q.snapshot().entries.filter(e=>e.state==='confirmed').length,50);
  assert.equal(posts.length,50); assert.equal(new Set(posts).size,50);
  console.log('Real mobile queue/repository + local Backend: lost midpoint ACK, stored queue recreation and GET-only recovery passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
