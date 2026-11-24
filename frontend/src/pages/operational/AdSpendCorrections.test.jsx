import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import http from '../../lib/api';
import AdSpendCorrections from './AdSpendCorrections';

// Exercise the real API serializer; only the transport boundary is replaced.
jest.mock('../../lib/api', () => ({ get: jest.fn(), post: jest.fn() }));
const scope = '1'.repeat(64), previewHash = '2'.repeat(64);
const context = { actor: { id: 'synthetic-owner', is_owner: true }, session_scope: scope, permission_version: 1, status: 'active' };
const selection = { account_id: 'synthetic-ad', date_from: '2030-04-02', date_to: '2030-04-04' };
const preview = (patch = {}) => ({ schema: 'operational_ad_correction_preview_v1', session_scope: scope,
  read_only: true, posting_allowed: false, source_completeness_verified: false, selection, revision: 7,
  as_of: '2030-04-04T04:00:00+00:00', preview_hash: previewHash, can_approve: true,
  items: [
    { business_date: '2030-04-02', ad_day_id: 'synthetic-day-2', currency: 'SAR', status: 'correction_required', can_approve: true,
      before_amount: '12.35', after_amount: '24.56', difference: '12.21', issues: [],
      parts: [{ id: 'synthetic-part', funding_type: 'postpaid', before: '12.35', after: '24.56', difference: '12.21' }] },
    { business_date: '2030-04-03', ad_day_id: 'synthetic-day-3', currency: 'SAR', status: 'unchanged', can_approve: false,
      before_amount: '6.78', after_amount: '6.78', difference: '0.00', issues: [],
      parts: [{ id: 'synthetic-unchanged', funding_type: 'postpaid', before: '6.78', after: '6.78', difference: '0.00' }] },
    { business_date: '2030-04-04', ad_day_id: 'synthetic-day-4', currency: 'SAR', status: 'blocked', can_approve: false,
      issues: ['ad_correction_saved_closed_day_required'], parts: [] },
  ], ...patch });
const receipt = intent => ({ schema: 'operational_ad_correction_receipt_v1', request_id: intent.request_id,
  preview_hash: intent.expected_preview_hash, account_id: intent.selection.account_id, manual_history_preserved: true,
  native_sources_changed: false, calculation_revision: 8, days: intent.days.map(business_date => ({ business_date })) });
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
let root, host, onSaved;
const render = async (value = context) => act(async () => root.render(<AdSpendCorrections context={value} onSaved={onSaved}/>));
const button = label => [...host.querySelectorAll('button')].find(node => node.textContent === label);
const click = async label => act(async () => button(label).click());
const input = label => [...host.querySelectorAll('label')].find(node => node.textContent.startsWith(label))?.querySelector('input,select,textarea');
const fill = async (node, value) => act(async () => {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value').set.call(node, value);
  node.dispatchEvent(new Event('change', { bubbles: true })); node.dispatchEvent(new Event('input', { bubbles: true }));
});
const toggle = async node => act(async () => node.click());
async function readyPreview() {
  await render(); await click('مراجعة إنفاق الأيام المغلقة');
  await fill(input('الحساب الإعلاني'), selection.account_id);
  await fill(input('من تاريخ'), selection.date_from); await fill(input('إلى تاريخ'), selection.date_to);
  await act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
}
async function readyApproval() {
  await readyPreview();
  await toggle(host.querySelector('[aria-label="اعتماد يوم 2030-04-02"]'));
  await fill(input('سبب الاعتماد'), 'Reviewed synthetic native platform correction');
  await toggle(input('راجعت الحساب'));
}
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true; jest.resetAllMocks(); localStorage.clear(); onSaved = jest.fn();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { randomUUID: () => 'synthetic-request-123' } });
  http.get.mockImplementation((path) => Promise.resolve({ data: path.endsWith('/entities/ad_account')
    ? { items: [{ id: selection.account_id, name: 'إعلان تجريبي', currency: 'SAR' }] } : preview() }));
  http.post.mockImplementation((path, body) => Promise.resolve({ data: { status: 'refreshed', session_scope: scope,
    ad_correction: receipt(body.ad_correction_approval) } }));
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

test('opening the tool is dormant and all reads require explicit interaction', async () => {
  await render(); expect(http.get).not.toHaveBeenCalled(); expect(http.post).not.toHaveBeenCalled();
  await click('مراجعة إنفاق الأيام المغلقة');
  expect(http.get).toHaveBeenCalledTimes(1); expect(http.post).not.toHaveBeenCalled();
  expect(http.get.mock.calls[0][0]).toBe('/operational-balances/entities/ad_account');
  expect(button('اعتماد التصحيح وتحديث الأرصدة')).toBeUndefined();
});

test('preview binds the owner and original period and never selects blocked or unchanged days', async () => {
  await readyPreview();
  expect(http.get.mock.calls[1]).toEqual(['/operational-balances/ad-spend-corrections/preview', expect.objectContaining({
    params: { ...selection, expected_session_scope: scope }, signal: expect.any(AbortSignal), timeout: 30000,
  })]);
  expect(host.querySelectorAll('tbody tr')).toHaveLength(3);
  expect(host.textContent).toContain('12.21'); expect(host.textContent).toContain('الأجزاء المالية متطابقة');
  expect(host.querySelector('[aria-label="اعتماد يوم 2030-04-03"]').disabled).toBe(true);
  expect(host.querySelector('[aria-label="اعتماد يوم 2030-04-04"]').disabled).toBe(true);
  expect(button('اعتماد التصحيح وتحديث الأرصدة').disabled).toBe(true);
  expect(http.post).not.toHaveBeenCalled();
});

test('explicit review sends only identities/digest/revision and uses the existing refresh', async () => {
  await readyApproval(); await click('اعتماد التصحيح وتحديث الأرصدة');
  expect(http.post).toHaveBeenCalledTimes(1);
  const [path, body, config] = http.post.mock.calls[0];
  expect(path).toBe('/operational-balances/refresh');
  expect(body).toEqual({ visible: true, expected_session_scope: scope, ad_correction_approval: {
    selection, days: ['2030-04-02'], expected_revision: 7, expected_preview_hash: previewHash,
    expected_session_scope: scope, request_id: 'synthetic-request-123',
    reason: 'Reviewed synthetic native platform correction', confirm_reviewed: true,
  } });
  expect(config).toEqual(expect.objectContaining({ signal: expect.any(AbortSignal), timeout: 90000 }));
  expect(localStorage.length).toBe(0); expect(onSaved).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain('حُفظ التصحيح على الأيام الأصلية');
});

test('a same-native-cent correction exposes the SAR difference and pinned FX evidence before approval', async () => {
  const value = preview();
  value.items[0] = { ...value.items[0], currency: 'USD', before_amount: '12.35', after_amount: '12.35', difference: '0.00',
    fx_basis: { fx_rate: '3.7', fx_at: '2030-04-03T04:00:00Z', fx_evidence: 'synthetic FX approval' },
    parts: [{ id: 'synthetic-part', funding_type: 'postpaid', before: '12.35', after: '12.35', difference: '0.00',
      before_sar: '45.68', after_sar: '45.71', sar_difference: '0.03' }] };
  http.get.mockImplementation(path => Promise.resolve({ data: path.endsWith('/entities/ad_account')
    ? { items: [{ id: selection.account_id, name: 'إعلان تجريبي', currency: 'USD' }] } : value }));
  await readyPreview();
  expect(host.textContent).toContain('بالريال: 45.68 ← 45.71 (الفرق 0.03)');
  expect(host.textContent).toContain('سعر الصرف المثبت: 3.7 ريال');
  expect(host.textContent).toContain('synthetic FX approval');
  expect(http.post).not.toHaveBeenCalled();
});

test.each(['hide', 'unmount', 'permission', 'owner'])('%s aborts preview and suppresses delayed financial details', async kind => {
  const pending = deferred();
  http.get.mockImplementation(path => path.endsWith('/entities/ad_account')
    ? Promise.resolve({ data: { items: [{ id: selection.account_id, name: 'إعلان تجريبي', currency: 'SAR' }] } }) : pending.promise);
  await readyPreview();
  const signal = http.get.mock.calls[1][1].signal;
  if (kind === 'hide') await act(async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange'));
  });
  if (kind === 'unmount') await act(async () => root.render(null));
  if (kind === 'permission') await render({ ...context, permission_version: 2 });
  if (kind === 'owner') await render({ ...context, actor: { id: 'synthetic-employee', is_owner: false }, session_scope: '3'.repeat(64) });
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve({ data: preview() }));
  expect(host.textContent).not.toContain('12.21'); expect(http.post).not.toHaveBeenCalled();
});

test('a mismatched owner preview cannot enable an approval', async () => {
  http.get.mockImplementation(path => Promise.resolve({ data: path.endsWith('/entities/ad_account')
    ? { items: [{ id: selection.account_id, name: 'إعلان تجريبي', currency: 'SAR' }] }
    : preview({ session_scope: '3'.repeat(64) }) }));
  await readyPreview(); expect(button('اعتماد التصحيح وتحديث الأرصدة')).toBeUndefined();
  expect(http.post).not.toHaveBeenCalled(); expect(host.querySelector('table')).toBeNull();
});

test('an uncertain result persists the exact intent and an explicit retry uses the same request', async () => {
  await readyApproval(); http.post.mockRejectedValueOnce(new Error('unknown transport'));
  await click('اعتماد التصحيح وتحديث الأرصدة');
  const original = http.post.mock.calls[0][1];
  expect(localStorage.length).toBe(1); expect(host.textContent).toContain('لم تتأكد نتيجة الاعتماد');
  await click('إعادة إرسال نفس الاعتماد');
  expect(http.post.mock.calls[1][1]).toEqual(original); expect(localStorage.length).toBe(0);
  expect(onSaved).toHaveBeenCalledTimes(1);
});

test('a saved receipt resolves an uncertain result with GET and no second POST', async () => {
  await readyApproval(); http.post.mockRejectedValueOnce(new Error('unknown transport'));
  await click('اعتماد التصحيح وتحديث الأرصدة');
  const intent = http.post.mock.calls[0][1].ad_correction_approval;
  http.get.mockResolvedValueOnce({ data: { schema: 'operational_ad_correction_status_v1', session_scope: scope,
    request_id: intent.request_id, status: 'applied', receipt: receipt(intent) } });
  await click('التحقق من إيصال الاعتماد');
  expect(http.post).toHaveBeenCalledTimes(1); expect(localStorage.length).toBe(0); expect(onSaved).toHaveBeenCalledTimes(1);
});

test('a stale source rejection clears the old preview and never automatically sends a new intent', async () => {
  await readyApproval(); http.post.mockRejectedValueOnce({ response: { status: 409,
    data: { detail: { code: 'ad_correction_preview_stale', message: 'تغير المصدر؛ أعد المعاينة' } } } });
  await click('اعتماد التصحيح وتحديث الأرصدة');
  expect(localStorage.length).toBe(0); expect(host.querySelector('table')).toBeNull();
  expect(http.post).toHaveBeenCalledTimes(1); expect(onSaved).not.toHaveBeenCalled();
});

test('leaving an in-flight approval preserves its reference and never claims a late success', async () => {
  await readyApproval(); const pending = deferred(); http.post.mockReturnValueOnce(pending.promise);
  await click('اعتماد التصحيح وتحديث الأرصدة');
  const [, body, config] = http.post.mock.calls[0];
  await act(async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange'));
  });
  expect(config.signal.aborted).toBe(true); expect(localStorage.length).toBe(1);
  await act(async () => pending.resolve({ data: { session_scope: scope, ad_correction: receipt(body.ad_correction_approval) } }));
  expect(onSaved).not.toHaveBeenCalled(); expect(host.textContent).not.toContain('حُفظ التصحيح');
  expect(http.post).toHaveBeenCalledTimes(1);
});
