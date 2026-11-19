import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import SimpleBalanceStatement from './SimpleBalanceStatement';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: { statement: jest.fn() }, simpleBalanceError: error => error?.response?.status === 403 ? 'لا توجد صلاحية للطباعة' : 'تعذر تحميل الكشف' }));
global.IS_REACT_ACT_ENVIRONMENT = true;
const resource = () => ({ kind: 'employee', id: 'employee', name: 'خالد', access: { report: true, print: true, balance: true, movements: 'all' } });
const context = () => ({ actor: { id: 'staff', is_owner: false }, session_scope: 'scope', permission_version: 1, resources: [resource()] });
const filters = { date_from: '2026-11-01', date_to: '2026-11-19' };
const report = patch => ({ resource: resource(), ...filters, scope: 'all', currency: 'SAR', opening_balance: '100.00', closing_balance: '1200.00',
  totals: { for_party: '1100.00', for_us: '0.00' }, can_print: true, prepared_by: { id: 'staff', name: 'محمد' }, generated_at: '2026-11-19T12:00:00Z',
  items: Array.from({ length: 11 }, (_, index) => ({ id: `row-${index}`, description: `استحقاق ${index}`, business_date: `2026-11-${String(index + 1).padStart(2, '0')}`, amount: '100.00', side: 'for_party', balance_after: `${200 + index * 100}.00` })), ...patch });
let host, root;
const render = async props => act(async () => root.render(<SimpleBalanceStatement context={context()} identity="staff" resource={resource()} initialFilters={filters} {...props} />));
const click = async text => act(async () => [...host.querySelectorAll('button')].find(button => button.textContent.includes(text)).click());
beforeEach(() => {
  jest.clearAllMocks();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  window.print = jest.fn(); api.statement.mockResolvedValue(report());
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test('a period is read only on request and includes all chronological rows rather than five display rows', async () => {
  await render(); expect(api.statement).not.toHaveBeenCalled();
  await click('عرض الكشف');
  expect(api.statement).toHaveBeenCalledWith('employee', 'employee', { ...filters, purpose: 'view' }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(host.querySelectorAll('.sb-statement-table tbody tr')).toHaveLength(11);
  expect(host.textContent).toContain('الرصيد الافتتاحي'); expect(host.textContent).toContain('الصافي بنهاية الفترة');
  expect(host.querySelector('.sb-statement-document h2').textContent).toBe('كشف حساب الموظف خالد');
  expect([...host.querySelectorAll('.sb-statement-table th')].map(th => th.textContent)).toEqual(['البيان', 'له', 'عليه', 'الرصيد بعد الحركة']);
  expect(host.querySelector('.sb-statement-table tbody').textContent).not.toContain('2026-');
  expect(host.querySelector('.sb-statement-period').textContent).toContain('الفترة من 01/11/2026 إلى 19/11/2026');
  expect(host.querySelector('.sb-statement-table').compareDocumentPosition(host.querySelector('.sb-statement-summary')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(window.print).not.toHaveBeenCalled();
});

test('printing waits for a second successful authorized response and does not reuse the preview', async () => {
  await render(); await click('عرض الكشف');
  let resolve;
  api.statement.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await click('طباعة'); expect(window.print).not.toHaveBeenCalled();
  expect(api.statement).toHaveBeenLastCalledWith('employee', 'employee', { ...filters, purpose: 'print' }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  let printedSnapshot;
  window.print.mockImplementation(() => {
    const printed = document.querySelector('.sb-print-root');
    printedSnapshot = printed?.cloneNode(true);
  });
  await act(async () => resolve(report({ closing_balance: '1250.00', items: [{ ...report().items[0], note: 'ملاحظة أصلية في الاستجابة الجديدة' }] })));
  expect(window.print).toHaveBeenCalledTimes(1);
  expect(printedSnapshot.textContent).toContain('1,250');
  expect(printedSnapshot.textContent).toContain('كشف حساب الموظف خالد');
  expect(printedSnapshot.querySelector('.sb-statement-period').textContent).toContain('الفترة من 01/11/2026 إلى 19/11/2026');
  expect([...printedSnapshot.querySelectorAll('th')].map(th => th.textContent)).toEqual(['البيان', 'له', 'عليه', 'الرصيد بعد الحركة', 'الملاحظة']);
  expect(printedSnapshot.querySelector('tbody .sb-statement-note').textContent).toBe('ملاحظة أصلية في الاستجابة الجديدة');
  expect(printedSnapshot.querySelector('.sb-statement-table').compareDocumentPosition(printedSnapshot.querySelector('.sb-statement-summary')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test('old grants cannot view reports and separate print denial leaves no print control', async () => {
  const ctx = context(); ctx.resources[0].access = { balance: true, movements: 'all' };
  await render({ context: ctx, resource: ctx.resources[0] });
  expect(api.statement).not.toHaveBeenCalled(); expect(host.textContent).toContain('لا توجد حسابات بصلاحية عرض التقارير');
  const viewOnly = context(); viewOnly.resources[0].access.print = false;
  await render({ context: viewOnly, resource: viewOnly.resources[0] }); await click('عرض الكشف');
  expect(host.textContent).not.toContain('طباعة / حفظ PDF');
});

test('a fresh print denial removes stale report data and never opens the print dialog', async () => {
  await render(); await click('عرض الكشف');
  api.statement.mockRejectedValueOnce({ response: { status: 403 } });
  await click('طباعة');
  expect(window.print).not.toHaveBeenCalled(); expect(host.querySelector('.sb-statement-document')).toBeNull();
  expect(host.textContent).toContain('لا توجد صلاحية للطباعة');
});

test('closing the panel cancels pending printing and ignores its late response', async () => {
  await render(); await click('عرض الكشف');
  let resolve;
  api.statement.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await click('طباعة'); const signal = api.statement.mock.calls.at(-1)[3].signal;
  await act(async () => root.render(null)); expect(signal.aborted).toBe(true);
  await act(async () => resolve(report())); expect(window.print).not.toHaveBeenCalled();
});

test('own activity never reveals opening, closing, or per-row balances even if stray values arrive', async () => {
  api.statement.mockResolvedValue(report({ scope: 'own', opening_balance: '99999.00', closing_balance: '99999.00',
    items: [{ id: 'mine', amount: '10.00', side: 'for_party', balance_after: '99999.00' }] }));
  await render(); await click('عرض الكشف');
  expect(host.textContent).toContain('كشف نشاط محدود'); expect(host.textContent).not.toContain('99,999');
  expect(host.querySelectorAll('.sb-statement-table th')).toHaveLength(3);
});

test('a permission change cancels a requested report and rejects the previous scope result', async () => {
  let resolve; api.statement.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await render(); await click('عرض الكشف');
  const signal = api.statement.mock.calls[0][3].signal;
  const changed = context(); changed.permission_version = 2; changed.resources[0].access.report = false;
  await render({ context: changed, resource: changed.resources[0] });
  await act(async () => resolve(report()));
  expect(signal.aborted).toBe(true); expect(host.querySelector('.sb-statement-document')).toBeNull();
});

test('unreconciled balances show the server explanation without presenting an empty closing balance as verified', async () => {
  api.statement.mockResolvedValue(report({ opening_balance: null, closing_balance: null,
    balance_note: 'لا تتوفر أرصدة متطابقة لهذا السجل.', items: [{ id: 'one', amount: '10.00', side: 'for_party', balance_after: null }] }));
  await render(); await click('عرض الكشف');
  expect(host.textContent).toContain('لا تتوفر أرصدة متطابقة لهذا السجل.');
  expect(host.textContent).not.toContain('الصافي بنهاية الفترة');
  expect(host.querySelectorAll('.sb-statement-table th')).toHaveLength(3);
});

test('only a saved original note adds the optional column and other descriptions remain outside it', async () => {
  api.statement.mockResolvedValue(report({ items: [
    { ...report().items[0], customer_name: 'عميل موثق', description: 'تحصيل الطلب', source_label: 'بنك الإنماء', note: 'اتفاق السداد\nحسب الفاتورة' },
    { ...report().items[1], description: 'وصف المصدر فقط', note: '  \n  ' },
    { ...report().items[2], description: 'وصف آخر بلا ملاحظة' },
  ] }));
  await render(); await click('عرض الكشف');
  expect([...host.querySelectorAll('.sb-statement-table th')].map(th => th.textContent)).toEqual(['البيان', 'له', 'عليه', 'الرصيد بعد الحركة', 'الملاحظة']);
  const rows = [...host.querySelectorAll('.sb-statement-table tbody tr')];
  expect(rows[0].querySelector('.sb-statement-note').textContent).toBe('اتفاق السداد\nحسب الفاتورة');
  expect(rows[0].querySelector('td').textContent).toContain('عميل موثق');
  expect(rows[0].querySelector('.sb-statement-note').textContent).not.toContain('عميل موثق');
  expect(rows.slice(1).map(row => row.querySelector('.sb-statement-note').textContent)).toEqual(['—', '—']);

  api.statement.mockResolvedValue(report({ items: report().items.map(row => ({ ...row, note: '  \n  ', source_label: 'مصدر ذو وصف' })) }));
  await click('عرض الكشف');
  expect(host.querySelectorAll('.sb-statement-table th')).toHaveLength(4);
  expect(host.querySelector('.sb-statement-note')).toBeNull();
});

test('notes do not restore a balance column when that permission is absent', async () => {
  const restricted = resource(); restricted.access.balance = false;
  const ctx = context(); ctx.resources = [restricted];
  api.statement.mockResolvedValue(report({ resource: restricted, opening_balance: '99999.00', closing_balance: '99999.00',
    items: [{ ...report().items[0], note: 'ملاحظة أصلية', balance_after: '99999.00' }] }));
  await render({ context: ctx, resource: restricted }); await click('عرض الكشف');
  expect([...host.querySelectorAll('.sb-statement-table th')].map(th => th.textContent)).toEqual(['البيان', 'له', 'عليه', 'الملاحظة']);
  expect(host.textContent).not.toContain('99,999');
  expect(host.textContent).not.toContain('الرصيد الافتتاحي');
  expect(host.textContent).not.toContain('الصافي بنهاية الفترة');
  expect(host.textContent).toContain('الأرصدة غير متاحة');
});

test.each([
  ['employee', 'خالد', '120.00', 'له', 'positive'],
  ['employee', 'خالد', '-120.00', 'عليه', 'negative'],
  ['bank', 'بنك الإنماء', '120.00', 'له', 'positive'],
  ['bank', 'بنك الإنماء', '-120.00', 'عليه', 'negative'],
])('closing %s balance %s uses the source sign and its correct side', async (kind, name, closing, side, sign) => {
  const account = { ...resource(), kind, name };
  const ctx = context(); ctx.resources = [account];
  api.statement.mockResolvedValue(report({ resource: account, closing_balance: closing }));
  await render({ context: ctx, resource: account }); await click('عرض الكشف');
  const closingRow = host.querySelector('[data-statement-total="closing"]');
  expect(closingRow.textContent).toContain('الصافي بنهاية الفترة');
  expect(closingRow.querySelector('.sb-balance-side').textContent).toBe(side);
  expect(closingRow.querySelector('.sb-running').dataset.sign).toBe(sign);
  expect(closingRow.textContent).toContain(closing.startsWith('-') ? '−120' : '120');
  expect(host.querySelector('.sb-statement-document h2').textContent).toBe(kind === 'bank' ? 'كشف حساب بنك الإنماء' : 'كشف حساب الموظف خالد');
  expect(host.querySelector('.sb-statement-summary').textContent).toContain('يشمل الرصيد الافتتاحي');
});

test('a successful print response with can_print false still cannot open printing', async () => {
  await render(); await click('عرض الكشف');
  api.statement.mockResolvedValueOnce(report({ can_print: false }));
  await click('طباعة');
  expect(window.print).not.toHaveBeenCalled(); expect(host.textContent).toContain('لا توجد صلاحية للطباعة');
});

test.each([
  ['bank', 'for_party', '0.00', '120.00', ['—', '120']],
  ['cash', 'for_us', '120.00', '0.00', ['120', '—']],
])('a bank top-up prints its signed %s transfer leg in the same column as the on-screen statement', async (kind, side, credit, debit, cells) => {
  const account = { ...resource(), kind, name: 'حساب تجريبي' };
  const ctx = context(); ctx.resources = [account];
  const totals = { for_party: side === 'for_party' ? debit : '0.00', for_us: side === 'for_us' ? credit : '0.00' };
  const data = report({ resource: account, totals, items: [
    { id: 'test-transfer', side, amount: '120.00', description: 'تعبئة المحفظة', balance_after: '200.00' },
  ] });
  api.statement.mockResolvedValue(data);
  await render({ context: ctx, resource: account }); await click('عرض الكشف');
  const renderedStatement = host.querySelector('.sb-statement-document');
  expect([...renderedStatement.querySelectorAll('tbody td')].slice(1, 3).map(td => td.textContent)).toEqual(cells);
  expect(renderedStatement.querySelector('.sb-statement-summary').textContent).toContain('إجمالي له' + Number(credit));
  expect(renderedStatement.querySelector('.sb-statement-summary').textContent).toContain('إجمالي عليه' + Number(debit));
  let printedSnapshot;
  window.print.mockImplementation(() => { printedSnapshot = document.querySelector('.sb-print-root').cloneNode(true); });
  await click('طباعة');
  expect([...printedSnapshot.querySelectorAll('tbody td')].slice(1, 3).map(td => td.textContent)).toEqual(cells);
});
