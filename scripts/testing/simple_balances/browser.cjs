/* Browser acceptance of actual components against a loopback synthetic API. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const f = require('./fixtures.cjs');
const root = path.resolve(__dirname, '../../..');
const out = process.env.SIMPLE_BALANCES_UAT_OUTPUT;
if (!out || !path.isAbsolute(out) || path.resolve(out) === root || path.resolve(out).startsWith(root + path.sep)) throw Error('Absolute repo-external output required');
assert.equal(f.synthetic_only, true);
const manifest = JSON.parse(fs.readFileSync(path.join(out, 'source-manifest.json')));
assert.equal(manifest.synthetic_only, true);
for (const [file, hash] of Object.entries(manifest.sha256)) assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex'), hash, file);
const calls = [], errors = [], results = [], screenshots = [];
let actor = f.owner, scenario = '', access = structuredClone(f.access);
const rows = structuredClone(f.ledger), refunds = [...Array.from({ length: 6 }, (_, index) => f.refund(index)), structuredClone(f.platformRefund)];
const requests = new Set();
const pendingCommands = new Map(), retiredCommands = new Set();
let holdEntryRegistration = false, releaseEntryRegistration = null, entryRegistrationStarted = null;
const assertRegistered = (action, targetId, body) => {
  const pending = pendingCommands.get(body.request_id);
  assert(pending && pending.actor_id === actor.id, 'A financial request requires its authenticated server recovery record');
  assert.equal(pending.value.action, action); assert.equal(pending.value.target_id, targetId);
  assert.deepEqual(pending.value.command, body, 'Execution must use the registered immutable command');
};
const pageResult = (items, params) => {
  const page = Math.max(1, Number(params.get('page') || 1));
  assert.equal(params.get('page_size'), '5');
  return { items: items.slice((page - 1) * 5, page * 5), page, page_size: 5, total: items.length, total_pages: Math.max(1, Math.ceil(items.length / 5)) };
};
const dist = path.join(out, 'dist');
const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');
  const send = (status, value) => { response.writeHead(status, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)); };
  if (!url.pathname.startsWith('/api/')) {
    const requested = path.resolve(dist, url.pathname.slice(1));
    const file = requested.startsWith(dist + path.sep) && fs.existsSync(requested) && fs.statSync(requested).isFile() ? requested : path.join(dist, 'index.html');
    response.writeHead(200, { 'content-type': file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html' });
    fs.createReadStream(file).pipe(response); return;
  }
  const call = { method: request.method, path: url.pathname, params: Object.fromEntries(url.searchParams), scenario };
  calls.push(call);
  try {
    let body;
    if (request.method !== 'GET') {
      let raw = ''; for await (const chunk of request) raw += chunk;
      body = JSON.parse(raw); call.body = body;
    }
    if (url.pathname === '/api/auth/me' && request.method === 'GET') return send(200, actor);
    const base = '/api/operational-balances/workspace';
    const admin = '/api/operational-balances/access';
    if (url.pathname.startsWith(`${base}/pending-commands/`)) {
      const id = url.pathname.split('/').at(-1);
      assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
      if (retiredCommands.has(id)) return send(410, { detail: { code: 'operational_pending_command_retired' } });
      if (request.method === 'POST') {
        assert.equal(body.command.request_id, id); assert.equal(body.command.expected_session_scope, f.scope);
        const value = { id, action: body.action, target_id: body.target_id || null, command: body.command };
        const old = pendingCommands.get(id);
        if (old) { assert.equal(old.actor_id, actor.id); assert.deepEqual(old.value, value); }
        else pendingCommands.set(id, { actor_id: actor.id, value });
        if (holdEntryRegistration && value.action === 'entry') {
          releaseEntryRegistration = () => send(200, value); entryRegistrationStarted?.(); return;
        }
        return send(200, value);
      }
      const pending = pendingCommands.get(id);
      if (!pending || pending.actor_id !== actor.id) return send(404, { detail: { code: 'operational_pending_command_not_found' } });
      if (request.method === 'GET') { assert.equal(url.searchParams.get('expected_session_scope'), f.scope); return send(200, pending.value); }
      if (request.method === 'DELETE') {
        assert.equal(body.expected_session_scope, f.scope); pendingCommands.delete(id); retiredCommands.add(id);
        return send(200, { deleted: true, id });
      }
      throw Error('Unsupported recovery method');
    }
    if (url.pathname.startsWith(admin) && !actor.is_owner) return send(403, { detail: { message: 'صلاحيات المالك مطلوبة' } });
    if (url.pathname === `${base}/context`) return send(200, f.context(actor));
    if (url.pathname === `${base}/parties`) return send(200, { items: f.resources.filter(item => (!url.searchParams.get('kind') || item.kind === url.searchParams.get('kind')) && (!url.searchParams.get('q') || item.name.includes(url.searchParams.get('q')))) });
    if (url.pathname === `${base}/movements`) return send(200, pageResult(rows, url.searchParams));
    if (url.pathname === `${base}/ledger/external_person/external-demo`) {
      const params = new URLSearchParams(url.search);
      if (params.get('focus_id')) params.set('page', String(Math.floor(rows.findIndex(item => item.id === params.get('focus_id')) / 5) + 1));
      return send(200, { ...pageResult(rows, params), resource: f.resources[0] });
    }
    if (url.pathname === `${base}/entries` && request.method === 'POST') {
      assertRegistered('entry', null, body);
      assert.equal(body.expected_session_scope, f.scope); assert.equal(body.party_type, 'external_person');
      assert.equal(body.party_id, 'external-demo'); assert.equal(body.side, 'for_us'); assert.equal(body.amount, '50.00');
      assert.equal(body.reason, undefined); assert.equal(body.source_type, 'employee_custody'); assert.equal(body.source_id, 'custody-demo');
      assert(!requests.has(body.request_id), 'The completed intent must not submit twice'); requests.add(body.request_id);
      const row = { ...rows[0], id: 'saved-backdated', event_id: 'saved-event', description: body.note,
        amount: body.amount, side: body.side, business_date: body.business_date, recorded_by: { id: actor.id, name: actor.name } };
      rows.push(row); f.resources[0].balance.amount = '350.00';
      return send(200, { saved: true, movement_id: row.id, party_type: row.party_type, party_id: row.party_id });
    }
    if (url.pathname === `${base}/refunds/order/2044`) return send(200, f.order);
    if (url.pathname === `${base}/refunds` && request.method === 'GET') return send(200, pageResult(refunds, url.searchParams));
    if (url.pathname === `${base}/refunds` && request.method === 'POST') {
      assertRegistered('refund.create', null, body);
      assert.equal(body.expected_session_scope, f.scope); assert.equal(body.order_number, '2044'); assert.equal(body.amount, '50.00');
      assert.equal(body.method, 'bank'); assert.equal(body.bank_id, 'bank-demo'); assert.equal(body.beneficiary_iban, undefined);
      assert.equal(body.items, undefined); assert(!requests.has(body.request_id)); requests.add(body.request_id);
      const item = { ...f.refund('saved'), order_number: '2044', reason: body.reason, recorded_by: { id: actor.id, name: actor.name } };
      refunds.unshift(item); f.order.reserved_amount = '50.00'; f.order.available_amount = '250.00';
      return send(200, { saved: true, item });
    }
    if (url.pathname === `${base}/refunds/refund-platform/reconcile` && request.method === 'POST') {
      assertRegistered('refund.reconcile', 'refund-platform', body);
      const item = refunds.find(value => value.id === 'refund-platform');
      assert.equal(body.expected_session_scope, f.scope); assert.equal(item.status, 'pending');
      assert.equal(body.evidence_id, f.platformRefund.provider_evidence[0].id);
      assert.equal(body.expected_evidence_hash, f.platformRefund.provider_evidence[0].fingerprint);
      assert.equal(body.reference, undefined); assert.equal(body.amount, undefined); assert.equal(body.bank_id, undefined);
      assert(!requests.has(body.request_id)); requests.add(body.request_id);
      Object.assign(item, { status: 'refunded', can_reconcile: false, provider_evidence: [], reference: 'SYNTHETIC-PROOF',
        confirmed_by: { id: actor.id, name: actor.name } });
      return send(200, { saved: true, item });
    }
    if (url.pathname.startsWith(`${base}/refunds/`) && request.method === 'GET') {
      const item = refunds.find(value => value.id === url.pathname.split('/').at(-1));
      if (item) return send(200, item);
    }
    if (url.pathname === `${admin}/catalog`) return send(200, f.catalog);
    if (url.pathname === `${admin}/users`) return send(200, { items: [f.owner, f.staff] });
    if (url.pathname === `${admin}/users/${f.staff.id}`) {
      if (request.method === 'PUT') {
        assert.equal(body.expected_version, access.version); assert.equal(body.enabled, true);
        const bank = body.resources.find(item => item.id === 'bank-demo');
        assert.equal(bank.use, false); assert.equal(bank.movements, 'all'); assert.equal(bank.balance, true);
        assert.deepEqual(body.operations, f.access.operations, 'Bulk supplier selection must not enable operations');
        assert.deepEqual(body.resources.filter(item => item.kind === 'supplier').sort((a, b) => a.id.localeCompare(b.id)), [
          { kind: 'supplier', id: 'supplier-demo', use: true, movements: 'all', balance: true },
          { kind: 'supplier', id: 'supplier-extra', use: true, movements: 'own', balance: true },
        ]);
        access = { version: access.version + 1, enabled: body.enabled, operations: body.operations, resources: body.resources };
      }
      return send(200, { user: f.staff, access });
    }
    throw Error(`Unexpected fixture request: ${request.method} ${url.pathname}`);
  } catch (error) { errors.push(error.message); return send(422, { detail: { not_applied: true, message: 'رفض طلب اختبار غير مطابق' } }); }
});
const basePath = '/operational-balances/daily-movements';
const field = (page, label) => page.locator('label.sb-field').filter({ has: page.getByText(label, { exact: true }) }).locator('input,select,textarea');
async function check(name, action) {
  scenario = name; const before = calls.length; await action();
  results.push({ name, status: 'PASS', request_count: calls.length - before }); console.log(`PASS ${name}`);
}
async function deadline(promise) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(Error('Recovery registration did not reach the fixture')), 5000); })]); }
  finally { clearTimeout(timer); }
}
async function capture(page, name) {
  const dimensions = await page.evaluate(() => ({ width: innerWidth, html: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
  assert(dimensions.html <= dimensions.width && dimensions.body <= dimensions.width, `Body overflow ${name}: ${JSON.stringify(dimensions)}`);
  const filename = `${name}.png`; await page.screenshot({ path: path.join(out, filename), fullPage: true }); screenshots.push(filename);
}
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true, chromiumSandbox: true,
    ...(process.env.SIMPLE_BALANCES_UAT_CHROMIUM ? { executablePath: process.env.SIMPLE_BALANCES_UAT_CHROMIUM } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    const ledgerUrl = `${origin}${basePath}?tab=parties&kind=external_person&id=external-demo`;
    await check('Desktop ledger uses five rows, recorder names, red له and green عليه', async () => {
      await page.goto(ledgerUrl); await page.locator('.sb-ledger tbody tr').first().waitFor();
      assert.equal(await page.locator('.sb-ledger tbody tr').count(), 5);
      assert((await page.locator('.sb-ledger').innerText()).includes(f.staff.name));
      const colors = await page.locator('.sb-ledger th[data-side]').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).color));
      assert.deepEqual(colors, ['rgb(174, 36, 66)', 'rgb(21, 115, 84)']);
      await capture(page, 'ledger-desktop');
      await page.getByRole('button', { name: 'التالي', exact: true }).click();
      await page.waitForFunction(() => document.querySelectorAll('.sb-ledger tbody tr').length === 2);
    });
    await check('Mobile ledger remains inside a 390px page with numbered pagination', async () => {
      await page.setViewportSize({ width: 390, height: 844 }); await page.goto(ledgerUrl);
      await page.locator('.sb-ledger tbody tr').first().waitFor();
      assert.equal(await page.locator('.sb-ledger tbody tr').count(), 5); await capture(page, 'ledger-mobile');
      assert.equal(await page.getByRole('button', { name: 'الصفحة 2', exact: true }).count(), 1);
    });
    await check('External reasons and custody source are conditional; save returns to the actual backdated page', async () => {
      await page.getByRole('button', { name: '＋ إضافة مبلغ', exact: true }).click();
      await page.locator('.sb-direction [data-side="for_us"]').click();
      assert.equal(await field(page, 'سبب المبلغ').count(), 0);
      await field(page, 'المبلغ بالريال').fill('50'); await field(page, 'ملاحظة (اختياري)').fill('صرف توضيحي من العهدة');
      await field(page, 'الدفع من').selectOption(JSON.stringify(['employee_custody', 'custody-demo']));
      await capture(page, 'external-form-mobile');
      await page.locator('.sb-direction [data-side="for_party"]').click();
      await field(page, 'سبب المبلغ').selectOption('salary_settlement');
      assert.equal(await field(page, 'الدفع من').count(), 0);
      await field(page, 'الموظف').selectOption('employee-demo');
      assert.equal(await field(page, 'المبلغ بالريال').inputValue(), '50');
      await page.setViewportSize({ width: 1440, height: 1000 }); await capture(page, 'external-form-desktop');
      await page.locator('.sb-direction [data-side="for_us"]').click();
      assert.equal(await field(page, 'الموظف').count(), 0); assert.equal(await field(page, 'الدفع من').inputValue(), '');
      await field(page, 'الدفع من').selectOption(JSON.stringify(['employee_custody', 'custody-demo']));
      await field(page, 'التاريخ').fill('2026-01-01');
      holdEntryRegistration = true;
      const registrationReady = new Promise(resolve => { entryRegistrationStarted = resolve; });
      await page.getByRole('button', { name: 'حفظ والعودة للحركات', exact: true }).click();
      await deadline(registrationReady);
      assert.equal(calls.filter(call => call.path.endsWith('/entries') && call.method === 'POST').length, 0,
        'No financial write may start before registration is confirmed');
      const markers = await page.evaluate(() => Object.entries(localStorage));
      assert.equal(markers.length, 1);
      assert.match(markers[0][0], /^mezan\.pending-command\.v1:[a-f0-9]{64}$/);
      assert.match(markers[0][1], /^[0-9a-f-]{36}$/i);
      holdEntryRegistration = false; releaseEntryRegistration(); releaseEntryRegistration = null; entryRegistrationStarted = null;
      await page.locator('.sb-new-row').waitFor();
      assert((await page.locator('.sb-new-row').innerText()).includes('صرف توضيحي من العهدة'));
      assert.equal(await page.getByRole('button', { name: 'الصفحة 2', exact: true }).getAttribute('aria-current'), 'page');
      assert.equal(calls.filter(call => call.path.endsWith('/entries') && call.method === 'POST').length, 1);
    });
    await check('Owner sees specialized routes without deleted grant keys and exactly three primary tabs', async () => {
      await page.getByRole('button', { name: 'جميع الأقسام', exact: true }).click();
      assert.equal(await page.getByRole('link', { name: /مرتجعات الأصناف/ }).count(), 1);
      assert.equal(await page.getByRole('link', { name: /^الاستبدال/ }).count(), 1);
      assert.deepEqual(await page.locator('.sb-tabs button').allTextContents(), ['الحركات', 'الأطراف', 'الحسابات']);
    });
    await check('Order refund reserves a partial amount and reconciles only server provider evidence', async () => {
      await page.goto(`${origin}${basePath}?tab=parties&panel=refunds`);
      await field(page, 'رقم الطلب').fill('2044'); await page.getByRole('button', { name: 'عرض الطلب', exact: true }).click();
      await field(page, 'طريقة الاسترجاع').waitFor(); assert.equal(await field(page, 'طريقة الاسترجاع').inputValue(), '');
      assert.deepEqual(await field(page, 'طريقة الاسترجاع').locator('option').allTextContents(), ['اختر طريقة الاسترجاع', 'تحويل بنكي', 'تمارا']);
      await field(page, 'طريقة الاسترجاع').selectOption('bank'); await field(page, 'بنك المؤسسة الذي سيخرج منه المبلغ').selectOption('bank-demo');
      await field(page, 'المبلغ المسترجع').fill('50'); await field(page, 'سبب الاسترجاع').fill('رد جزء من المبلغ — توضيحي');
      assert(!/آيبان|IBAN|اسم المستفيد/.test(await page.locator('main').innerText()));
      await capture(page, 'refund-desktop'); await page.setViewportSize({ width: 390, height: 844 }); await capture(page, 'refund-mobile');
      await page.getByRole('button', { name: 'حفظ طلب الاسترجاع', exact: true }).click();
      await page.getByRole('heading', { name: 'استرجاع طلب 2044', exact: true }).waitFor();
      assert.equal(f.order.available_amount, '250.00'); assert.equal(f.resources.find(item => item.kind === 'bank').balance.amount, '5000.00');
      assert.equal(calls.filter(call => call.path.endsWith('/confirm')).length, 0);
      await page.goto(`${origin}${basePath}?tab=parties&panel=refunds&refund_id=refund-platform`);
      await field(page, 'تأكيد المنصة').waitFor();
      assert.equal(await field(page, 'مرجع التحويل (اختياري)').count(), 0);
      await field(page, 'تأكيد المنصة').selectOption(f.platformRefund.provider_evidence[0].id);
      await page.getByRole('button', { name: 'مطابقة تأكيد المنصة', exact: true }).click();
      await page.getByText('تم تسجيل رد المبلغ', { exact: true }).waitFor();
      assert.equal(calls.filter(call => call.path.endsWith('/reconcile')).length, 1);
      assert.equal(calls.filter(call => call.path.endsWith('/confirm')).length, 0);
      assert.equal(f.resources.find(item => item.kind === 'bank').balance.amount, '5000.00');
    });
    await check('Owner permissions keep use, own/all movements and balance independent until explicit save', async () => {
      await page.setViewportSize({ width: 1440, height: 1000 }); await page.goto(`${origin}/operational-balances/access`);
      await field(page, 'الموظف').selectOption(f.staff.id); await page.locator('.sb-access-table').waitFor();
      assert(!await field(page, 'الموظف').locator('option').evaluateAll(nodes => nodes.some(node => node.value === 'synthetic-owner')));
      await page.getByLabel('استخدام بنك المؤسسة التوضيحي', { exact: true }).uncheck();
      await page.getByLabel('حركات بنك المؤسسة التوضيحي', { exact: true }).selectOption('all');
      await page.getByLabel('رصيد بنك المؤسسة التوضيحي', { exact: true }).check();
      await field(page, 'نوع الحساب أو الجهة').selectOption('supplier');
      await field(page, 'بحث في الحسابات').fill('مورد توضيحي');
      assert.equal(await page.locator('.sb-access-table tbody tr').count(), 1);
      await field(page, 'استخدام الموردين').selectOption('allow');
      await field(page, 'حركات الموردين').selectOption('all');
      await field(page, 'أرصدة الموردين').selectOption('allow');
      await page.getByRole('button', { name: 'تطبيق على جميع الموردين (2)', exact: true }).click();
      assert.equal(await page.locator('.sb-access-table tbody tr').count(), 2);
      for (const name of ['مورد توضيحي', 'مورد إضافي للاختبار']) {
        assert.equal(await page.getByLabel(`استخدام ${name}`, { exact: true }).isChecked(), true);
        assert.equal(await page.getByLabel(`حركات ${name}`, { exact: true }).inputValue(), 'all');
        assert.equal(await page.getByLabel(`رصيد ${name}`, { exact: true }).isChecked(), true);
      }
      await page.getByLabel('حركات مورد إضافي للاختبار', { exact: true }).selectOption('own');
      await field(page, 'نوع الحساب أو الجهة').selectOption('');
      assert.equal(calls.filter(call => call.method === 'PUT').length, 0); await capture(page, 'access-desktop');
      await page.getByRole('button', { name: 'حفظ صلاحيات الموظف', exact: true }).click();
      await page.getByText('تم حفظ صلاحيات موظف الاختبار. تُطبق على الطلبات التالية مباشرة.', { exact: true }).waitFor();
      assert.equal(calls.filter(call => call.method === 'PUT').length, 1);
    });
    await check('Mobile permission cards expose every account label and control inside the viewport', async () => {
      await page.setViewportSize({ width: 390, height: 844 });
      const cards = page.locator('.sb-access-table tbody tr');
      assert.equal(await cards.count(), f.catalog.resources.length);
      const bulkControls = await page.locator('.sb-access-bulk select,.sb-access-bulk button').evaluateAll(nodes => nodes.map(node => {
        const rect = node.getBoundingClientRect();
        return { left: rect.left, right: rect.right, width: rect.width, height: rect.height, viewport: innerWidth };
      }));
      assert.equal(bulkControls.length, 4);
      for (const box of bulkControls) assert(box.width > 0 && box.height > 0 && box.left >= 0 && box.right <= box.viewport,
        `Clipped mobile supplier bulk control: ${JSON.stringify(box)}`);
      // A bounded body alone can conceal a horizontally clipped table. Inspect
      // every permission and its visible label before any horizontal scrolling.
      const horizontal = await cards.evaluateAll(nodes => nodes.flatMap(card =>
        [...card.querySelectorAll('th strong,.sb-access-mobile-label,input,select')].map(node => {
          const rect = node.getBoundingClientRect();
          return { name: node.getAttribute('aria-label') || node.textContent, left: rect.left, right: rect.right,
            width: rect.width, height: rect.height, viewport: innerWidth };
        })));
      assert.equal(horizontal.length, f.catalog.resources.length * 7);
      for (const box of horizontal) assert(box.width > 0 && box.height > 0 && box.left >= 0 && box.right <= box.viewport,
        `Clipped mobile permission: ${JSON.stringify(box)}`);
      for (let index = 0; index < await cards.count(); index++) {
        const card = cards.nth(index); await card.scrollIntoViewIfNeeded();
        assert.equal(await card.evaluate(node => [...node.querySelectorAll('.sb-access-mobile-label,input,select')].every(control => {
          const rect = control.getBoundingClientRect();
          return rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth;
        })), true, 'Every permission must be visible when its account card is in view');
      }
      await page.evaluate(() => window.scrollTo(0, 0)); await capture(page, 'access-mobile');
      assert.equal(await page.getByLabel('استخدام بنك المؤسسة التوضيحي', { exact: true }).isChecked(), false);
      assert.equal(await page.getByLabel('رصيد بنك المؤسسة التوضيحي', { exact: true }).isChecked(), true);
    });
    await check('Staff cannot see owner tools or fetch the owner permission catalog', async () => {
      actor = f.staff; await page.goto(ledgerUrl); await page.locator('.sb-ledger tbody tr').first().waitFor();
      await page.getByRole('button', { name: 'جميع الأقسام', exact: true }).click();
      assert.equal(await page.getByRole('link', { name: /مرتجعات الأصناف|الاستبدال|صلاحيات الموظفين/ }).count(), 0);
      const before = calls.length; await page.goto(`${origin}/operational-balances/access`);
      await page.getByText('إدارة صلاحيات الأرصدة متاحة لمالك الحساب.', { exact: true }).waitFor();
      assert.equal(calls.slice(before).filter(call => call.path.startsWith('/api/operational-balances/access')).length, 0);
    });
    assert.deepEqual(errors, []); assert.equal(results.length, 8); assert.equal(screenshots.length, 8);
    await context.close();
  } finally {
    fs.writeFileSync(path.join(out, 'browser-evidence.json'), JSON.stringify({ synthetic_only: true, actual_components: true, chromium_sandbox: true, results, errors, screenshots, calls }, null, 2));
    await browser.close(); server.close();
  }
})().catch(error => {
  errors.push(error.message);
  fs.writeFileSync(path.join(out, 'browser-evidence.json'), JSON.stringify({ synthetic_only: true, actual_components: true,
    chromium_sandbox: true, results, errors, screenshots, calls }, null, 2));
  console.error(error); server.close(); process.exitCode = 1;
});
