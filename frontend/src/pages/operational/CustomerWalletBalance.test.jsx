import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import CustomerWalletBalance from './CustomerWalletBalance';
import { operationalApi as api, requestId } from './api';

jest.mock('./api', () => ({
  operationalApi: { customerWalletContext: jest.fn(), saveCustomerWalletSnapshot: jest.fn() },
  requestId: jest.fn(), messageFor: () => 'تعذر حفظ الكشف',
}));

const scope = 'wallet-test-scope';
let host, root, onSaved;
const context = () => ({ session_scope: scope, can_manage: true, status: 'active', merchant_id: 'test-store',
  balances: { SAR: { remaining: '150.25', as_of: '2026-11-03T12:00:00Z' } } });
const button = label => [...host.querySelectorAll('button')].find(node => node.textContent.includes(label));
const render = async (sessionScope = scope) => act(async () => root.render(
  <CustomerWalletBalance key={sessionScope} sessionScope={sessionScope} onSaved={onSaved} />));
const open = async () => act(async () => button('تحديث رصيد').click());
const fill = async (label, value) => act(async () => {
  const input = host.querySelector(`[aria-label="${label}"]`);
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
});
const submit = async () => act(async () => host.querySelector('form').dispatchEvent(
  new Event('submit', { bubbles: true, cancelable: true })));
const fillSnapshot = async () => {
  await fill('الرصيد المتبقي للعملاء بالريال', '125.20');
  await fill('تاريخ كشف سلة بتوقيت الرياض', '2026-11-04T16:30:00');
  await fill('مرجع كشف سلة', 'كشف محافظ سلة التجريبي');
};

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  onSaved = jest.fn();
  requestId.mockReturnValue('wallet-request-test');
  api.customerWalletContext.mockResolvedValue(context());
  api.saveCustomerWalletSnapshot.mockResolvedValue({ session_scope: scope });
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

test('opens the owner-bound source form and submits the remaining balance at Riyadh time', async () => {
  await render();
  expect(api.customerWalletContext).not.toHaveBeenCalled();
  await open();
  expect(host.textContent).toContain('150.25 SAR');
  await fillSnapshot();
  await submit();
  expect(api.saveCustomerWalletSnapshot).toHaveBeenCalledWith({
    request_id: 'wallet-request-test', expected_session_scope: scope,
    merchant_id: 'test-store', currency: 'SAR', remaining_balance: '125.20',
    as_of: '2026-11-04T16:30+03:00', source_reference: 'كشف محافظ سلة التجريبي',
  });
  expect(onSaved).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain('حُفظ الرصيد ضمن المبالغ المستحقة للعملاء علينا');
});

test('retries the identical request after a transport error', async () => {
  api.saveCustomerWalletSnapshot.mockRejectedValueOnce(Error('انقطع الاتصال'));
  await render(); await open(); await fillSnapshot(); await submit();
  expect(onSaved).not.toHaveBeenCalled();
  await submit();
  expect(requestId).toHaveBeenCalledTimes(1);
  expect(api.saveCustomerWalletSnapshot.mock.calls[1][0]).toEqual(api.saveCustomerWalletSnapshot.mock.calls[0][0]);
  expect(onSaved).toHaveBeenCalledTimes(1);
});

test('a mismatched context or denied owner permission cannot expose the form', async () => {
  api.customerWalletContext.mockResolvedValueOnce({ ...context(), session_scope: 'other-owner' });
  await render(); await open();
  expect(host.querySelector('form')).toBeNull();
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  await render('denied');
  api.customerWalletContext.mockResolvedValueOnce({ ...context(), session_scope: 'denied', can_manage: false });
  await open();
  expect(host.querySelector('form')).toBeNull();
  expect(api.saveCustomerWalletSnapshot).not.toHaveBeenCalled();
});

test('a successful response in a different session does not confirm the saved balance', async () => {
  api.saveCustomerWalletSnapshot.mockResolvedValueOnce({ session_scope: 'other-owner' });
  await render(); await open(); await fillSnapshot(); await submit();
  expect(onSaved).not.toHaveBeenCalled();
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

test('changing owners while a save is pending ignores the old result', async () => {
  let resolve;
  api.saveCustomerWalletSnapshot.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await render(); await open(); await fillSnapshot();
  await act(async () => { host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
  await render('new-owner');
  await act(async () => resolve({ session_scope: scope }));
  expect(onSaved).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain('150.25');
});
