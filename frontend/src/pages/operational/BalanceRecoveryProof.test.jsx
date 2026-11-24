import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import http from '../../lib/api';
import BalanceRecoveryProof from './BalanceRecoveryProof';

jest.mock('../../lib/api', () => ({ get: jest.fn(), post: jest.fn() }));
const context = { actor: { id: 'owner', is_owner: true }, session_scope: 'scope', permission_version: 1 };
const empty = () => ({ obligations: [], automatic_movements: [] });
const row = patch => ({ entity_type: 'ad_account', entity_id: 'snap-usd', currency: 'USD',
  direction: 'payable', business_date: '2026-11-21', count: 1, confirmed: '10.25', expected: '0.00', ...patch });
const receipt = patch => ({ schema_version: 1, session_scope: 'scope', state: 'available',
  entities: [{ entity_type: 'ad_account', entity_id: 'snap-usd', currency: 'USD', label: 'سناب الدولار' }],
  proof: { schema_version: 1, calculation_revision: 9, as_of: '2026-11-24T12:00:00Z', complete: true,
    source_completeness_verified: false, original_dates_verified: false,
    new_original_dated: { ...empty(), obligations: [row()] },
    new_date_unverified: { ...empty(), obligations: [row({ business_date: '2026-11-24', confirmed: '20.00' })] },
    changed_existing: { count: 0, before: empty(), after: empty() },
    preservation: { manual_movements: { preserved: true }, openings: { preserved: true }, requests: { preserved: true } },
    new_duplicate_keys: { movement_id: 0 }, existing_duplicate_keys: { movement_id: 0 }, ...patch } });
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const urlDescriptors = Object.fromEntries(['createObjectURL', 'revokeObjectURL'].map(name => [name, Object.getOwnPropertyDescriptor(URL, name)]));
let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true; jest.clearAllMocks();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  http.get.mockResolvedValue({ data: receipt() });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); jest.restoreAllMocks();
  for (const [name, descriptor] of Object.entries(urlDescriptors)) {
    if (descriptor) Object.defineProperty(URL, name, descriptor); else delete URL[name];
  }
});
const render = async value => act(async () => root.render(<BalanceRecoveryProof context={value || context}/>));
const click = async text => act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === text).click());
const select = async value => act(async () => { const node = host.querySelector('select'); node.value = value;
  node.dispatchEvent(new Event('change', { bubbles: true })); });
const mockDownload = () => {
  const files = [];
  URL.createObjectURL = jest.fn(() => 'blob:receipt'); URL.revokeObjectURL = jest.fn();
  jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
    files.push({ href: this.href, name: this.download });
  });
  return files;
};
const readBlob = blob => new Promise((resolve, reject) => {
  const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject;
  reader.readAsText(blob);
});

test('opening never reads or writes; explicit read shows separate named original and unverified date sections', async () => {
  await render(); expect(http.get).not.toHaveBeenCalled(); expect(http.post).not.toHaveBeenCalled();
  await click('قراءة تقرير آخر احتساب');
  expect(http.get).toHaveBeenCalledWith('/operational-balances/recovery-proof', expect.objectContaining({ timeout: 8000, signal: expect.any(AbortSignal) }));
  expect(host.querySelector('tbody').textContent).toContain('سناب الدولار');
  expect(host.querySelector('tbody').textContent).toContain('2026-11-21');
  expect(host.querySelector('tbody').textContent).toContain('10.25');
  expect(host.querySelector('tbody').textContent).not.toContain('20.00');
  expect(host.textContent).toContain('لا يثبت اكتمال جميع المصادر');
  await select('unverified');
  expect(host.querySelector('tbody').textContent).toContain('20.00');
  expect(host.querySelector('tbody').textContent).not.toContain('10.25');
  expect(http.get).toHaveBeenCalledTimes(1); expect(http.post).not.toHaveBeenCalled();
});

test('large receipts render only twelve groups at a time and navigation never reads', async () => {
  http.get.mockResolvedValueOnce({ data: receipt({ new_original_dated: { ...empty(),
    obligations: Array.from({ length: 25 }, (_, i) => row({ business_date: `2026-11-${String(i + 1).padStart(2, '0')}` })) } }) });
  await render(); await click('قراءة تقرير آخر احتساب');
  expect(host.querySelectorAll('tbody tr')).toHaveLength(12);
  await click('التالي'); expect(host.querySelectorAll('tbody tr')).toHaveLength(12);
  await click('التالي'); expect(host.querySelectorAll('tbody tr')).toHaveLength(1);
  expect(http.get).toHaveBeenCalledTimes(1);
});

test('a backend unallocated supplier forecast remains visibly unassigned without losing the rest of the receipt', async () => {
  const unassigned = row({ entity_type: 'supplier', entity_id: null, currency: 'SAR',
    confirmed: '0.00', expected: '123.45', confirmed_count: 0 });
  const value = receipt({ new_date_unverified: { ...empty(), obligations: [unassigned] } });
  const original = JSON.stringify(value);
  http.get.mockResolvedValueOnce({ data: value });
  await render(); await click('قراءة تقرير آخر احتساب');
  expect(host.querySelector('tbody').textContent).toContain('سناب الدولار');
  expect(host.textContent).toContain('توجد سجلات دون جهة محددة');
  expect(host.textContent).toContain('تنزيل التقرير المقروء (JSON)');
  await select('unverified');
  expect(host.querySelector('tbody').textContent).toContain('الموردون — الجهة غير محددة');
  expect(host.querySelector('tbody').textContent).toContain('123.45');
  expect(host.querySelector('tbody').textContent).not.toContain('سناب الدولار');
  expect(value.proof.new_date_unverified.obligations[0].entity_id).toBeNull();
  expect(JSON.stringify(value)).toBe(original);
  expect(http.get).toHaveBeenCalledTimes(1); expect(http.post).not.toHaveBeenCalled();
});

test.each([undefined, '', 0, false, {}, [], 'x'.repeat(201)])(
  'an invalid group identity other than explicit null still blocks the receipt: %p', async entityId => {
    mockDownload();
    http.get.mockResolvedValueOnce({ data: receipt({ new_date_unverified: { ...empty(),
      obligations: [row({ entity_type: 'supplier', entity_id: entityId, confirmed: '0.00', expected: '12.00' })] } }) });
    await render(); await click('قراءة تقرير آخر احتساب');
    expect(host.textContent).toContain('تعذر التحقق'); expect(host.querySelector('table')).toBeNull();
    expect(host.textContent).not.toContain('تنزيل التقرير المقروء');
    expect(URL.createObjectURL).not.toHaveBeenCalled(); expect(http.post).not.toHaveBeenCalled();
  });

test('null cannot identify an entry in the named entity lookup', async () => {
  http.get.mockResolvedValueOnce({ data: { ...receipt(), entities: [
    { entity_type: 'supplier', entity_id: null, currency: 'SAR', label: 'اسم لا يثبت جهة' },
  ] } });
  await render(); await click('قراءة تقرير آخر احتساب');
  expect(host.textContent).toContain('تعذر التحقق');
  expect(host.textContent).not.toContain('اسم لا يثبت جهة');
  expect(host.textContent).not.toContain('تنزيل التقرير المقروء');
});

test('preservation changes and new duplicates stay visible independently of successful receipt parsing', async () => {
  http.get.mockResolvedValueOnce({ data: receipt({ complete: false, new_duplicate_keys: { request_id: 2 },
    preservation: { manual_movements: { preserved: false } }, changed_existing: { count: 1,
      before: { ...empty(), obligations: [row({ confirmed: '4.00' })] },
      after: { ...empty(), obligations: [row({ confirmed: '5.00' })] } } }) });
  await render(); await click('قراءة تقرير آخر احتساب');
  expect(host.textContent).toContain('القيود اليدوية: توجد تغييرات');
  expect(host.textContent).toContain('زيادة المعرفات المكررة: 2');
  expect(host.textContent).toContain('تغيّر 1 استحقاقًا سابقًا');
  await select('changed'); expect(host.querySelectorAll('tbody tr')).toHaveLength(2);
  expect(host.querySelector('tbody').textContent).toContain('قبل:'); expect(host.querySelector('tbody').textContent).toContain('بعد:');
});

test('missing or overflowed evidence is unknown and never rendered as zero restored', async () => {
  http.get.mockResolvedValueOnce({ data: { schema_version: 1, session_scope: 'scope', state: 'unknown', proof: null } });
  await render(); await click('قراءة تقرير آخر احتساب');
  expect(host.textContent).toContain('لا يعني ذلك أن عدد الحركات المستعادة صفر');
  expect(host.querySelector('table')).toBeNull();
});

test('staff cannot read and a mismatched scope cannot display proof names', async () => {
  await render({ ...context, actor: { id: 'staff', is_owner: false } });
  expect(host.textContent).toBe(''); expect(http.get).not.toHaveBeenCalled();
  http.get.mockResolvedValueOnce({ data: { ...receipt(), session_scope: 'foreign' } });
  await render(); await click('قراءة تقرير آخر احتساب');
  expect(host.textContent).toContain('تعذر التحقق'); expect(host.textContent).not.toContain('سناب الدولار');
});

test('scope change aborts the read and its delayed result cannot appear', async () => {
  const pending = deferred(); http.get.mockReturnValueOnce(pending.promise);
  await render(); await click('قراءة تقرير آخر احتساب');
  const signal = http.get.mock.calls[0][1].signal;
  await render({ ...context, session_scope: 'other-scope' }); expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve({ data: receipt() }));
  expect(host.textContent).not.toContain('سناب الدولار'); expect(host.querySelector('table')).toBeNull();
  expect(http.get).toHaveBeenCalledTimes(1);
});

test('hiding aborts a pending read; return does not poll or fetch', async () => {
  const pending = deferred(); http.get.mockReturnValueOnce(pending.promise);
  await render(); await click('قراءة تقرير آخر احتساب');
  const signal = http.get.mock.calls[0][1].signal;
  await act(async () => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange')); });
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve({ data: receipt() }));
  await act(async () => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange')); });
  expect(http.get).toHaveBeenCalledTimes(1); expect(host.querySelector('table')).toBeNull();
});

test('explicit download preserves the entire validated receipt including off-page rows and previous calculation metadata', async () => {
  const files = mockDownload();
  const value = receipt({ previous_as_of: '2026-11-21T14:50:00Z', previous_revision: 8,
    new_original_dated: { ...empty(), obligations: Array.from({ length: 13 }, (_, i) => row({
      business_date: `2026-11-${String(i + 1).padStart(2, '0')}`, confirmed_count: 1 })) },
    original_date_unknown_reasons: { provider_observation_or_aggregate: 1 },
    preservation: { manual_movements: { preserved: true, before_count: 4, before_digest: 'opaque-before', after_existing_digest: 'opaque-after' } },
    financial_delta_digest: 'opaque-financial-delta' });
  http.get.mockResolvedValueOnce({ data: value });
  await render(); expect(URL.createObjectURL).not.toHaveBeenCalled();
  await click('قراءة تقرير آخر احتساب');
  expect(host.textContent).toContain('الاحتساب المحفوظ السابق:');
  expect(host.textContent).toContain('النسخة السابقة: 8');
  expect(host.querySelectorAll('tbody tr')).toHaveLength(12);
  await click('تنزيل التقرير المقروء (JSON)');
  expect(files).toEqual([{ href: 'blob:receipt', name: 'mezan-recovery-proof-2026-11-24-r9.json' }]);
  const blob = URL.createObjectURL.mock.calls[0][0];
  expect(blob.type).toBe('application/json;charset=utf-8');
  expect(JSON.parse(await readBlob(blob))).toEqual(value);
  await act(async () => new Promise(resolve => setTimeout(resolve, 0)));
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:receipt');
  expect(document.querySelector('a[download]')).toBeNull();
  expect(http.get).toHaveBeenCalledTimes(1); expect(http.post).not.toHaveBeenCalled();
});

test('bounded incomplete proof exports its unknown reason and counts without inventing missing groups', async () => {
  mockDownload();
  const value = { schema_version: 1, session_scope: 'scope', state: 'available', entities: [],
    proof: { schema_version: 1, previous_as_of: null, previous_revision: 0, calculation_revision: 1,
      as_of: '2026-11-24T12:00:00Z', complete: false, source_completeness_verified: false,
      reason: 'metadata_limit', omitted_group_count: 1200, new_obligation_count: 2000 } };
  http.get.mockResolvedValueOnce({ data: value });
  await render(); await click('قراءة تقرير آخر احتساب');
  expect(host.textContent).toContain('الاحتساب المحفوظ السابق: غير متاح');
  expect(host.textContent).toContain('المقارنة غير مكتملة');
  expect(host.textContent).toContain('لا يُفترض أن عددها صفر');
  await click('تنزيل التقرير المقروء (JSON)');
  expect(JSON.parse(await readBlob(URL.createObjectURL.mock.calls[0][0]))).toEqual(value);
  await act(async () => new Promise(resolve => setTimeout(resolve, 0)));
  expect(http.get).toHaveBeenCalledTimes(1); expect(http.post).not.toHaveBeenCalled();
});

test('download preserves every assigned and unassigned group across all sections and pages without rewriting identities', async () => {
  const files = mockDownload();
  const unassigned = patch => row({ entity_type: 'supplier', entity_id: null, currency: 'SAR',
    confirmed: '0.00', expected: '12.50', confirmed_count: 0, ...patch });
  const value = receipt({ previous_revision: 8, previous_as_of: '2026-11-20T12:00:00Z',
    new_original_dated: { ...empty(), obligations: Array.from({ length: 13 }, (_, i) => row({
      business_date: `2026-11-${String(i + 1).padStart(2, '0')}` })) },
    new_date_unverified: { ...empty(), obligations: Array.from({ length: 13 }, (_, i) => unassigned({
      business_date: `2026-11-${String(i + 1).padStart(2, '0')}` })) },
    changed_existing: { count: 1, original_dates_verified: false,
      before: { ...empty(), obligations: [unassigned({ expected: '50.00' })] },
      after: { ...empty(), obligations: [unassigned({ expected: '60.00' })] } },
  });
  const original = JSON.stringify(value);
  http.get.mockResolvedValueOnce({ data: value });
  await render(); await click('قراءة تقرير آخر احتساب');
  expect(host.querySelectorAll('tbody tr')).toHaveLength(12);
  await select('unverified');
  expect(host.querySelectorAll('tbody tr')).toHaveLength(12);
  await click('التالي'); expect(host.querySelectorAll('tbody tr')).toHaveLength(1);
  expect(host.querySelector('tbody').textContent).toContain('الموردون — الجهة غير محددة');
  await select('changed'); expect(host.querySelectorAll('tbody tr')).toHaveLength(2);
  expect(host.querySelector('tbody').textContent).toContain('قبل:');
  expect(host.querySelector('tbody').textContent).toContain('بعد:');
  await click('تنزيل التقرير المقروء (JSON)');
  expect(files).toEqual([{ href: 'blob:receipt', name: 'mezan-recovery-proof-2026-11-24-r9.json' }]);
  const downloaded = JSON.parse(await readBlob(URL.createObjectURL.mock.calls[0][0]));
  expect(downloaded).toEqual(value);
  expect(downloaded.proof.new_date_unverified.obligations).toHaveLength(13);
  expect(downloaded.proof.new_date_unverified.obligations.every(item => item.entity_id === null)).toBe(true);
  expect(downloaded.proof.changed_existing.before.obligations[0].entity_id).toBeNull();
  expect(downloaded.proof.changed_existing.after.obligations[0].entity_id).toBeNull();
  expect(JSON.stringify(value)).toBe(original);
  expect(http.get).toHaveBeenCalledTimes(1); expect(http.post).not.toHaveBeenCalled();
});

test.each([
  { previous_revision: -1 }, { previous_revision: 9 },
  { previous_as_of: 'unknown' }, { previous_as_of: '2026-11-25T12:00:00Z' },
])('invalid previous metadata cannot display or download a receipt: %o', async patch => {
  mockDownload(); http.get.mockResolvedValueOnce({ data: receipt(patch) });
  await render(); await click('قراءة تقرير آخر احتساب');
  expect(host.textContent).toContain('تعذر التحقق'); expect(host.querySelector('table')).toBeNull();
  expect(host.textContent).not.toContain('تنزيل التقرير المقروء'); expect(URL.createObjectURL).not.toHaveBeenCalled();
});

test.each(['scope', 'owner'])('a previously loaded receipt cannot be downloaded after its %s changes', async change => {
  mockDownload(); await render(); await click('قراءة تقرير آخر احتساب');
  const previousButton = [...host.querySelectorAll('button')].find(button => button.textContent === 'تنزيل التقرير المقروء (JSON)');
  await render(change === 'scope' ? { ...context, session_scope: 'other-scope' }
    : { ...context, actor: { ...context.actor, is_owner: false } });
  expect(host.textContent).not.toContain('تنزيل التقرير المقروء');
  await act(async () => previousButton.click());
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  expect(http.get).toHaveBeenCalledTimes(1); expect(http.post).not.toHaveBeenCalled();
});
