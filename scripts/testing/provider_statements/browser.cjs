/* Loopback-only browser acceptance. This script cannot reach a real API. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixture = require('./fixtures.cjs');
const out = process.env.PROVIDER_STATEMENT_UAT_OUTPUT, root = path.resolve(__dirname,'../../..');
if (!out || !path.isAbsolute(out) || path.resolve(out) === root || path.resolve(out).startsWith(root + path.sep)) throw Error('Absolute repo-external fixture output required');
const manifest = JSON.parse(fs.readFileSync(path.join(out,'source-manifest.json')));
for (const [file, hash] of Object.entries(manifest)) assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex'),hash,'Fixture must match current source: '+file);
const files = path.join(out,'dist'), calls = [], errors = [], scenarios = [];
let scenario = '', uploaded = process.env.PROVIDER_STATEMENT_UAT_SAVED === '1', confirmed = false, blockedExternal = 0;
const server = http.createServer(async (request,response) => {
  const url = new URL(request.url,'http://127.0.0.1');
  const send = (status, value) => {response.writeHead(status,{'content-type':'application/json'});response.end(JSON.stringify(value));};
  try {
    if (url.pathname.startsWith('/api/')) {
      calls.push({method:request.method,path:url.pathname,scenario});
      if (request.method === 'GET' && url.pathname === '/api/auth/me') return send(200,{id:'synthetic-owner',role:'owner',is_owner:true});
      if (request.method === 'GET' && url.pathname === '/api/operational-balances/provider-statements') return send(200,fixture.history(uploaded,confirmed));
      if (request.method === 'GET' && url.pathname === '/api/operational-balances/provider-statements/saved-files') return send(200,fixture.savedFiles());
      if (request.method === 'GET' && url.pathname === '/api/operational-balances/provider-statements/synthetic-weekly-statement/review') return send(200,fixture.detail(confirmed));
      let size=0; const chunks=[];
      for await (const chunk of request) {size+=chunk.length;assert(size<=2*1024*1024,'Synthetic body must remain bounded');chunks.push(chunk);}
      const bytes=Buffer.concat(chunks);
      if (request.method === 'POST' && url.pathname === '/api/operational-balances/provider-statements/saved-files/synthetic-saved-tabby/use') {
        const body=JSON.parse(bytes.toString('utf8'));
        assert.deepEqual(Object.keys(body).sort(),['expected_file_hash','expected_session_scope','expected_started_at','request_id']);
        assert.equal(body.expected_file_hash,fixture.savedFiles().items[0].file_hash);
        assert.equal(body.expected_session_scope,fixture.scope);assert.equal(body.expected_started_at,fixture.startedAt);assert(body.request_id.length>=8);
        assert.equal(uploaded,false,'One explicit saved-file use only');uploaded=true;return send(200,fixture.detail());
      }
      if (request.method === 'POST' && url.pathname === '/api/operational-balances/provider-statements/upload') {
        const body = await new Request('http://127.0.0.1/fixture',{method:'POST',headers:request.headers,body:bytes}).formData();
        assert.deepEqual([...body.keys()],['file','request_id','expected_session_scope','expected_started_at']);
        assert.equal(body.get('file').name,'weekly-tabby.xlsx');assert.equal(body.get('expected_session_scope'),fixture.scope);
        assert.equal(body.get('expected_started_at'),fixture.startedAt);assert(body.get('request_id').length>=8);
        assert.equal(uploaded,false,'One explicit upload only');uploaded=true;return send(200,fixture.detail());
      }
      if (request.method === 'POST' && url.pathname === '/api/operational-balances/provider-statements/synthetic-weekly-statement/confirm') {
        const body=JSON.parse(bytes.toString('utf8'));
        assert.equal(body.expected_session_scope,fixture.scope);assert.equal(body.expected_preview_hash,'b'.repeat(64));
        assert.equal(body.confirmed,true);assert.equal(body.confirmed_unrecorded,true);assert(body.request_id.length>=8);
        assert.deepEqual(body.decisions,[{row_id:'refund-missing',action:'record_missing_refund'}]);
        assert.equal(confirmed,false,'One missing refund only');confirmed=true;
        return send(200,{...fixture.detail(true),applied_row_ids:['refund-missing'],replayed:false});
      }
      throw Error('Unexpected synthetic API: '+request.method+' '+url.pathname);
    }
    if (url.pathname === '/favicon.ico') {response.writeHead(204);return response.end();}
    const file=path.resolve(files,url.pathname==='/'?'index.html':url.pathname.slice(1));
    if (!file.startsWith(files+path.sep) || !fs.existsSync(file)) {response.writeHead(404);return response.end();}
    response.writeHead(200,{'content-type':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});fs.createReadStream(file).pipe(response);
  } catch(error) {errors.push(error.message);send(422,{detail:{message:'Synthetic contract rejected'}});}
});
const posts = start => calls.slice(start).filter(call=>call.method==='POST');
async function quiet(page) {const count=calls.length;await page.waitForTimeout(250);assert.equal(calls.length,count,'Completed page must stay quiet');}
async function visibilityCycle(page) {
  await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'));});
  await quiet(page);
  await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'));window.dispatchEvent(new Event('focus'));});
  await quiet(page);
}
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  if (process.env.PROVIDER_STATEMENT_UAT_SERVE_ONLY === '1') {
    console.log(JSON.stringify({status:'ready',synthetic_only:true,origin,saved_fixture:uploaded}));
    return;
  }
  const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.PROVIDER_STATEMENT_UAT_CHROMIUM?{executablePath:process.env.PROVIDER_STATEMENT_UAT_CHROMIUM}:{})});
  try {
    for (const viewport of [{width:1440,height:1080},{width:390,height:844}]) {
      scenario=viewport.width===390?'mobile':'desktop';uploaded=false;confirmed=false;const first=calls.length;
      const context=await browser.newContext({viewport,locale:'ar-SA'});
      await context.route('**/*',route=>{
        if(new URL(route.request().url()).origin===origin)return route.continue();
        blockedExternal+=1;return route.abort();
      });
      const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
      await page.goto(origin);await page.getByRole('heading',{name:'رفع كشف للمطابقة',exact:true}).waitFor();
      assert.equal(posts(first).length,0,'Mount must never POST');await visibilityCycle(page);
      assert.equal(calls.slice(first).filter(call=>call.path.endsWith('/saved-files')).length,0,'Saved metadata needs explicit opening');
      if(scenario==='desktop') {
        await page.getByRole('button',{name:'استخدام كشف مرفوع سابقًا',exact:true}).click();
        const saved=page.getByLabel('اختيار الكشف المحفوظ saved-weekly-tabby.xlsx',{exact:true});await saved.waitFor();
        assert.equal(await page.getByLabel('اختيار الكشف المحفوظ missing-original.xlsx',{exact:true}).isDisabled(),true);
        await saved.check();await visibilityCycle(page);assert.equal(posts(first).length,0,'Selecting a saved original must never use it');
        assert.equal(calls.slice(first).filter(call=>call.path.endsWith('/saved-files')).length,1);
        await page.getByRole('button',{name:'استخدام الكشف المختار ومطابقته',exact:true}).click();
      } else {
        await page.getByLabel('ملف كشف المنصة',{exact:true}).setInputFiles({name:'weekly-tabby.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from('synthetic-only-file')});
        await quiet(page);assert.equal(posts(first).length,0,'Selecting a file must never upload');
        await page.getByRole('button',{name:'رفع الكشف ومطابقته',exact:true}).click();
      }
      await page.getByTestId('provider-statement-review').waitFor();assert.equal(posts(first).length,1);
      assert((await page.getByTestId('statement-row-sale-matched').innerText()).includes('Installments: 11 Months'));
      assert((await page.getByTestId('statement-row-refund-missing').innerText()).includes('Installments: 5 Months'));
      assert((await page.getByTestId('statement-row-sale-matched').innerText()).includes('9.74'));
      assert((await page.getByLabel('متوسط رسوم المنصة',{exact:true}).innerText()).includes('7.47%'));
      assert((await page.getByLabel('متوسط رسوم المنصة',{exact:true}).innerText()).includes('متوسط العمولة المتغيرة قبل الضريبة'));
      assert((await page.getByTestId('statement-row-incomplete').innerText()).includes('غير متاح'));
      await visibilityCycle(page);assert.equal(posts(first).length,1,'Returning must not POST');
      const dimensions=await page.evaluate(()=>({viewport:window.innerWidth,document:document.documentElement.scrollWidth,body:document.body.scrollWidth,
        tableOverflow:[...document.querySelectorAll('.op-table')].map(node=>({width:node.clientWidth,scrollWidth:node.scrollWidth}))}));
      assert(dimensions.document<=dimensions.viewport+1,'Horizontal document overflow');assert(dimensions.body<=dimensions.viewport+1,'Horizontal body overflow');
      if(scenario==='mobile')assert(dimensions.tableOverflow.some(table=>table.scrollWidth>table.width),'Wide table must scroll inside its own container');
      await page.screenshot({path:path.join(out,scenario+'-review.png'),fullPage:true});
      await page.getByLabel('اختيار العملية 900002',{exact:true}).check();await page.getByLabel('تأكيد مراجعة العمليات المختارة',{exact:true}).check();
      const confirm=page.getByRole('button',{name:'اعتماد العمليات المختارة',exact:true});
      assert.equal(await confirm.isDisabled(),true,'Missing refund needs additional acknowledgement');
      assert((await page.getByTestId('statement-row-refund-missing').innerText()).includes('ذمة المنصة من أصل المدفوع'));
      await page.getByLabel('تأكيد أن العمليات الجديدة غير مسجلة سابقًا',{exact:true}).check();
      await confirm.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,scenario+'-confirmation.png')});
      await confirm.click();await page.getByText('حُفظ الاعتماد وسُجلت العمليات المالية المحددة. تم تحديث دليل العمولة.',{exact:true}).waitFor();
      assert.equal(posts(first).length,2);await visibilityCycle(page);assert.equal(posts(first).length,2);
      assert.equal(await page.getByTestId('statement-row-refund-missing').locator('input[type=checkbox]').count(),0);
      scenarios.push({name:scenario,status:'PASS',viewport,request_counts:{get:calls.slice(first).filter(call=>call.method==='GET').length,post:posts(first).length},dimensions});
      await context.close();console.log('PASS '+scenario+' real component intake, review, missing refund and visibility');
    }
    assert.equal(errors.length,0,errors.join('\n'));
    fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify({synthetic_only:true,status:'PASS',scenarios,blocked_external_requests:blockedExternal,page_errors:errors.length,
      total_requests:calls.length,total_posts:calls.filter(call=>call.method==='POST').length},null,2)+'\n');
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
