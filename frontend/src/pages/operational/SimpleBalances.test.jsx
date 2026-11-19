import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import SimpleBalances, { EntryForm } from './SimpleBalances';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { useOptionalAuth } from '../../context/AuthContext';

jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: { context: jest.fn(), parties: jest.fn(), ledger: jest.fn(), movements: jest.fn(), history: jest.fn(), statement: jest.fn(), quoteAd: jest.fn(), entry: jest.fn(), registerPending: jest.fn(), pendingCommand: jest.fn(), retirePending: jest.fn() }, simpleBalanceError: error => error?.localMessage || (error?.response?.status === 403 ? 'لا توجد صلاحية لهذا الحساب' : 'لم يتأكد الحفظ') }));
jest.mock('../../context/AuthContext', () => ({ useOptionalAuth: jest.fn() }));
jest.mock('react-router-dom', () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useLocation: () => ({ pathname: globalThis.window.location.pathname, search: globalThis.window.location.search, state: globalThis.window.history.state }),
  useNavigate: () => value => { if (value === -1) globalThis.window.history.back(); },
  useSearchParams: () => {
    const ReactModule = require('react');
    const [params, setParams] = ReactModule.useState(() => new URLSearchParams(globalThis.window.location.search));
    return [params, (values, options = {}) => { const next = new URLSearchParams(values); globalThis.window.history.replaceState(options.state || {}, '', `/?${next}`); setParams(next); }];
  },
}));
global.IS_REACT_ACT_ENVIRONMENT = true;

const operations = ['external.pay', 'external.receive', 'external.salary', 'external.charity_company',
  'external.charity_owner', 'external.household', 'external.personal', 'custody.spend'];
const resource = (kind, id, name, patch = {}) => ({ kind, id, name, currency: 'SAR', access: { use: true, movements: 'all', balance: true },
  balance: { amount: '400.00', side: 'for_party', visible: true }, operations: [...operations], ...patch });
const external = () => resource('external_person', 'external', 'شركة آفاق');
const context = () => ({ actor: { id: 'staff', name: 'محمد', is_owner: false }, session_scope: 'scope', permission_version: 2,
  status: 'active', capabilities: { create_entries: true, manage_access: false }, operations: operations.map(key => ({ key, label: key })),
  resources: [external(), resource('bank', 'bank', 'بنك المؤسسة'), resource('cash', 'cash', 'الصندوق'),
    resource('employee_custody', 'custody', 'عهدة عمر', { balance: { amount: '100.00', side: 'for_us', visible: true } }),
    resource('employee', 'employee', 'أحمد', { balance: { amount: '1000.00', side: 'for_party', visible: true } }),
    resource('owner_withdrawal', 'owner', 'حساب المالك'), resource('operating_expense', 'charity', 'الصدقات')],
});
const page = (number = 1, patch = {}) => ({ page: number, page_size: 5, total: 6, total_pages: 2,
  items: Array.from({ length: number === 1 ? 5 : 1 }, (_, index) => ({ id: `movement-${number}-${index}`, description: `حركة ${index + 1}`,
    business_date: '2026-11-01', recorded_by: index === 0 ? null : { id: 'staff', name: 'محمد' }, amount: '10.00', currency: 'SAR', side: 'for_party', party_name: 'شركة آفاق' })), ...patch });
let host, root, saved, uuid;
const render = async element => act(async () => root.render(element));
const byLabel = text => [...host.querySelectorAll('label')].find(label => label.querySelector('span')?.textContent === text)?.querySelector('input,select,textarea');
const change = async (label, value) => {
  const input = byLabel(label);
  if (!input) throw new Error(`Missing field: ${label}`);
  const prototype = input.tagName === 'SELECT' ? HTMLSelectElement.prototype : input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, value);
    input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
  });
};
const click = async text => act(async () => {
  const button = [...host.querySelectorAll('button')].find(item => item.textContent.includes(text));
  if (!button) throw new Error(`Missing button: ${text}`);
  button.click();
});
const submit = async () => act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
beforeEach(() => {
  jest.clearAllMocks(); localStorage.clear(); window.history.replaceState({}, '', '/');
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  uuid = 0; Object.defineProperty(globalThis, 'TextEncoder', { configurable: true, value: require('util').TextEncoder });
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: {
    randomUUID: () => `00000000-0000-4000-8000-${String(++uuid).padStart(12, '0')}`,
    subtle: { digest: async (algorithm, input) => Uint8Array.from(require('crypto').createHash('sha256').update(input).digest()).buffer },
  } });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host); saved = jest.fn();
  useOptionalAuth.mockReturnValue({ user: { id: 'staff' } });
  api.context.mockResolvedValue(context()); api.movements.mockImplementation(query => Promise.resolve(page(query.page)));
  api.parties.mockResolvedValue({ items: context().resources });
  api.ledger.mockImplementation((kind, id, query) => Promise.resolve(page(query.page, { resource: context().resources.find(item => item.id === id && item.kind === kind) })));
  api.entry.mockResolvedValue({ saved: true, movement_id: 'new-movement', party_type: 'external_person', party_id: 'external' });
  api.registerPending.mockImplementation((id, value) => Promise.resolve({ id, ...value }));
  api.pendingCommand.mockRejectedValue({ response: { status: 404, data: { detail: { code: 'operational_pending_command_not_found' } } } });
  api.retirePending.mockImplementation(id => Promise.resolve({ deleted: true, id }));
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test('the daily list renders five records, recorder names, and real pagination', async () => {
  await render(<SimpleBalances />);
  expect(host.querySelectorAll('[data-testid="simple-movement-row"]')).toHaveLength(5);
  expect(host.textContent).toContain('سجّلها: اسم الموظف غير متوفر');
  expect(host.textContent).toContain('سجّلها: محمد');
  await click('التالي');
  expect(api.movements).toHaveBeenLastCalledWith({ page: 2, page_size: 5 }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(host.querySelectorAll('[data-testid="simple-movement-row"]')).toHaveLength(1);
});

test('a stale daily-page link renders the server-clamped last page without retrying', async () => {
  window.history.replaceState({}, '', '/?tab=movements&page=9');
  api.movements.mockResolvedValue(page(2));
  await render(<SimpleBalances />);
  expect(api.movements).toHaveBeenCalledTimes(1);
  expect(api.movements).toHaveBeenCalledWith({ page: 9, page_size: 5 }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(host.querySelectorAll('[data-testid="simple-movement-row"]')).toHaveLength(1);
  expect(host.querySelector('button[aria-label="الصفحة 2"]').getAttribute('aria-current')).toBe('page');
  expect(host.querySelector('[role="alert"]')).toBeNull();
});

test('an old ledger-page link still opens after permissions shrink from all movements to own', async () => {
  window.history.replaceState({}, '', '/?tab=parties&kind=external_person&id=external&page=3');
  const ctx = context();
  const limited = { ...external(), access: { use: true, movements: 'own', balance: true } };
  ctx.permission_version += 1; ctx.resources[0] = limited;
  api.context.mockResolvedValue(ctx);
  api.ledger.mockResolvedValue(page(1, { resource: limited, total: 1, total_pages: 1,
    items: [{ id: 'own-row', description: 'حركتي المتاحة', amount: '10.00', side: 'for_party', recorded_by: { id: 'staff', name: 'محمد' } }],
  }));
  await render(<SimpleBalances />);
  expect(api.ledger).toHaveBeenCalledTimes(1);
  expect(api.ledger).toHaveBeenCalledWith('external_person', 'external', { page: 3, page_size: 5 }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(host.querySelectorAll('[data-testid="simple-movement-row"]')).toHaveLength(1);
  expect(host.textContent).toContain('حركتي المتاحة');
  expect(host.textContent).toContain('الحركات التي سجّلتها أنت فقط');
  expect(host.querySelector('button[aria-label="الصفحة 1"]').getAttribute('aria-current')).toBe('page');
  expect(host.querySelector('[role="alert"]')).toBeNull();
});

test('changing external direction clears salary fields, keeps amount and note, and submits one custody payment', async () => {
  await render(<EntryForm context={context()} resource={external()} onCancel={() => {}} onSaved={saved} />);
  await act(async () => host.querySelector('[data-side="for_party"][aria-pressed]').click());
  await change('سبب المبلغ', 'salary_settlement'); await change('الموظف', 'employee');
  await change('المبلغ بالريال', '50'); await change('ملاحظة (اختياري)', 'دفعة اليوم');
  await act(async () => host.querySelector('[data-side="for_us"][aria-pressed]').click());
  expect(byLabel('سبب المبلغ')).toBeUndefined(); expect(byLabel('الموظف')).toBeUndefined();
  expect(byLabel('المبلغ بالريال').value).toBe('50'); expect(byLabel('ملاحظة (اختياري)').value).toBe('دفعة اليوم');
  await change('الدفع من', JSON.stringify(['employee_custody', 'custody']));
  await submit();
  expect(api.entry).toHaveBeenCalledTimes(1);
  expect(api.entry.mock.calls[0][0]).toEqual(expect.objectContaining({ side: 'for_us', amount: '50.00', source_type: 'employee_custody', source_id: 'custody', note: 'دفعة اليوم', expected_session_scope: 'scope' }));
  expect(api.entry.mock.calls[0][0].reason).toBeUndefined(); expect(api.entry.mock.calls[0][0].employee_id).toBeUndefined();
  expect(saved).toHaveBeenCalledTimes(1);
});

test('custody sources require the separate custody.spend operation', async () => {
  const ctx = context(); ctx.operations = ctx.operations.filter(item => item.key !== 'custody.spend');
  await render(<EntryForm context={ctx} resource={external()} onCancel={() => {}} onSaved={saved} />);
  await act(async () => host.querySelector('[data-side="for_us"][aria-pressed]').click());
  expect([...byLabel('الدفع من').options].some(option => option.value.includes('custody'))).toBe(false);
});

test('an uncertain save retries the identical command and prevents double submission', async () => {
  let reject;
  api.entry.mockImplementationOnce(() => new Promise((resolve, fail) => { reject = fail; }));
  await render(<EntryForm context={context()} resource={external()} onCancel={() => {}} onSaved={saved} />);
  await act(async () => host.querySelector('[data-side="for_us"][aria-pressed]').click());
  await change('المبلغ بالريال', '50'); await change('الدفع من', JSON.stringify(['bank', 'bank']));
  await act(async () => {
    const form = host.querySelector('form');
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  expect(api.entry).toHaveBeenCalledTimes(1);
  const command = api.entry.mock.calls[0][0];
  await act(async () => reject(new Error('network result unknown')));
  expect(byLabel('المبلغ بالريال').disabled).toBe(true);
  await submit();
  expect(api.entry).toHaveBeenCalledTimes(2); expect(api.entry.mock.calls[1][0]).toEqual(command);
  expect(saved).toHaveBeenCalledTimes(1); expect(localStorage.length).toBe(0);
});

test('reopening an entry restores its server body without an automatic financial retry', async () => {
  api.entry.mockRejectedValueOnce(new Error('financial response unknown'));
  await render(<EntryForm key="before" context={context()} resource={external()} onCancel={() => {}} onSaved={saved} />);
  await act(async () => host.querySelector('[data-side="for_us"][aria-pressed]').click());
  await change('المبلغ بالريال', '50'); await change('الدفع من', JSON.stringify(['bank', 'bank']));
  await change('ملاحظة (اختياري)', 'دفعة توضيحية'); await submit();
  const original = api.entry.mock.calls[0][0];
  const [id, registered] = api.registerPending.mock.calls[0];
  expect(localStorage.getItem(localStorage.key(0))).toBe(id);
  api.pendingCommand.mockResolvedValue({ id, ...registered });
  await render(<EntryForm key="after" context={context()} resource={external()} onCancel={() => {}} onSaved={saved} />);
  expect(byLabel('المبلغ بالريال').value).toBe('50.00'); expect(byLabel('المبلغ بالريال').disabled).toBe(true);
  expect(byLabel('ملاحظة (اختياري)').value).toBe('دفعة توضيحية');
  expect(api.entry).toHaveBeenCalledTimes(1);
  await submit(); expect(api.entry).toHaveBeenCalledTimes(2); expect(api.entry.mock.calls[1][0]).toEqual(original);
  expect(localStorage.length).toBe(0);
});

test('salary settlement cannot exceed the selected employee due and creates no payment-source field', async () => {
  await render(<EntryForm context={context()} resource={external()} onCancel={() => {}} onSaved={saved} />);
  await act(async () => host.querySelector('[data-side="for_party"][aria-pressed]').click());
  await change('سبب المبلغ', 'salary_settlement'); await change('الموظف', 'employee'); await change('المبلغ بالريال', '1001');
  expect(byLabel('الدفع من')).toBeUndefined(); await submit();
  expect(api.entry).not.toHaveBeenCalled(); expect(host.querySelector('[role="alert"]').textContent).toContain('أكبر من مستحق');
  await change('المبلغ بالريال', '200'); await submit();
  expect(api.entry.mock.calls[0][0]).toEqual(expect.objectContaining({ reason: 'salary_settlement', employee_id: 'employee', amount: '200.00' }));
  expect(api.entry.mock.calls[0][0].source_id).toBeUndefined();
});

test('permission denial removes ledger money and hides the stale balance', async () => {
  window.history.replaceState({}, '', '/?tab=parties&kind=external_person&id=external');
  api.ledger.mockRejectedValue({ response: { status: 403 } });
  await render(<SimpleBalances />);
  expect(host.querySelectorAll('[data-testid="simple-movement-row"]')).toHaveLength(0);
  expect(host.querySelector('.sb-balance')).toBeNull();
  expect(host.textContent).toContain('لا توجد صلاحية');
});

test('a response from the previous signed-in actor cannot render in the new session', async () => {
  let finish;
  api.context.mockImplementationOnce(() => new Promise(resolve => { finish = () => resolve(context()); }));
  await render(<SimpleBalances />);
  const originalSignal = api.context.mock.calls[0][0].signal;
  useOptionalAuth.mockReturnValue({ user: { id: 'other' } });
  api.context.mockRejectedValue({ response: { status: 403 } });
  await render(<SimpleBalances />);
  expect(originalSignal.aborted).toBe(true);
  await act(async () => finish());
  expect(host.textContent).not.toContain('شركة آفاق'); expect(api.movements).not.toHaveBeenCalled();
});

test('specialized legacy links remain unavailable to a scoped employee', async () => {
  await render(<SimpleBalances />); await click('الأقسام');
  expect(host.querySelector('a[href="/operational-balances/customer-returns"]')).toBeNull();
  expect(host.querySelector('a[href="/operational-balances/access"]')).toBeNull();
});

test('a saved backdated movement returns to its server-selected ledger page and stays highlighted', async () => {
  window.history.replaceState({}, '', '/?tab=parties&kind=external_person&id=external&action=add');
  api.ledger.mockImplementation((kind, id, query) => Promise.resolve(page(2, {
    resource: external(), items: [{ id: 'new-movement', description: 'دفعة محفوظة', amount: '50.00', side: 'for_us', business_date: '2026-10-01', recorded_by: { id: 'staff', name: 'محمد' } }],
  })));
  await render(<SimpleBalances />);
  await act(async () => host.querySelector('[data-side="for_us"][aria-pressed]').click());
  await change('المبلغ بالريال', '50'); await change('الدفع من', JSON.stringify(['bank', 'bank'])); await submit();
  expect(api.ledger).toHaveBeenLastCalledWith('external_person', 'external', { page: 1, page_size: 5, focus_id: 'new-movement' }, expect.any(Object));
  expect(host.querySelector('.sb-new-row').textContent).toContain('دفعة محفوظة');
  expect(host.querySelector('button[aria-label="الصفحة 2"]').getAttribute('aria-current')).toBe('page');
  expect(host.textContent).toContain('تم حفظ الحركة');
});

test('an invalid page larger than five rows is not presented as a valid ledger', async () => {
  api.movements.mockResolvedValue({ ...page(), items: Array.from({ length: 6 }, (_, index) => ({ id: `invalid-${index}`, amount: '99999.00' })) });
  await render(<SimpleBalances />);
  expect(host.querySelectorAll('[data-testid="simple-movement-row"]')).toHaveLength(0);
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
});


test.each([true, false])('owner or granted employee can fund USD advertising from SAR, owner=%s', async isOwner => {
  const ctx = context();
  ctx.actor.is_owner = isOwner;
  ctx.operations.push({ key: 'ads.fund', label: 'إعلانات' });
  const ad = resource('ad_account', 'snap', 'سناب', { currency: 'USD', operations: ['ads.fund'],
    ad_bank_funding: { mode: 'hybrid', wallet_first: true, terms: {} } });
  ctx.resources.push(ad);
  api.quoteAd.mockResolvedValue({ amount_usd: '100.00', gross_sar: '383.63', fee_sar: '8.63' });
  await render(<EntryForm context={ctx} resource={ad} onCancel={() => {}} onSaved={saved} />);
  await change('إجمالي الخصم بالريال شامل العمولة', '٣٨٣٫٦٣');
  await click('احسب المقابل');
  expect(api.quoteAd).toHaveBeenCalledWith({ party_id: 'snap', currency: 'SAR', amount: '383.63' });
  expect(host.textContent).toContain('100.00 دولار');
  const banks = byLabel('الدفع من');
  expect([...banks.options].map(o => o.textContent)).not.toContain('الصندوق');
  await change('الدفع من', JSON.stringify(['bank', 'bank']));
  await change('مرجع العملية البنكية', 'BANK-TEST');
  await submit();
  expect(api.entry).toHaveBeenCalledWith(expect.objectContaining({ amount: '100.00', reference: 'BANK-TEST', source_id: 'bank',
    ad_bank_funding: { bank_amount: '383.63', bank_fee_amount: '8.63', kind: 'payment' } }));
});
