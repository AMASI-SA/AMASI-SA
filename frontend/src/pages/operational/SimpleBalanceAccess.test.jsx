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
const labeledControl = name => {
  const label = [...host.querySelectorAll('label')].find(node => [...node.childNodes].some(child => child.textContent.trim() === name));
  const control = host.querySelector(`[aria-label="${name}"]`) || (label?.htmlFor ? global.document.getElementById(label.htmlFor) : label?.querySelector('input,select'));
  expect(control).toBeTruthy();
  return control;
};
const buttonNamed = name => {
  const button = [...host.querySelectorAll('button')].find(node => node.textContent === name);
  expect(button).toBeTruthy();
  return button;
};
const changeValue = async (control, value) => act(async () => {
  const select = control.tagName === 'SELECT';
  Object.getOwnPropertyDescriptor(select ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value').set.call(control, value);
  control.dispatchEvent(new Event(select ? 'change' : 'input', { bubbles: true }));
});
const supplierCatalog = {
  operations: [...catalog.operations, { key: 'supplier.payment', label: 'دفع مبلغ لمورد' }],
  resources: [
    { kind: 'supplier', id: 'shared', name: 'المورد الأول' },
    { kind: 'supplier', id: 'supplier-b', name: 'المورد الثاني' },
    { kind: 'supplier', id: 'supplier-c', name: 'المورد الثالث' },
    { kind: 'bank', id: 'shared', name: 'البنك المشترك' },
    { kind: 'external_person', id: 'external', name: 'شركة آفاق' },
  ],
  resource_kinds: [{ key: 'supplier', label: 'الموردون' }, ...catalog.resource_kinds],
};
const bulkNames = ['استخدام الموردين', 'حركات الموردين', 'أرصدة الموردين'];
const applySuppliers = () => buttonNamed('تطبيق على جميع الموردين (3)');
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

test('bulk use covers every current supplier despite search, preserves mixed grants and same-ID accounts, and waits for save', async () => {
  const existing = [
    { kind: 'supplier', id: 'shared', use: false, movements: 'own', balance: true },
    { kind: 'supplier', id: 'supplier-b', use: true, movements: 'all', balance: false },
    { kind: 'bank', id: 'shared', use: true, movements: 'none', balance: false },
    { kind: 'external_person', id: 'external', use: false, movements: 'all', balance: true },
  ];
  api.userAccess.mockResolvedValue({ user, access: { version: 11, enabled: true, operations: ['external.pay'], resources: existing } });
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  expect([...host.querySelectorAll('legend')].map(node => node.textContent)).toContain('صلاحيات جميع الموردين');
  expect(bulkNames.map(name => labeledControl(name).value)).toEqual(['', '', '']);
  expect(applySuppliers().disabled).toBe(true);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'supplier');
  await changeValue(host.querySelector('[placeholder="اسم البنك أو الموظف أو الجهة"]'), 'المورد الأول');
  expect(host.querySelectorAll('.sb-access-table tbody tr')).toHaveLength(1);
  await changeValue(labeledControl('استخدام الموردين'), 'allow');
  await click(applySuppliers());
  expect(api.saveUserAccess).not.toHaveBeenCalled();
  expect(api.userAccess).toHaveBeenCalledTimes(1);
  expect(host.querySelectorAll('.sb-access-table tbody tr')).toHaveLength(3);
  expect(labeledControl('نوع الحساب أو الجهة').value).toBe('supplier');
  expect(host.querySelector('[placeholder="اسم البنك أو الموظف أو الجهة"]').value).toBe('');
  expect(bulkNames.map(name => labeledControl(name).value)).toEqual(['', '', '']);
  expect(labeledControl('استخدام المورد الأول').checked).toBe(true);
  expect(labeledControl('حركات المورد الأول').value).toBe('own');
  expect(labeledControl('رصيد المورد الأول').checked).toBe(true);
  expect(labeledControl('حركات المورد الثاني').value).toBe('all');
  expect(labeledControl('رصيد المورد الثاني').checked).toBe(false);
  await submit();
  expect(api.saveUserAccess).toHaveBeenCalledTimes(1);
  const [target, payload] = api.saveUserAccess.mock.calls[0];
  expect(target).toBe('staff');
  expect(payload).toEqual({ expected_version: 11, enabled: true, operations: ['external.pay'], resources: expect.any(Array) });
  expect(payload.resources).toHaveLength(5);
  expect(payload.resources).toEqual(expect.arrayContaining([
    { kind: 'supplier', id: 'shared', use: true, movements: 'own', balance: true },
    { kind: 'supplier', id: 'supplier-b', use: true, movements: 'all', balance: false },
    { kind: 'supplier', id: 'supplier-c', use: true, movements: 'none', balance: false },
    { kind: 'bank', id: 'shared', use: true, movements: 'none', balance: false },
    { kind: 'external_person', id: 'external', use: false, movements: 'all', balance: true },
  ]));
});

test('bulk movement visibility permits a per-supplier exception without granting use, balances, or operations', async () => {
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  await changeValue(labeledControl('حركات الموردين'), 'all');
  await click(applySuppliers());
  await changeValue(labeledControl('حركات المورد الثاني'), 'own');
  expect(api.saveUserAccess).not.toHaveBeenCalled();
  await submit();
  const payload = api.saveUserAccess.mock.calls[0][1];
  expect(payload.operations).toEqual([]);
  expect(payload.resources).toHaveLength(3);
  expect(payload.resources).toEqual(expect.arrayContaining([
    { kind: 'supplier', id: 'shared', use: false, movements: 'all', balance: false },
    { kind: 'supplier', id: 'supplier-b', use: false, movements: 'own', balance: false },
    { kind: 'supplier', id: 'supplier-c', use: false, movements: 'all', balance: false },
  ]));
});

test('bulk deny applies false permissions and save omits fully denied supplier grants without revoking other accounts', async () => {
  const bank = { kind: 'bank', id: 'shared', use: true, movements: 'all', balance: true };
  api.userAccess.mockResolvedValue({ user, access: {
    version: 4, enabled: true, operations: ['supplier.payment'], resources: [
      { kind: 'supplier', id: 'shared', use: true, movements: 'all', balance: true },
      { kind: 'supplier', id: 'supplier-b', use: true, movements: 'own', balance: true },
      { kind: 'supplier', id: 'supplier-c', use: false, movements: 'none', balance: true }, bank,
    ],
  } });
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  await changeValue(labeledControl('استخدام الموردين'), 'deny');
  await changeValue(labeledControl('حركات الموردين'), 'none');
  await changeValue(labeledControl('أرصدة الموردين'), 'deny');
  await click(applySuppliers());
  for (const name of ['المورد الأول', 'المورد الثاني', 'المورد الثالث']) {
    expect(labeledControl(`استخدام ${name}`).checked).toBe(false);
    expect(labeledControl(`حركات ${name}`).value).toBe('none');
    expect(labeledControl(`رصيد ${name}`).checked).toBe(false);
  }
  await submit();
  expect(api.saveUserAccess).toHaveBeenCalledWith('staff', {
    expected_version: 4, enabled: true, operations: ['supplier.payment'], resources: [bank],
  });
});

test('bulk controls cannot apply to an empty supplier catalogue or save an untouched draft', async () => {
  await render(<AccessEditor user={user} catalog={catalog} identity="owner" />);
  const apply = buttonNamed('تطبيق على جميع الموردين (0)');
  expect(apply.disabled).toBe(true);
  expect(bulkNames.map(name => labeledControl(name).value)).toEqual(['', '', '']);
  await changeValue(labeledControl('استخدام الموردين'), 'allow');
  expect(apply.disabled).toBe(true);
  await click(apply);
  expect(buttonNamed('حفظ صلاحيات الموظف').disabled).toBe(true);
  expect(api.saveUserAccess).not.toHaveBeenCalled();
});

test('applying an already matching bulk setting leaves the saved draft clean', async () => {
  api.userAccess.mockResolvedValue({ user, access: {
    version: 3, enabled: true, operations: [], resources: [
      { kind: 'supplier', id: 'shared', use: true, movements: 'own', balance: true },
      { kind: 'supplier', id: 'supplier-b', use: true, movements: 'none', balance: false },
      { kind: 'supplier', id: 'supplier-c', use: true, movements: 'all', balance: false },
    ],
  } });
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  expect(applySuppliers().disabled).toBe(true);
  await changeValue(labeledControl('استخدام الموردين'), 'allow');
  await click(applySuppliers());
  expect(buttonNamed('حفظ صلاحيات الموظف').disabled).toBe(true);
  expect(host.textContent).not.toContain('تغييرات لم تُحفظ');
  expect(host.querySelector('[role="status"]')?.textContent).toBeTruthy();
  expect(api.saveUserAccess).not.toHaveBeenCalled();
});

test('bulk selections reset on reload and cannot change the draft during a pending save', async () => {
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  await changeValue(labeledControl('استخدام الموردين'), 'allow');
  await click(buttonNamed('إعادة قراءة النسخة المحفوظة'));
  expect(api.userAccess).toHaveBeenCalledTimes(2);
  expect(bulkNames.map(name => labeledControl(name).value)).toEqual(['', '', '']);
  await changeValue(labeledControl('استخدام الموردين'), 'allow');
  await click(applySuppliers());
  let finishSave;
  api.saveUserAccess.mockImplementation(() => new Promise(resolve => { finishSave = resolve; }));
  await submit();
  for (const name of bulkNames) expect(labeledControl(name).matches(':disabled')).toBe(true);
  expect(applySuppliers().matches(':disabled')).toBe(true);
  expect(api.saveUserAccess).toHaveBeenCalledTimes(1);
  const payload = api.saveUserAccess.mock.calls[0][1];
  await act(async () => finishSave({ user, access: { ...payload, version: 4 } }));
  expect(labeledControl('استخدام الموردين').matches(':disabled')).toBe(false);
  expect(buttonNamed('حفظ صلاحيات الموظف').disabled).toBe(true);
});
