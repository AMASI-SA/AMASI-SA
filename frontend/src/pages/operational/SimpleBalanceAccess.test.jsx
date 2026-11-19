import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import SimpleBalanceAccess, { AccessEditor } from './SimpleBalanceAccess';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { useOptionalAuth } from '../../context/AuthContext';

jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: { accessCatalog: jest.fn(), accessUsers: jest.fn(), userAccess: jest.fn(), saveUserAccess: jest.fn() }, simpleBalanceError: () => 'تعذر تحميل الصلاحيات' }));
jest.mock('../../context/AuthContext', () => ({ useOptionalAuth: jest.fn() }));
jest.mock('react-router-dom', () => ({ useLocation: () => ({ search: globalThis.location.search }), Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
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
const allKindsCatalog = {
  operations: [...supplierCatalog.operations, { key: 'courier.pay', label: 'دفع للشحن والتوصيل' }],
  resources: [
    ...supplierCatalog.resources,
    { kind: 'employee', id: 'shared', name: 'الموظف الأول' },
    { kind: 'employee', id: 'employee-b', name: 'الموظف الثاني' },
    { kind: 'employee_custody', id: 'shared', name: 'عهدة الموظف الأول' },
    { kind: 'store_driver', id: 'shared', name: 'الموصل الأول' },
    { kind: 'store_driver', id: 'driver-b', name: 'الموصل الثاني' },
    { kind: 'courier', id: 'shared', name: 'شركة الشحن' },
    { kind: 'cash', id: 'shared', name: 'صندوق المؤسسة' },
    { kind: 'provider', id: 'tamara', name: 'تمارا' },
    { kind: 'ad_account', id: 'ads', name: 'حساب الإعلانات' },
    { kind: 'pos_receivable', id: 'pos', name: 'حساب الشبكة' },
    { kind: 'operating_expense', id: 'rent', name: 'الإيجار' },
    { kind: 'owner_withdrawal', id: 'owner', name: 'سحوبات المالك' },
  ],
  resource_kinds: [
    ...supplierCatalog.resource_kinds,
    { key: 'employee', label: 'الموظفون' },
    { key: 'employee_custody', label: 'عهد الموظفين' },
    { key: 'store_driver', label: 'الموصلون' },
    { key: 'courier', label: 'شركات الشحن' },
    { key: 'cash', label: 'الصناديق' },
    { key: 'provider', label: 'مزودو الدفع' },
    { key: 'ad_account', label: 'الحسابات الإعلانية' },
    { key: 'pos_receivable', label: 'حسابات الشبكة' },
    { key: 'operating_expense', label: 'تصنيفات المصروفات' },
    { key: 'owner_withdrawal', label: 'مستفيدو سحوبات المالك' },
  ],
};
const bulkNames = ['استخدام الحسابات والأطراف', 'عرض الحركات', 'عرض الأرصدة'];
const applySuppliers = () => buttonNamed('تطبيق على جميع الموردين (3)');
const applyAll = () => buttonNamed('تطبيق على جميع الحسابات والأطراف (17)');
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

test('legacy grants keep report and print denied and changing a report grant never enables money use or balances', async () => {
  api.userAccess.mockResolvedValue({ user, access: { version: 7, enabled: true, operations: [], resources: [
    { kind: 'bank', id: 'bank', use: true, movements: 'own', balance: false },
  ] } });
  await render(<AccessEditor user={user} catalog={catalog} identity="owner" />);
  expect(labeledControl('كشف فترة بنك المؤسسة').checked).toBe(false);
  expect(labeledControl('طباعة بنك المؤسسة').checked).toBe(false);
  await click(labeledControl('كشف فترة شركة آفاق'));
  expect(labeledControl('طباعة شركة آفاق').checked).toBe(false);
  expect(labeledControl('استخدام شركة آفاق').checked).toBe(false);
  expect(labeledControl('رصيد شركة آفاق').checked).toBe(false);
  expect(labeledControl('حركات شركة آفاق').value).toBe('none');
  await submit();
  expect(api.saveUserAccess).toHaveBeenCalledWith('staff', {
    expected_version: 7, enabled: true, operations: [], resources: [
      { kind: 'bank', id: 'bank', use: true, movements: 'own', balance: false, report: false, print: false },
      { kind: 'external_person', id: 'external', use: false, movements: 'none', balance: false, report: true, print: false },
    ],
  });
});

test('bulk report and print grants preserve individual exceptions and do not cross the selected kind', async () => {
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'supplier');
  await changeValue(labeledControl('تجهيز كشوف الفترة'), 'allow');
  await changeValue(labeledControl('طباعة وحفظ الكشوف'), 'allow');
  await click(applySuppliers());
  await click(labeledControl('طباعة المورد الثاني'));
  expect(api.saveUserAccess).not.toHaveBeenCalled();
  await submit();
  const saved = api.saveUserAccess.mock.calls[0][1];
  expect(saved.resources).toHaveLength(3);
  expect(saved).toEqual({
    expected_version: 3, enabled: true, operations: [], resources: expect.arrayContaining([
      { kind: 'supplier', id: 'shared', use: false, movements: 'none', balance: false, report: true, print: true },
      { kind: 'supplier', id: 'supplier-b', use: false, movements: 'none', balance: false, report: true, print: false },
      { kind: 'supplier', id: 'supplier-c', use: false, movements: 'none', balance: false, report: true, print: true },
    ]),
  });
});

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
      { kind: 'bank', id: 'bank', use: true, movements: 'none', balance: false, report: false, print: false },
      { kind: 'external_person', id: 'external', use: false, movements: 'own', balance: false, report: false, print: false },
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
      .toEqual(['استخدام الحساب', 'الحركات المعروضة', 'عرض الرصيد', 'تجهيز كشف الفترة', 'طباعة وحفظ الكشف']);
    expect(row.querySelectorAll('input,select')).toHaveLength(5);
    expect([...row.querySelectorAll('label')].every(label => label.querySelectorAll('input,select').length === 1)).toBe(true);
  }
  const bankBalance = rows[0].querySelector('[aria-label="رصيد بنك المؤسسة"]');
  await click(bankBalance.closest('label'));
  expect(bankBalance.checked).toBe(true);
  expect(rows[0].querySelector('[aria-label="استخدام بنك المؤسسة"]').checked).toBe(false);
  expect(rows[0].querySelector('select').value).toBe('none');
  await submit();
  expect(api.saveUserAccess.mock.calls[0][1].resources).toEqual([
    { kind: 'bank', id: 'bank', use: false, movements: 'none', balance: true, report: false, print: false },
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
    { kind: 'supplier', id: 'shared', use: false, movements: 'own', balance: true, report: false, print: false },
    { kind: 'supplier', id: 'supplier-b', use: true, movements: 'all', balance: false, report: false, print: false },
    { kind: 'bank', id: 'shared', use: true, movements: 'none', balance: false, report: false, print: false },
    { kind: 'external_person', id: 'external', use: false, movements: 'all', balance: true, report: false, print: false },
  ];
  api.userAccess.mockResolvedValue({ user, access: { version: 11, enabled: true, operations: ['external.pay'], resources: existing } });
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'supplier');
  expect([...host.querySelectorAll('legend')].map(node => node.textContent)).toContain('صلاحيات جميع الموردين');
  expect(bulkNames.map(name => labeledControl(name).value)).toEqual(['', '', '']);
  expect(applySuppliers().disabled).toBe(true);
  await changeValue(host.querySelector('[placeholder="اسم البنك أو الموظف أو الجهة"]'), 'المورد الأول');
  expect(host.querySelectorAll('.sb-access-table tbody tr')).toHaveLength(1);
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'allow');
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
    { kind: 'supplier', id: 'shared', use: true, movements: 'own', balance: true, report: false, print: false },
    { kind: 'supplier', id: 'supplier-b', use: true, movements: 'all', balance: false, report: false, print: false },
    { kind: 'supplier', id: 'supplier-c', use: true, movements: 'none', balance: false, report: false, print: false },
    { kind: 'bank', id: 'shared', use: true, movements: 'none', balance: false, report: false, print: false },
    { kind: 'external_person', id: 'external', use: false, movements: 'all', balance: true, report: false, print: false },
  ]));
});

test('bulk movement visibility permits a per-supplier exception without granting use, balances, or operations', async () => {
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'supplier');
  await changeValue(labeledControl('عرض الحركات'), 'all');
  await click(applySuppliers());
  await changeValue(labeledControl('حركات المورد الثاني'), 'own');
  expect(api.saveUserAccess).not.toHaveBeenCalled();
  await submit();
  const payload = api.saveUserAccess.mock.calls[0][1];
  expect(payload.operations).toEqual([]);
  expect(payload.resources).toHaveLength(3);
  expect(payload.resources).toEqual(expect.arrayContaining([
    { kind: 'supplier', id: 'shared', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'supplier', id: 'supplier-b', use: false, movements: 'own', balance: false, report: false, print: false },
    { kind: 'supplier', id: 'supplier-c', use: false, movements: 'all', balance: false, report: false, print: false },
  ]));
});

test('bulk deny applies false permissions and save omits fully denied supplier grants without revoking other accounts', async () => {
  const bank = { kind: 'bank', id: 'shared', use: true, movements: 'all', balance: true, report: false, print: false };
  api.userAccess.mockResolvedValue({ user, access: {
    version: 4, enabled: true, operations: ['supplier.payment'], resources: [
      { kind: 'supplier', id: 'shared', use: true, movements: 'all', balance: true, report: false, print: false },
      { kind: 'supplier', id: 'supplier-b', use: true, movements: 'own', balance: true, report: false, print: false },
      { kind: 'supplier', id: 'supplier-c', use: false, movements: 'none', balance: true, report: false, print: false }, bank,
    ],
  } });
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'supplier');
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'deny');
  await changeValue(labeledControl('عرض الحركات'), 'none');
  await changeValue(labeledControl('عرض الأرصدة'), 'deny');
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

test('bulk controls cannot apply to an empty selected kind or save an untouched draft', async () => {
  const emptySupplierCatalog = { ...catalog, resource_kinds: supplierCatalog.resource_kinds };
  await render(<AccessEditor user={user} catalog={emptySupplierCatalog} identity="owner" />);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'supplier');
  const apply = buttonNamed('تطبيق على جميع الموردين (0)');
  expect(apply.disabled).toBe(true);
  expect(bulkNames.map(name => labeledControl(name).value)).toEqual(['', '', '']);
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'allow');
  expect(apply.disabled).toBe(true);
  await click(apply);
  expect(buttonNamed('حفظ صلاحيات الموظف').disabled).toBe(true);
  expect(api.saveUserAccess).not.toHaveBeenCalled();
});

test('applying an already matching bulk setting leaves the saved draft clean', async () => {
  api.userAccess.mockResolvedValue({ user, access: {
    version: 3, enabled: true, operations: [], resources: [
      { kind: 'supplier', id: 'shared', use: true, movements: 'own', balance: true, report: false, print: false },
      { kind: 'supplier', id: 'supplier-b', use: true, movements: 'none', balance: false, report: false, print: false },
      { kind: 'supplier', id: 'supplier-c', use: true, movements: 'all', balance: false, report: false, print: false },
    ],
  } });
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'supplier');
  expect(applySuppliers().disabled).toBe(true);
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'allow');
  await click(applySuppliers());
  expect(buttonNamed('حفظ صلاحيات الموظف').disabled).toBe(true);
  expect(host.textContent).not.toContain('تغييرات لم تُحفظ');
  expect(host.querySelector('[role="status"]')?.textContent).toBe('الحسابات والأطراف المحددة لديها هذه الصلاحيات بالفعل.');
  expect(api.saveUserAccess).not.toHaveBeenCalled();
});

test('bulk selections reset on reload and cannot change the draft during a pending save', async () => {
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'supplier');
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'allow');
  await click(buttonNamed('إعادة قراءة النسخة المحفوظة'));
  expect(api.userAccess).toHaveBeenCalledTimes(2);
  expect(bulkNames.map(name => labeledControl(name).value)).toEqual(['', '', '']);
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'allow');
  await click(applySuppliers());
  let finishSave;
  api.saveUserAccess.mockImplementation(() => new Promise(resolve => { finishSave = resolve; }));
  await submit();
  for (const name of bulkNames) expect(labeledControl(name).matches(':disabled')).toBe(true);
  expect(applySuppliers().matches(':disabled')).toBe(true);
  expect(api.saveUserAccess).toHaveBeenCalledTimes(1);
  const payload = api.saveUserAccess.mock.calls[0][1];
  await act(async () => finishSave({ user, access: { ...payload, version: 4 } }));
  expect(labeledControl('استخدام الحسابات والأطراف').matches(':disabled')).toBe(false);
  expect(buttonNamed('حفظ صلاحيات الموظف').disabled).toBe(true);
});

test('all balances apply across current resource kinds with individual exceptions and no other permission changes', async () => {
  api.userAccess.mockResolvedValue({ user, access: {
    version: 12, enabled: false, operations: ['external.pay', 'supplier.payment'], resources: [
      { kind: 'supplier', id: 'shared', use: true, movements: 'own', balance: false, report: false, print: false },
      { kind: 'bank', id: 'shared', use: true, movements: 'all', balance: false, report: false, print: false },
      { kind: 'employee', id: 'shared', use: false, movements: 'all', balance: true, report: false, print: false },
      { kind: 'employee_custody', id: 'shared', use: true, movements: 'none', balance: false, report: false, print: false },
    ],
  } });
  await render(<AccessEditor user={user} catalog={allKindsCatalog} identity="owner" />);
  expect(host.querySelector('.sb-access-bulk legend').textContent).toBe('صلاحيات جميع الحسابات والأطراف');
  expect(bulkNames.map(name => labeledControl(name).value)).toEqual(['', '', '']);
  expect(applyAll().disabled).toBe(true);
  await changeValue(labeledControl('بحث في الحسابات'), 'المورد الأول');
  expect(host.querySelectorAll('.sb-access-table tbody tr')).toHaveLength(1);
  await changeValue(labeledControl('عرض الأرصدة'), 'allow');
  await click(applyAll());
  expect(labeledControl('نوع الحساب أو الجهة').value).toBe('');
  expect(labeledControl('بحث في الحسابات').value).toBe('');
  expect(host.querySelectorAll('.sb-access-table tbody tr')).toHaveLength(17);
  expect(bulkNames.map(name => labeledControl(name).value)).toEqual(['', '', '']);
  for (const resource of allKindsCatalog.resources) expect(labeledControl(`رصيد ${resource.name}`).checked).toBe(true);
  await click(labeledControl('رصيد المورد الأول'));
  await click(labeledControl('رصيد الموظف الأول'));
  await click(labeledControl('رصيد المورد الثالث'));
  expect(labeledControl('السماح بدخول مساحة الأرصدة').checked).toBe(false);
  expect(api.saveUserAccess).not.toHaveBeenCalled();
  await submit();
  const payload = api.saveUserAccess.mock.calls[0][1];
  expect(api.saveUserAccess).toHaveBeenCalledTimes(1);
  expect(payload).toEqual({ expected_version: 12, enabled: false, operations: ['external.pay', 'supplier.payment'], resources: expect.any(Array) });
  expect(payload.resources).toHaveLength(16);
  expect(payload.resources).toEqual(expect.arrayContaining([
    { kind: 'supplier', id: 'shared', use: true, movements: 'own', balance: false, report: false, print: false },
    { kind: 'supplier', id: 'supplier-b', use: false, movements: 'none', balance: true, report: false, print: false },
    { kind: 'bank', id: 'shared', use: true, movements: 'all', balance: true, report: false, print: false },
    { kind: 'external_person', id: 'external', use: false, movements: 'none', balance: true, report: false, print: false },
    { kind: 'employee', id: 'shared', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'employee', id: 'employee-b', use: false, movements: 'none', balance: true, report: false, print: false },
    { kind: 'employee_custody', id: 'shared', use: true, movements: 'none', balance: true, report: false, print: false },
    { kind: 'store_driver', id: 'shared', use: false, movements: 'none', balance: true, report: false, print: false },
    { kind: 'store_driver', id: 'driver-b', use: false, movements: 'none', balance: true, report: false, print: false },
    { kind: 'courier', id: 'shared', use: false, movements: 'none', balance: true, report: false, print: false },
    { kind: 'cash', id: 'shared', use: false, movements: 'none', balance: true, report: false, print: false },
    { kind: 'provider', id: 'tamara', use: false, movements: 'none', balance: true, report: false, print: false },
    { kind: 'ad_account', id: 'ads', use: false, movements: 'none', balance: true, report: false, print: false },
    { kind: 'pos_receivable', id: 'pos', use: false, movements: 'none', balance: true, report: false, print: false },
    { kind: 'operating_expense', id: 'rent', use: false, movements: 'none', balance: true, report: false, print: false },
    { kind: 'owner_withdrawal', id: 'owner', use: false, movements: 'none', balance: true, report: false, print: false },
  ]));
});

test('all movements allow own and none exceptions without granting balances or use', async () => {
  await render(<AccessEditor user={user} catalog={allKindsCatalog} identity="owner" />);
  await changeValue(labeledControl('عرض الحركات'), 'all');
  await click(applyAll());
  await changeValue(labeledControl('حركات المورد الأول'), 'own');
  await changeValue(labeledControl('حركات الموظف الأول'), 'none');
  await changeValue(labeledControl('حركات الموصل الأول'), 'none');
  expect(labeledControl('حركات شركة الشحن').value).toBe('all');
  expect(labeledControl('حركات عهدة الموظف الأول').value).toBe('all');
  for (const resource of allKindsCatalog.resources) {
    expect(labeledControl(`رصيد ${resource.name}`).checked).toBe(false);
    expect(labeledControl(`استخدام ${resource.name}`).checked).toBe(false);
  }
  expect(api.saveUserAccess).not.toHaveBeenCalled();
  await submit();
  const payload = api.saveUserAccess.mock.calls[0][1];
  expect(payload).toEqual({ expected_version: 3, enabled: true, operations: [], resources: expect.any(Array) });
  expect(payload.resources).toHaveLength(15);
  expect(payload.resources).toEqual(expect.arrayContaining([
    { kind: 'supplier', id: 'shared', use: false, movements: 'own', balance: false, report: false, print: false },
    { kind: 'supplier', id: 'supplier-b', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'supplier', id: 'supplier-c', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'bank', id: 'shared', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'external_person', id: 'external', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'employee', id: 'employee-b', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'employee_custody', id: 'shared', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'store_driver', id: 'driver-b', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'courier', id: 'shared', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'cash', id: 'shared', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'provider', id: 'tamara', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'ad_account', id: 'ads', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'pos_receivable', id: 'pos', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'operating_expense', id: 'rent', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'owner_withdrawal', id: 'owner', use: false, movements: 'all', balance: false, report: false, print: false },
  ]));
});

test('bulk driver permissions cover hidden drivers and preserve same-ID shipping companies and employees', async () => {
  const otherGrants = [
    { kind: 'courier', id: 'shared', use: false, movements: 'own', balance: true, report: false, print: false },
    { kind: 'employee', id: 'shared', use: true, movements: 'none', balance: false, report: false, print: false },
    { kind: 'employee_custody', id: 'shared', use: false, movements: 'all', balance: false, report: false, print: false },
    { kind: 'bank', id: 'shared', use: true, movements: 'all', balance: true, report: false, print: false },
  ];
  api.userAccess.mockResolvedValue({ user, access: {
    version: 8, enabled: true, operations: ['courier.pay'], resources: [
      ...otherGrants, { kind: 'store_driver', id: 'shared', use: false, movements: 'none', balance: true, report: false, print: false },
    ],
  } });
  await render(<AccessEditor user={user} catalog={allKindsCatalog} identity="owner" />);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'store_driver');
  expect(host.querySelector('.sb-access-bulk')).toBeTruthy();
  expect(host.querySelector('.sb-access-bulk legend').textContent).toBe('صلاحيات جميع الموصلين');
  await changeValue(labeledControl('بحث في الحسابات'), 'الموصل الأول');
  expect(host.querySelectorAll('.sb-access-table tbody tr')).toHaveLength(1);
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'allow');
  await changeValue(labeledControl('عرض الحركات'), 'all');
  await click(buttonNamed('تطبيق على جميع الموصلين (2)'));
  expect(labeledControl('نوع الحساب أو الجهة').value).toBe('store_driver');
  expect(labeledControl('بحث في الحسابات').value).toBe('');
  expect(host.querySelectorAll('.sb-access-table tbody tr')).toHaveLength(2);
  expect(labeledControl('استخدام الموصل الثاني').checked).toBe(true);
  expect(labeledControl('رصيد الموصل الأول').checked).toBe(true);
  expect(labeledControl('رصيد الموصل الثاني').checked).toBe(false);
  expect(api.saveUserAccess).not.toHaveBeenCalled();
  await submit();
  const payload = api.saveUserAccess.mock.calls[0][1];
  expect(payload).toEqual({ expected_version: 8, enabled: true, operations: ['courier.pay'], resources: expect.any(Array) });
  expect(payload.resources).toHaveLength(6);
  expect(payload.resources).toEqual(expect.arrayContaining([
    ...otherGrants,
    { kind: 'store_driver', id: 'shared', use: true, movements: 'all', balance: true, report: false, print: false },
    { kind: 'store_driver', id: 'driver-b', use: true, movements: 'all', balance: false, report: false, print: false },
  ]));
});

test.each([
  { kind: 'employee', label: 'الموظفين', expected: [
    { kind: 'employee', id: 'shared', use: true, movements: 'none', balance: false, report: false, print: false },
    { kind: 'employee', id: 'employee-b', use: true, movements: 'none', balance: false, report: false, print: false },
  ] },
  { kind: 'employee_custody', label: 'عهد الموظفين', expected: [
    { kind: 'employee_custody', id: 'shared', use: true, movements: 'none', balance: false, report: false, print: false },
  ] },
  { kind: 'courier', label: 'شركات الشحن', expected: [
    { kind: 'courier', id: 'shared', use: true, movements: 'none', balance: false, report: false, print: false },
  ] },
])('bulk $kind use never grants other resource kinds with the same ID', async ({ kind, label, expected }) => {
  await render(<AccessEditor user={user} catalog={allKindsCatalog} identity="owner" />);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), kind);
  expect(host.querySelector('.sb-access-bulk')).toBeTruthy();
  expect(host.querySelector('.sb-access-bulk legend').textContent).toBe(`صلاحيات جميع ${label}`);
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'allow');
  await click(buttonNamed(`تطبيق على جميع ${label} (${expected.length})`));
  expect(labeledControl('نوع الحساب أو الجهة').value).toBe(kind);
  expect(api.saveUserAccess).not.toHaveBeenCalled();
  await submit();
  expect(api.saveUserAccess).toHaveBeenCalledWith('staff', {
    expected_version: 3, enabled: true, operations: [], resources: expected,
  });
});

test('changing kind resets staged bulk choices without losing already applied draft grants', async () => {
  await render(<AccessEditor user={user} catalog={allKindsCatalog} identity="owner" />);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'supplier');
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'allow');
  await click(applySuppliers());
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'deny');
  await changeValue(labeledControl('عرض الحركات'), 'all');
  await changeValue(labeledControl('عرض الأرصدة'), 'allow');
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'store_driver');
  expect(bulkNames.map(name => labeledControl(name).value)).toEqual(['', '', '']);
  expect(buttonNamed('تطبيق على جميع الموصلين (2)').disabled).toBe(true);
  expect(labeledControl('استخدام الموصل الأول').checked).toBe(false);
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'allow');
  await changeValue(labeledControl('نوع الحساب أو الجهة'), '');
  expect(bulkNames.map(name => labeledControl(name).value)).toEqual(['', '', '']);
  expect(applyAll().disabled).toBe(true);
  expect(labeledControl('استخدام المورد الأول').checked).toBe(true);
  expect(labeledControl('استخدام المورد الثاني').checked).toBe(true);
  expect(labeledControl('استخدام المورد الثالث').checked).toBe(true);
  expect(labeledControl('استخدام الموصل الأول').checked).toBe(false);
  expect(labeledControl('حركات المورد الأول').value).toBe('none');
  expect(labeledControl('رصيد المورد الأول').checked).toBe(false);
  expect(buttonNamed('حفظ صلاحيات الموظف').disabled).toBe(false);
  expect(api.saveUserAccess).not.toHaveBeenCalled();
  await submit();
  expect(api.saveUserAccess).toHaveBeenCalledWith('staff', {
    expected_version: 3, enabled: true, operations: [], resources: [
      { kind: 'supplier', id: 'shared', use: true, movements: 'none', balance: false, report: false, print: false },
      { kind: 'supplier', id: 'supplier-b', use: true, movements: 'none', balance: false, report: false, print: false },
      { kind: 'supplier', id: 'supplier-c', use: true, movements: 'none', balance: false, report: false, print: false },
    ],
  });
});

test('a resource added after a saved bulk action has no implicit permissions', async () => {
  await render(<AccessEditor user={user} catalog={supplierCatalog} identity="owner" />);
  await changeValue(labeledControl('نوع الحساب أو الجهة'), 'supplier');
  await changeValue(labeledControl('استخدام الحسابات والأطراف'), 'allow');
  await click(applySuppliers());
  await submit();
  const saved = api.saveUserAccess.mock.calls[0][1];
  api.userAccess.mockResolvedValue({ user, access: { ...saved, version: 4 } });
  const expandedCatalog = { ...supplierCatalog, resources: [
    ...supplierCatalog.resources, { kind: 'supplier', id: 'new-supplier', name: 'المورد الجديد' },
  ] };
  await render(<AccessEditor user={user} catalog={expandedCatalog} identity="owner" />);
  await click(buttonNamed('إعادة قراءة النسخة المحفوظة'));
  expect(labeledControl('استخدام المورد الجديد').checked).toBe(false);
  expect(labeledControl('حركات المورد الجديد').value).toBe('none');
  expect(labeledControl('رصيد المورد الجديد').checked).toBe(false);
  expect(labeledControl('استخدام المورد الأول').checked).toBe(true);
  expect(buttonNamed('تطبيق على جميع الموردين (4)').disabled).toBe(true);
  expect(buttonNamed('حفظ صلاحيات الموظف').disabled).toBe(true);
  expect(api.saveUserAccess).toHaveBeenCalledTimes(1);
});

test.each([['staff',true], ['not-in-this-store',false]])('permission deep link validates member %s against the accessible roster', async (member,matched) => {
  global.history.replaceState(null, '', '/operational-balances/access?member='+member);
  try {
    await render(<SimpleBalanceAccess/>);
    const back=host.querySelector('a[href^="/employees-v2?workspace=permissions"]');
    expect(back.getAttribute('href')).toBe('/employees-v2?workspace=permissions'+(matched?'&member=staff':''));
    expect(host.querySelector('.sb-content select').value).toBe(matched?'staff':'');
    if(matched) expect(api.userAccess).toHaveBeenCalledWith('staff', expect.anything());
    else expect(api.userAccess).not.toHaveBeenCalled();
    expect(api.saveUserAccess).not.toHaveBeenCalled();
  } finally {global.history.replaceState(null, '', '/');}
});
