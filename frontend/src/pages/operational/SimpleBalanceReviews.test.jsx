import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import SimpleBalanceReviews from './SimpleBalanceReviews';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('react-router-dom', () => {
  global.TextEncoder = require('util').TextEncoder;
  return jest.requireActual('react-router/dist/development/index.js');
});

jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: {
  reviews: jest.fn(), review: jest.fn(), reviewOptions: jest.fn(), reviewBankMovements: jest.fn(), reviewReceipt: jest.fn(), reviewDecision: jest.fn(),
}, simpleBalanceError: () => 'تعذر إتمام العملية' }));
global.IS_REACT_ACT_ENVIRONMENT = true;
const context = { session_scope: 'scope', permission_version: 1, operations: [{ key: 'courier.review' }], resources: [
  { kind: 'store_driver', id: 'driver', name: 'بلال التجريبي', access: { movements: 'all' } },
  { kind: 'bank', id: 'bank', name: 'بنك تجريبي', access: { use: true, movements: 'all' } },
] };
const review = { assignment_id: 'a1', driver_id: 'driver', driver_name: 'بلال التجريبي', order_number: '1001',
  payment_method: 'bank_transfer', bank_account_id: 'bank', amount: '300.00', status: 'pending',
  has_receipt: true, submitted_at: '2026-11-18T12:00:00Z' };
const options = { financial_source: 'operational', session_scope: 'scope', expected_review_revision: 1,
  can_approve: true, can_reject: true, banks: [{ id: 'bank', name: 'بنك تجريبي' }], pos_receivables: [] };
let host, root;
const click = async button => act(async () => button.click());
const button = name => [...host.querySelectorAll('button')].find(node => node.textContent === name);
const control = label => [...host.querySelectorAll('label')].find(node => node.textContent.includes(label)).querySelector('input,select,textarea');
const change = async (node, value) => act(async () => {
  const proto = node.tagName === 'SELECT' ? HTMLSelectElement.prototype : node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(node, value);
  node.dispatchEvent(new Event(node.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
});
const render = async (path = '/', overrides = {}) => act(async () => root.render(<MemoryRouter initialEntries={[path]}><SimpleBalanceReviews context={{ ...context, ...overrides }} identity="staff" onBack={jest.fn()} /></MemoryRouter>));
beforeEach(() => {
  jest.clearAllMocks();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  api.reviews.mockResolvedValue({ items: [review], total: 1, page: 1, page_size: 5, total_pages: 1 });
  api.review.mockResolvedValue(review); api.reviewOptions.mockResolvedValue(options);
  api.reviewBankMovements.mockResolvedValue({ items: [{ id: 'imported-100', movement_date: '2026-11-18',
    amount: '300.00', reference: 'BANK-100', description: 'تحويل عميل تجريبي', bank_account_id: 'bank' }], total: 1, page: 1, total_pages: 1 });
  api.reviewDecision.mockResolvedValue({ decision: 'approved', financial_source: 'operational' });
  api.reviewReceipt.mockResolvedValue(new Blob(['synthetic'], { type: 'image/png' }));
  URL.createObjectURL = jest.fn(() => 'blob:synthetic'); URL.revokeObjectURL = jest.fn();
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test('denied review permission makes no queue, proof, or decision request', async () => {
  await render('/?review_id=a1', { operations: [] });
  expect(host.textContent).toContain('لا توجد صلاحية');
  for (const method of Object.values(api)) expect(method).not.toHaveBeenCalled();
});

test('queue requests selected driver and status without loading proof or approval options', async () => {
  await render('/?review_driver=driver&review_status=approved&review_page=2');
  expect(api.reviews).toHaveBeenCalledWith({ status: 'approved', driver_id: 'driver', page: 2, page_size: 5 }, expect.any(Object));
  expect(api.reviewOptions).not.toHaveBeenCalled(); expect(api.reviewReceipt).not.toHaveBeenCalled();
});

test('proof Back preserves approval draft and bank approval needs matching proof and explicit check', async () => {
  await render('/?review_id=a1&review_driver=driver');
  expect(button('اعتماد الإيصال').disabled).toBe(true);
  await change(control('البنك المستلم'), 'bank');
  expect(api.reviewBankMovements).toHaveBeenCalledWith('a1', { page: 1, page_size: 5 }, expect.any(Object));
  await click(host.querySelector('input[type="radio"]'));
  expect(button('اعتماد الإيصال').disabled).toBe(true);
  await click(button('عرض الإيصال'));
  expect(host.querySelector('.sbr-proof')).toBeTruthy();
  expect([...host.querySelectorAll('button')].filter(node => node.textContent === 'رجوع' && !node.closest('[hidden]'))).toHaveLength(1);
  await click(host.querySelector('.sbr-proof button'));
  expect(host.querySelector('.sbr-proof')).toBeNull();
  expect(host.querySelector('input[type="radio"]').checked).toBe(true);
  expect(control('البنك المستلم').value).toBe('bank');
  await click(control('راجعت الإيصال'));
  await click(button('اعتماد الإيصال'));
  expect(api.reviewDecision).toHaveBeenCalledWith('a1', {
    decision: 'approved', note: 'تمت مطابقة الإيصال', expected_financial_source: 'operational',
    expected_review_revision: 1, expected_session_scope: 'scope',
    destination_financial_id: 'bank', settlement_reference: 'imported-100',
  });
  expect(host.textContent).toContain('تم الاعتماد');
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic');
});

test('missing order numbers do not turn assignment IDs into order numbers and decision audit is visible', async () => {
  api.review.mockResolvedValue({ ...review, order_number: null, customer_name: 'عميل معروف', status: 'approved',
    reviewed_by_name: 'مراجع تجريبي', reviewed_at: '2026-11-19T10:15:00Z' });
  await render('/?review_id=a1');
  expect(host.textContent).toContain('عميل معروف');
  expect(host.textContent).toContain('مراجع تجريبي');
  expect(host.textContent).toContain('2026');
  expect(host.textContent).not.toContain('#undefined');
  expect(host.textContent).not.toContain('#a1');
  expect(api.reviewBankMovements).not.toHaveBeenCalled();
});

test('receipt amounts preserve exact saved decimals beyond Number precision', async () => {
  api.review.mockResolvedValue({ ...review, amount: '9007199254740993.25', status: 'approved' });
  await render('/?review_id=a1');
  expect(host.textContent).toContain('9,007,199,254,740,993.25');
});

test('uncertain decision can only retry the identical request and rejection needs a reason', async () => {
  await render('/?review_id=a1');
  expect(button('رفض الإيصال').disabled).toBe(true);
  await change(control('ملاحظة'), 'الصورة غير واضحة');
  api.reviewDecision.mockRejectedValueOnce(new Error('network uncertain')).mockResolvedValueOnce({ decision: 'rejected' });
  await click(button('رفض الإيصال'));
  const first = api.reviewDecision.mock.calls[0][1];
  expect(control('ملاحظة').matches(':disabled')).toBe(true);
  expect(button('اعتماد الإيصال')).toBeUndefined();
  await click(button('إعادة نفس الطلب'));
  expect(api.reviewDecision.mock.calls[1][1]).toEqual(first);
  expect(host.textContent).toContain('الإيصال مرفوض');
});
