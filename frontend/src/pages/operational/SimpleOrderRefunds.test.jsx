import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import SimpleOrderRefunds, { RefundDetails, RefundRequestForm } from './SimpleOrderRefunds';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: { refundOrder: jest.fn(), refunds: jest.fn(), refund: jest.fn(), requestRefund: jest.fn(), confirmRefund: jest.fn(), cancelRefund: jest.fn(), reconcileRefund: jest.fn() }, simpleBalanceError: error => error?.localMessage || 'لم تتأكد نتيجة الحفظ' }));
jest.mock('react-router-dom', () => ({ useSearchParams: () => {
  const ReactModule = require('react');
  const [params, setParams] = ReactModule.useState(() => new URLSearchParams(globalThis.window.location.search));
  return [params, values => { const next = new URLSearchParams(values); globalThis.window.history.replaceState({}, '', `/?${next}`); setParams(next); }];
} }));
global.IS_REACT_ACT_ENVIRONMENT = true;
const context = () => ({ actor: { id: 'staff', name: 'محمد' }, session_scope: 'scope', permission_version: 1, status: 'active',
  operations: [{ key: 'customer.refund_request' }, { key: 'customer.refund_execute' }] });
const order = patch => ({ id: 'order-id', order_number: '2042', total: '300.00', currency: 'SAR', paid_amount: '300.00',
  refunded_amount: '0.00', reserved_amount: '0.00', available_amount: '300.00', can_request: true,
  methods: [{ id: 'bank', label: 'تحويل بنكي' }, { id: 'original', label: 'تمارا' }], banks: [{ id: 'bank', name: 'بنك المؤسسة', currency: 'SAR' }], ...patch });
const refund = patch => ({ id: 'refund-one', order_number: '2042', amount: '50.00', currency: 'SAR', method: 'bank',
  source: { kind: 'bank', id: 'bank', name: 'بنك المؤسسة' }, status: 'pending', reason: 'استرجاع جزئي', request_reference: '',
  recorded_by: { id: 'staff', name: 'محمد' }, created_at: '2026-11-01T10:00:00Z', can_confirm: true, can_cancel: true, ...patch });
let host, root, saved, counter;
const render = async element => act(async () => root.render(element));
const field = text => [...host.querySelectorAll('label')].find(label => label.querySelector('span')?.textContent === text)?.querySelector('input,select,textarea');
const change = async (label, value) => {
  const input = field(label);
  if (!input) throw Error(`Missing field ${label}`);
  const proto = input.tagName === 'SELECT' ? HTMLSelectElement.prototype : input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => { Object.getOwnPropertyDescriptor(proto, 'value').set.call(input, value); input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
};
const click = async label => act(async () => {
  const button = [...host.querySelectorAll('button')].find(item => item.textContent === label);
  if (!button) throw Error(`Missing button ${label}`);
  button.click();
});
const submit = async () => act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
beforeEach(() => {
  jest.clearAllMocks(); localStorage.clear(); window.history.replaceState({}, '', '/');
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  counter = 0; Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { randomUUID: () => `refund-request-${++counter}` } });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host); saved = jest.fn();
  api.requestRefund.mockResolvedValue({ saved: true, item: refund() });
  api.confirmRefund.mockResolvedValue({ saved: true, item: refund({ status: 'refunded', can_confirm: false }) });
  api.cancelRefund.mockResolvedValue({ saved: true, item: refund({ status: 'cancelled', can_cancel: false }) });
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test('the refund method starts empty, reveals only the institution bank, and records a financial request without item returns', async () => {
  await render(<RefundRequestForm context={context()} order={order()} onSaved={saved} />);
  expect(field('طريقة الاسترجاع').value).toBe('');
  expect(field('بنك المؤسسة الذي سيخرج منه المبلغ')).toBeUndefined();
  await change('المبلغ المسترجع', '50'); await change('سبب الاسترجاع', 'رد جزئي'); await change('طريقة الاسترجاع', 'bank');
  await change('بنك المؤسسة الذي سيخرج منه المبلغ', 'bank'); await submit();
  expect(api.requestRefund).toHaveBeenCalledWith(expect.objectContaining({ order_number: '2042', amount: '50.00', method: 'bank', bank_id: 'bank', reason: 'رد جزئي', expected_session_scope: 'scope' }));
  expect(api.requestRefund.mock.calls[0][0].items).toBeUndefined();
  expect(api.confirmRefund).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain('آيبان');
});

test('changing to the payment platform retains amount and reason but never submits the stale bank', async () => {
  await render(<RefundRequestForm context={context()} order={order()} onSaved={saved} />);
  await change('المبلغ المسترجع', '50'); await change('سبب الاسترجاع', 'رد جزء من المبلغ');
  await change('طريقة الاسترجاع', 'bank'); await change('بنك المؤسسة الذي سيخرج منه المبلغ', 'bank');
  await change('طريقة الاسترجاع', 'original');
  expect(field('بنك المؤسسة الذي سيخرج منه المبلغ')).toBeUndefined();
  expect(field('المبلغ المسترجع').value).toBe('50'); expect(field('سبب الاسترجاع').value).toBe('رد جزء من المبلغ');
  await submit(); expect(api.requestRefund.mock.calls[0][0].method).toBe('original');
  expect(api.requestRefund.mock.calls[0][0].bank_id).toBeUndefined();
});

test('partial 50 then full available 250 uses the fresh reserved ceiling and distinct intents', async () => {
  await render(<RefundRequestForm key="first" context={context()} order={order()} onSaved={saved} />);
  await change('المبلغ المسترجع', '50'); await change('سبب الاسترجاع', 'جزئي'); await change('طريقة الاسترجاع', 'bank'); await change('بنك المؤسسة الذي سيخرج منه المبلغ', 'bank'); await submit();
  const first = api.requestRefund.mock.calls[0][0];
  await render(<RefundRequestForm key="after-reservation" context={context()} order={order({ reserved_amount: '50.00', available_amount: '250.00' })} onSaved={saved} />);
  await click('رد كامل المتاح'); await change('سبب الاسترجاع', 'الباقي'); await change('طريقة الاسترجاع', 'bank'); await change('بنك المؤسسة الذي سيخرج منه المبلغ', 'bank'); await submit();
  const second = api.requestRefund.mock.calls[1][0];
  expect(first.amount).toBe('50.00'); expect(second.amount).toBe('250.00'); expect(second.request_id).not.toBe(first.request_id);
});

test('requests above available and requests for an unpaid order do not submit', async () => {
  await render(<RefundRequestForm key="paid" context={context()} order={order()} onSaved={saved} />);
  await change('المبلغ المسترجع', '301'); await change('سبب الاسترجاع', 'رد'); await change('طريقة الاسترجاع', 'bank'); await change('بنك المؤسسة الذي سيخرج منه المبلغ', 'bank'); await submit();
  expect(api.requestRefund).not.toHaveBeenCalled(); expect(host.querySelector('[role="alert"]').textContent).toContain('ضمن المتاح');
  await render(<RefundRequestForm key="unpaid" context={context()} order={order({ paid_amount: '0.00', available_amount: '0.00' })} onSaved={saved} />);
  expect(host.querySelector('form')).toBeNull(); expect(host.textContent).toContain('لا يوجد مبلغ متاح');
});

test('bank confirmation accepts no reference, fixes the source and amount, and submits once', async () => {
  await render(<RefundDetails context={context()} item={refund()} onBack={() => {}} onSaved={saved} />);
  expect(field('بنك المؤسسة الذي سيخرج منه المبلغ')).toBeUndefined();
  expect(field('المبلغ المسترجع')).toBeUndefined();
  await act(async () => host.querySelector('input[type="checkbox"]').click());
  await submit(); await submit();
  expect(api.confirmRefund).toHaveBeenCalledTimes(1);
  expect(api.confirmRefund.mock.calls[0][0]).toBe('refund-one');
  const body = api.confirmRefund.mock.calls[0][1];
  expect(body).toEqual(expect.objectContaining({ confirmed: true, reference: '', expected_session_scope: 'scope' }));
  expect(Number.isNaN(Date.parse(body.executed_at))).toBe(false);
  expect(body.amount).toBeUndefined(); expect(body.bank_id).toBeUndefined();
});

test('a platform request never exposes a local bank confirmation even if a malformed flag says it can', async () => {
  await render(<RefundDetails context={context()} item={refund({ method: 'original', source: { kind: 'provider', id: 'tamara', name: 'تمارا' }, can_confirm: true, can_cancel: false })} onBack={() => {}} onSaved={saved} />);
  expect(host.textContent).toContain('بانتظار تأكيد تمارا');
  expect(host.querySelector('input[type="checkbox"]')).toBeNull(); expect(host.querySelector('form')).toBeNull();
  expect(api.confirmRefund).not.toHaveBeenCalled();
});

test('a confirmed no-write conflict releases the form for correction; uncertain transport retries the same request', async () => {
  api.requestRefund.mockRejectedValueOnce({ response: { status: 409, data: { detail: { not_applied: true } } } });
  await render(<RefundRequestForm context={context()} order={order()} onSaved={saved} />);
  await change('المبلغ المسترجع', '50'); await change('سبب الاسترجاع', 'رد'); await change('طريقة الاسترجاع', 'bank'); await change('بنك المؤسسة الذي سيخرج منه المبلغ', 'bank'); await submit();
  expect(field('المبلغ المسترجع').disabled).toBe(false); expect(localStorage.length).toBe(0);
  await change('المبلغ المسترجع', '40'); api.requestRefund.mockRejectedValueOnce(new Error('unknown result')); await submit();
  const uncertain = api.requestRefund.mock.calls[1][0]; expect(field('المبلغ المسترجع').disabled).toBe(true);
  await submit(); expect(api.requestRefund.mock.calls[2][0]).toEqual(uncertain);
});

test('a platform refund can only match a server supplied confirmation without creating another payment', async () => {
  const platform = refund({ method: 'original', source: { kind: 'provider', id: 'tamara', name: 'تمارا' },
    can_confirm: false, can_cancel: false, can_reconcile: true,
    provider_evidence: [{ id: 'fact-1', reference: 'T-445', amount: '50.00', fingerprint: 'b'.repeat(64) }] });
  api.reconcileRefund.mockResolvedValue({ saved: true, item: { ...platform, status: 'refunded' } });
  await render(<RefundDetails context={context()} item={platform} onBack={() => {}} onSaved={saved} />);
  expect(field('مرجع التحويل (اختياري)')).toBeUndefined();
  await submit(); expect(api.reconcileRefund).not.toHaveBeenCalled();
  await change('تأكيد المنصة', 'fact-1'); await submit(); await submit();
  expect(api.reconcileRefund).toHaveBeenCalledTimes(1);
  expect(api.reconcileRefund).toHaveBeenCalledWith('refund-one', expect.objectContaining({
    evidence_id: 'fact-1', expected_evidence_hash: 'b'.repeat(64), expected_session_scope: 'scope',
  }));
  expect(api.confirmRefund).not.toHaveBeenCalled();
});

test('refund history is paginated in five rows and keeps recorder attribution', async () => {
  api.refunds.mockImplementation(query => Promise.resolve({ page: query.page, page_size: 5, total: 6, total_pages: 2,
    items: Array.from({ length: query.page === 1 ? 5 : 1 }, (_, index) => refund({ id: `refund-${query.page}-${index}` })) }));
  await render(<SimpleOrderRefunds context={context()} identity="staff" onBalancesChanged={() => {}} onExit={() => {}} />);
  expect(host.querySelectorAll('tbody tr')).toHaveLength(5); expect(host.textContent).toContain('سجّلها: محمد');
  await click('التالي'); expect(host.querySelectorAll('tbody tr')).toHaveLength(1);
  expect(api.refunds).toHaveBeenLastCalledWith({ page: 2, page_size: 5 }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
});
