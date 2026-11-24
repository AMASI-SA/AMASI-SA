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
let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true; jest.clearAllMocks();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  http.get.mockResolvedValue({ data: receipt() });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const render = async value => act(async () => root.render(<BalanceRecoveryProof context={value || context}/>));
const click = async text => act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === text).click());
const select = async value => act(async () => { const node = host.querySelector('select'); node.value = value;
  node.dispatchEvent(new Event('change', { bubbles: true })); });

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
