import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import SimpleBalanceAccess, { AccessEditor } from './SimpleBalanceAccess';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { useOptionalAuth } from '../../context/AuthContext';

jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: { accessCatalog: jest.fn(), accessUsers: jest.fn(), userAccess: jest.fn(), saveUserAccess: jest.fn() }, simpleBalanceError: () => 'تعذر تحميل الصلاحيات' }));
jest.mock('../../context/AuthContext', () => ({ useOptionalAuth: jest.fn() }));
jest.mock('react-router-dom', () => ({ Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
global.IS_REACT_ACT_ENVIRONMENT = true;
const user = { id: 'staff', name: 'محمد', role: 'employee' };
const catalog = {
  operations: [{ key: 'external.pay', label: 'تسليم مبلغ لجهة خارجية' }, { key: 'employee.payment', label: 'دفع مستحق موظف' }],
  resources: [{ kind: 'bank', id: 'bank', name: 'بنك المؤسسة', currency: 'SAR' }, { kind: 'external_person', id: 'external', name: 'شركة آفاق', currency: 'SAR' }],
  resource_kinds: [{ key: 'bank', label: 'البنوك' }, { key: 'external_person', label: 'الجهات الخارجية' }],
};
const document = (version = 3) => ({ user, access: { version, enabled: true, operations: [], resources: [] } });
let host, root;
const render = async element => act(async () => root.render(element));
const click = async element => act(async () => element.click());
const submit = async () => act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
beforeEach(() => {
  jest.clearAllMocks();
  Object.defineProperty(global.document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  host = global.document.createElement('div'); global.document.body.appendChild(host); root = createRoot(host);
  useOptionalAuth.mockReturnValue({ user: { id: 'owner', is_owner: true } });
  api.userAccess.mockResolvedValue(document()); api.accessCatalog.mockResolvedValue(catalog); api.accessUsers.mockResolvedValue({ items: [user] });
  api.saveUserAccess.mockImplementation((id, body) => Promise.resolve({ user, access: { ...body, version: body.expected_version + 1 } }));
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test('resource use, own-movement visibility, balance, and operation are independently saved with the loaded version', async () => {
  await render(<AccessEditor user={user} catalog={catalog} identity="owner" />);
  await click(host.querySelector('[aria-label="استخدام بنك المؤسسة"]'));
  const movements = host.querySelector('[aria-label="حركات شركة آفاق"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(movements, 'own');
    movements.dispatchEvent(new Event('change', { bubbles: true }));
  });
  const operation = [...host.querySelectorAll('label')].find(label => label.textContent.includes('تسليم مبلغ لجهة خارجية')).querySelector('input');
  await click(operation);
  expect(api.saveUserAccess).not.toHaveBeenCalled();
  await submit();
  expect(api.saveUserAccess).toHaveBeenCalledTimes(1);
  expect(api.saveUserAccess).toHaveBeenCalledWith('staff', {
    expected_version: 3, enabled: true, operations: ['external.pay'], resources: [
      { kind: 'bank', id: 'bank', use: true, movements: 'none', balance: false },
      { kind: 'external_person', id: 'external', use: false, movements: 'own', balance: false },
    ],
  });
  expect(host.textContent).toContain('تم حفظ صلاحيات محمد');
});

test('a stale-version conflict keeps the proposed grants and requires explicit reload', async () => {
  api.saveUserAccess.mockRejectedValue({ response: { status: 409, data: { detail: { code: 'operational_access_changed' } } } });
  await render(<AccessEditor user={user} catalog={catalog} identity="owner" />);
  await click(host.querySelector('[aria-label="استخدام بنك المؤسسة"]')); await submit();
  expect(host.querySelector('[aria-label="استخدام بنك المؤسسة"]').checked).toBe(true);
  expect(host.textContent).toContain('عدّل شخص آخر'); expect(api.userAccess).toHaveBeenCalledTimes(1);
  expect([...host.querySelectorAll('button')].find(button => button.textContent === 'حفظ صلاحيات الموظف').disabled).toBe(true);
  api.userAccess.mockResolvedValue(document(4));
  await click([...host.querySelectorAll('button')].find(button => button.textContent.includes('تحميل آخر نسخة')));
  expect(api.userAccess).toHaveBeenCalledTimes(2);
  expect(host.querySelector('[aria-label="استخدام بنك المؤسسة"]').checked).toBe(false);
});

test('each responsive account row has one labeled control per permission and balance remains independent', async () => {
  await render(<AccessEditor user={user} catalog={catalog} identity="owner" />);
  const rows = [...host.querySelectorAll('.sb-access-table tbody tr')];
  expect(rows).toHaveLength(2);
  for (const [index, row] of rows.entries()) {
    expect(row.querySelector('th[scope="row"]').textContent).toContain(catalog.resources[index].name);
    expect([...row.querySelectorAll('.sb-access-mobile-label')].map(label => label.textContent))
      .toEqual(['استخدام الحساب', 'الحركات المعروضة', 'عرض الرصيد']);
    expect(row.querySelectorAll('input,select')).toHaveLength(3);
    expect([...row.querySelectorAll('label')].every(label => label.querySelectorAll('input,select').length === 1)).toBe(true);
  }
  const bankBalance = rows[0].querySelector('[aria-label="رصيد بنك المؤسسة"]');
  await click(bankBalance.closest('label'));
  expect(bankBalance.checked).toBe(true);
  expect(rows[0].querySelector('[aria-label="استخدام بنك المؤسسة"]').checked).toBe(false);
  expect(rows[0].querySelector('select').value).toBe('none');
  await submit();
  expect(api.saveUserAccess.mock.calls[0][1].resources).toEqual([
    { kind: 'bank', id: 'bank', use: false, movements: 'none', balance: true },
  ]);
});

test('a non-owner never loads the owner resource catalogue or employee roster', async () => {
  useOptionalAuth.mockReturnValue({ user: { id: 'staff', is_owner: false } });
  await render(<SimpleBalanceAccess />);
  expect(api.accessCatalog).not.toHaveBeenCalled(); expect(api.accessUsers).not.toHaveBeenCalled();
  expect(host.querySelector('[role="alert"]').textContent).toContain('مالك الحساب');
});

test('the owner account is not offered as a configurable employee', async () => {
  api.accessUsers.mockResolvedValue({ items: [{ id: 'owner', name: 'مالك الحساب', role: 'owner' }, user] });
  await render(<SimpleBalanceAccess />);
  expect([...host.querySelector('select').options].map(option => option.value)).toEqual(['', 'staff']);
  expect(api.userAccess).not.toHaveBeenCalled();
});
