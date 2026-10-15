/* Isolated real React/DOM test; API module replaced at the network boundary. */
const path = require('node:path');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const moduleRoot = process.env.UI_TEST_MODULES || path.join(root, 'frontend');
const req = createRequire(path.join(moduleRoot, 'package.json'));
const esbuild = req('esbuild');
const { JSDOM } = req('jsdom');
const dom = new JSDOM('<div id="root"></div>', {url:'http://isolated.test'});
global.window = dom.window; global.document = dom.window.document;
global.HTMLElement = dom.window.HTMLElement; global.IS_REACT_ACT_ENVIRONMENT = true;
const React = req('react');
const {createRoot} = req('react-dom/client');
const {act} = React;
let data = {state:'prepared', can_audit:true, can_activate:true, fingerprint:'reviewed-scope', total:199, verified:2, remaining:197,
  excluded:['synthetic-a','synthetic-b'], results:[{reference:'synthetic-c',state:'pending',reason:'awaiting_activation'}]};
const posts = [];
global.__recoveryApi = {get: async () => ({data}), post: async (url, body) => {
  posts.push({url, body}); data = {...data, state:url.endsWith('/activate')?'active':'paused',
    can_activate:false, can_audit:!url.endsWith('/activate')}; return {data};
}};
(async () => {
  const build = await esbuild.build({entryPoints:[path.join(root,'frontend/src/components/qoyod/Qoyod404Recovery.jsx')],
    bundle:true, write:false, platform:'node', format:'cjs', jsx:'automatic',
    external:['react','react/jsx-runtime'],
    plugins:[{name:'isolated-api',setup(b){b.onResolve({filter:/lib\/api$/},()=>({path:'api',namespace:'test'}));
      b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:'export default globalThis.__recoveryApi',loader:'js'}));}}]});
  const loaded = {exports:{}};
  new Function('require','module','exports',build.outputFiles[0].text)(req,loaded,loaded.exports);
  const Component = loaded.exports.default;
  const container = document.getElementById('root');
  const app = createRoot(container);
  await act(async()=>{app.render(React.createElement(Component));});
  assert.equal(posts.length,0,'render/refresh must not activate');
  assert.match(container.querySelector('[data-testid="recovery-counts"]').textContent,/2 \/ 199.*197/);
  const findButton = text => [...container.querySelectorAll('button')].find(x=>x.textContent===text);
  const activate = findButton('تفعيل التعافي التلقائي للنطاق المحدد');
  assert.equal(activate.disabled,true,'explicit checkbox required');
  await act(async()=>{container.querySelector('input[type="checkbox"]').click();});
  assert.equal(activate.disabled,false);
  await act(async()=>{activate.click();});
  assert.deepEqual(posts[0],{url:'/integrations/qoyod/manual/recovery-404/activate',
    body:{fingerprint:'reviewed-scope',confirmation:'ACTIVATE_REVIEWED_404_COHORT'}});
  assert.equal(findButton('التحقق من المحاولات غير المحسومة — دون إرسال').disabled,true);
  await act(async()=>{findButton('إيقاف التعافي').click();});
  await act(async()=>{findButton('التحقق من المحاولات غير المحسومة — دون إرسال').click();});
  assert.ok(posts[2].url.endsWith('/audit'));
  assert.equal(posts.filter(x=>x.url.endsWith('/activate')).length,1);
  assert.match(container.textContent,/synthetic-c/,'per-order result remains visible');
  const auditText = 'التحقق من المحاولات غير المحسومة — دون إرسال';
  async function show(next) {
    data = {...data, ...next};
    await act(async()=>{findButton('تحديث حالة التعافي').click();});
  }
  // The server clock/lease decision is authoritative, not a persisted busy bit.
  await show({state:'paused',busy:true,can_audit:true,audit_block_reason:null,
    lease_until:'2000-01-01T00:00:00Z',counts:{unknown:1}});
  assert.equal(findButton(auditText).disabled,false,'expired paused lease can audit');
  assert.equal(findButton('تفعيل التعافي التلقائي للنطاق المحدد'),undefined);
  const before = posts.length;
  await act(async()=>{findButton(auditText).click();});
  assert.deepEqual(posts.slice(before),[{url:'/integrations/qoyod/manual/recovery-404/audit',body:{}}]);
  for (const state of ['paused','active']) {
    await show({state,busy:true,can_audit:false,audit_block_reason:'operation_in_progress',
      lease_until:'2099-01-01T00:00:00Z'});
    assert.equal(findButton(auditText).disabled,true,'live lease cannot audit');
    const count = posts.length;
    await act(async()=>{findButton(auditText).click();});
    assert.equal(posts.length,count);
  }
  await show({state:'paused',busy:false,can_audit:undefined});
  assert.equal(findButton(auditText).disabled,true,'missing server eligibility fails closed');
  assert.equal(posts.filter(x=>x.url.endsWith('/activate')).length,1,'audit never reactivates');
  await show({state:'paused',busy:false,can_audit:true,can_activate:true,counts:{rounding_review:1,pending:196},
    rounding_unsettled:1,verified:2,remaining:197,results:[{reference:'synthetic-c',state:'rounding_review',
      reason:'existing_invoice_rounding_requires_settlement',invoice_id:'existing',salla_total:'161.11',
      invoice_total:'161.12',paid_amount:'161.11',remaining:'0.01'}]});
  assert.match(container.textContent,/فاتورة موجودة — فرق تقريب يحتاج تسوية/);
  assert.match(container.textContent,/161.12/);
  assert.match(container.textContent,/0.01/);
  assert.match(container.querySelector('[data-testid="recovery-counts"]').textContent,/2 \/ 199.*197/);
  assert.ok(findButton('تفعيل التعافي التلقائي للنطاق المحدد'),'isolated rounding permits explicit resume');
  assert.equal(posts.filter(x=>x.url.endsWith('/activate')).length,1,'isolation does not auto activate');
  await show({release_review_required:true,can_activate:false});
  assert.equal(findButton('تفعيل التعافي التلقائي للنطاق المحدد'),undefined);
  const prep = findButton('تجهيز الاستئناف على الإصدار الحالي — دون إرسال');
  assert.ok(prep);
  await act(async()=>{prep.click();});
  assert.equal(posts.at(-1).url,'/integrations/qoyod/manual/recovery-404/review-release');
  assert.equal(posts.filter(x=>x.url.endsWith('/activate')).length,1,'release review never activates');
  await show({can_activate:false,results:[{reference:'synthetic-c',state:'review',reason:'outcome_unknown',
    read_diagnostic:{stage:'provider_invoice',error_type:'ManualQoyodError',http_status:0,
      cause_type:'ReadTimeout',page:7,elapsed_ms:25001,location:'client.py:147'}}]});
  const diagnostic = container.querySelector('[data-testid="recovery-read-diagnostic"]').textContent;
  assert.match(diagnostic,/provider_invoice.*ReadTimeout.*page 7.*25001/);
  assert.equal(findButton('تفعيل التعافي التلقائي للنطاق المحدد'),undefined,'diagnostics never authorize unknown');
  await act(async()=>{app.unmount();});
  console.log('PASS: real React DOM, counters, explicit fingerprint activation, pause, read-only audit, no implicit send');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>dom.window.close());
