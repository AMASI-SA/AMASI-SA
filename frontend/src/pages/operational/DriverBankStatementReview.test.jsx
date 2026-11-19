import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import Review from '../StoreDeliveryPaymentReview';
import api from '../../lib/api';

jest.mock('../../lib/api', () => ({__esModule: true, default: {get: jest.fn(), post: jest.fn()}}));
jest.mock('sonner', () => ({toast: {success: jest.fn(), error: jest.fn()}}));

const base = '/store-delivery/payment-review';
const scope = 'a'.repeat(64);
const started = '2026-11-01T12:00:00Z';
const bankId = 'operational-bank:bank-42';
const movementId = 'operational-bank-movement:row-7';
const bank = {id: bankId, name: 'بنك المتجر', account_type: 'bank', currency: 'SAR', status: 'active'};
const review = {assignment_id: 'assignment', order_number: '123', payment_method: 'bank_transfer', amount: '500.00',
  receipt_reference: 'receipt', receipt_url: '/synthetic-receipt'};
const movement = {id: movementId, file_id: 'file-1', bank_account_id: bankId, movement_date: '2026-11-12',
  amount: '500.00', currency: 'SAR', direction: 'in', reference: 'BANK-REFERENCE', description: 'حوالة واردة',
  row_no: 7, status: 'unclassified', source_namespace: 'operational_driver_bank_movements_v1'};
let host, root, context, options, result;
const button = label => [...host.querySelectorAll('button')].find(b => b.textContent === label);
const field = label => [...host.querySelectorAll('label')].find(l => l.textContent.startsWith(label))?.querySelector('input,select');
const click = async label => {await act(async () => button(label).click());};
const change = async (element, value) => {await act(async () => {
  Object.getOwnPropertyDescriptor(element.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value').set.call(element, value);
  element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true}));
});};
const chooseFile = async file => {await act(async () => {
  const input = field('كشف البنك الأصلي');
  Object.defineProperty(input, 'files', {configurable: true, value: file ? [file] : []});
  input.dispatchEvent(new Event('change', {bubbles: true}));
});};
const file = () => new File(['synthetic workbook bytes'], 'bank.xlsx', {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
const render = async () => {await act(async () => root.render(<Review/>));};
const load = async () => {await click('تحميل خيارات الاعتماد');};
const prepare = async () => {await render(); await load(); await change(field('البنك المستلم المؤكد'), bankId);};
const deferred = () => {let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject};};
beforeEach(() => {
  jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
  context = {source: 'operational_driver_bank_statement', bank_source: 'operational_bank_catalog',
    financial_source: 'operational', session_scope: scope, started_at: started, can_upload: true,
    max_file_bytes: 4096, accepted_file_type: '.xlsx', items: [bank, {...bank, id: 'other-bank', name: 'بنك آخر'}]};
  options = {...review, financial_source: 'operational', expected_review_revision: 2, session_scope: scope,
    started_at: started, bank_account_id: bankId, bank_movements: [movement], can_approve: true, can_reject: true};
  result = {status: 'imported', financial_source: 'operational', source: 'operational_driver_bank_statement',
    file: {id: 'file-1', filename: 'bank.xlsx', bank_account_id: bankId, started_at: started, row_count: 2},
    items: [movement]};
  api.get.mockImplementation(url => {
    if (url === `${base}/pending`) return Promise.resolve({data: {items: [review]}});
    if (url === `${base}/bank-accounts`) return Promise.resolve({data: {...context, source: 'operational_bank_catalog'}});
    if (url === `${base}/bank-statements/context`) return Promise.resolve({data: context});
    if (url.endsWith('/approval-options')) return Promise.resolve({data: options});
    return Promise.resolve({data: {items: []}});
  });
  api.post.mockImplementation(url => Promise.resolve({data: url.endsWith('/upload') ? result : {state: 'operational_recorded'}}));
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => {act(() => root.unmount()); host.remove(); jest.restoreAllMocks();});

test('operational catalog and eligible movements are read-only until explicit selection and approval', async () => {
  await prepare();
  expect(api.get).toHaveBeenCalledWith(`${base}/bank-statements/context`);
  expect(field('معرف حركة البنك')).toBeUndefined();
  expect([...field('حركة البنك الواردة المطابقة').options].map(o => o.value)).toEqual(['', movementId]);
  expect(host.textContent).toContain('BANK-REFERENCE');
  expect(button('اعتماد مدفوع').disabled).toBe(true);
  expect(api.post).not.toHaveBeenCalled();
  await change(field('حركة البنك الواردة المطابقة'), movementId);
  await click('اعتماد مدفوع');
  expect(api.post).toHaveBeenCalledWith(`${base}/assignment`, {
    decision: 'approved', note: 'تمت المطابقة', expected_financial_source: 'operational', expected_review_revision: 2,
    expected_session_scope: scope, destination_financial_id: bankId, settlement_reference: movementId});
});

test.each(['imported', 'duplicate'])('upload %s preserves bank identity and only refreshes candidates, without approving', async status => {
  result.status = status;
  result.items = [{...movement, id: 'upload-result-must-not-authorize', amount: '999.00'}];
  await prepare(); const selected = file(); await chooseFile(selected);
  await click('رفع كشف البنك');
  const [url, body] = api.post.mock.calls[0];
  expect(url).toBe(`${base}/bank-statements/upload`); expect(body).toBeInstanceOf(FormData);
  expect(Object.fromEntries([...body.entries()].filter(([key]) => key !== 'file'))).toEqual({
    assignment_id: 'assignment', bank_account_id: bankId, request_id: expect.any(String),
    expected_started_at: started, expected_session_scope: scope});
  expect(body.get('request_id').length).toBeGreaterThanOrEqual(8);
  expect(body.get('file')).toBe(selected);
  expect(api.get.mock.calls.filter(([url]) => url.endsWith('/approval-options'))).toHaveLength(2);
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain(status === 'duplicate' ? 'الكشف محفوظ مسبقًا' : 'تم حفظ كشف البنك');
  expect([...field('حركة البنك الواردة المطابقة').options].map(o => o.value)).toEqual(['', movementId]);
  expect(button('اعتماد مدفوع').disabled).toBe(true);
});

test.each([undefined, 503, 408])('ambiguous upload status %s retries the same file and fields once', async status => {
  api.post.mockRejectedValueOnce(status ? {response: {status}} : new Error('connection lost'));
  await prepare(); const selected = file(); await chooseFile(selected); await click('رفع كشف البنك');
  expect(field('كشف البنك الأصلي').disabled).toBe(true);
  expect(field('البنك المستلم المؤكد').disabled).toBe(true);
  expect(button('اعتماد مدفوع').disabled).toBe(true);
  const first = api.post.mock.calls[0][1];
  await click('إعادة رفع نفس الملف');
  expect([...api.post.mock.calls[1][1].entries()]).toEqual([...first.entries()]);
  expect(api.post).toHaveBeenCalledTimes(2);
  expect(button('إعادة رفع نفس الملف')).toBeUndefined();
});

test('a second bank cannot replace the bank submitted by the courier', async () => {
  await prepare(); await chooseFile(file()); await change(field('البنك المستلم المؤكد'), 'other-bank');
  expect(button('رفع كشف البنك').disabled).toBe(true);
  expect(button('اعتماد مدفوع').disabled).toBe(true);
  await click('رفع كشف البنك'); expect(api.post).not.toHaveBeenCalled();
});

test.each([
  {financial_source: 'native'}, {session_scope: 'b'.repeat(64)}, {started_at: '2026-10-01T12:00:00Z'},
  {bank_source: 'accounts'}, {source: 'unrelated-source'},
])('mismatched statement context %j cannot authorize uploads or review', async patch => {
  context = {...context, ...patch}; await render(); await load();
  expect(button('رفع كشف البنك')?.disabled ?? true).toBe(true);
  expect(button('اعتماد مدفوع').disabled).toBe(true);
  expect(host.textContent).toContain('أعد تحميل خيارات الاعتماد');
  expect(api.post).not.toHaveBeenCalled();
});

test('review permission without import permission still allows an existing eligible movement', async () => {
  context.can_upload = false; await prepare();
  expect(field('كشف البنك الأصلي').disabled).toBe(true);
  expect(button('رفع كشف البنك').disabled).toBe(true);
  expect(host.textContent).toContain('صلاحية رفع كشوف البنك');
  await change(field('حركة البنك الواردة المطابقة'), movementId);
  expect(button('اعتماد مدفوع').disabled).toBe(false);
});

test.each([
  ['not-excel.csv', 'row'], ['empty.xlsx', ''], ['large.xlsx', 'x'.repeat(4097)],
])('invalid file %s is rejected before upload', async (name, content) => {
  await prepare(); await chooseFile(new File([content], name));
  expect(button('رفع كشف البنك').disabled).toBe(true);
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  await click('رفع كشف البنك'); expect(api.post).not.toHaveBeenCalled();
});

test('a changed server context clears pending upload, selection and authorization', async () => {
  api.post.mockRejectedValueOnce({response: {status: 409, data: {detail: {code: 'operational_driver_bank_context_changed'}}}});
  await prepare(); await change(field('حركة البنك الواردة المطابقة'), movementId); await chooseFile(file());
  await click('رفع كشف البنك');
  expect(button('اعتماد مدفوع').disabled).toBe(true);
  expect(button('إعادة رفع نفس الملف')).toBeUndefined();
  expect(host.textContent).toContain('أعد تحميل خيارات الاعتماد');
  await load(); expect(field('البنك المستلم المؤكد').value).toBe('');
  expect(field('حركة البنك الواردة المطابقة').value).toBe('');
  expect(button('رفع كشف البنك').disabled).toBe(true);
});

test('eligible movement picker excludes foreign, consumed, outgoing and unequal rows', async () => {
  options.bank_movements = [movement, {...movement, id: 'native-existing', source_namespace: 'mz2_daily_movements'},
    ...[{bank_account_id: 'other-bank'}, {amount: '500.01'}, {currency: 'USD'}, {direction: 'out'},
      {status: 'matched'}, {source_namespace: 'unverified'}].map((patch, index) => ({...movement, id: `invalid-${index}`, ...patch}))];
  await prepare();
  expect([...field('حركة البنك الواردة المطابقة').options].map(o => o.value)).toEqual(['', movementId, 'native-existing']);
  expect(button('اعتماد مدفوع').disabled).toBe(true);
});

test('changed phase on reread discards an uncertain attempt instead of repinning its file', async () => {
  api.post.mockRejectedValueOnce(new Error('lost response'));
  await prepare(); await chooseFile(file()); await click('رفع كشف البنك');
  options = {...options, started_at: '2026-11-02T12:00:00Z'}; context = {...context, started_at: options.started_at};
  await load();
  expect(button('إعادة رفع نفس الملف')).toBeUndefined();
  expect(field('البنك المستلم المؤكد').value).toBe('');
  expect(button('رفع كشف البنك').disabled).toBe(true);
  expect(api.post).toHaveBeenCalledTimes(1);
});

test('list refresh preserves the uncertain upload until matching context allows the exact retry', async () => {
  api.post.mockRejectedValueOnce(new Error('lost response'));
  await prepare(); await chooseFile(file()); await click('رفع كشف البنك');
  const original = [...api.post.mock.calls[0][1].entries()];
  await click('تحديث القائمة للتحقق'); await load();
  expect(field('البنك المستلم المؤكد').value).toBe(bankId);
  expect(button('إعادة رفع نفس الملف').disabled).toBe(false);
  await click('إعادة رفع نفس الملف');
  expect([...api.post.mock.calls[1][1].entries()]).toEqual(original);
});

test('a new review revision discards a file selected for the previous receipt review', async () => {
  await prepare(); await chooseFile(file());
  options = {...options, expected_review_revision: 3};
  await load();
  expect(field('البنك المستلم المؤكد').value).toBe('');
  expect(button('رفع كشف البنك').disabled).toBe(true);
  expect(host.textContent).not.toContain('الملف المختار:');
});

test('list refresh cannot carry a selected file into a different operational period', async () => {
  await prepare(); await chooseFile(file());
  options = {...options, started_at: '2026-11-02T12:00:00Z'}; context = {...context, started_at: options.started_at};
  await click('تحديث القائمة للتحقق'); await load(); await change(field('البنك المستلم المؤكد'), bankId);
  expect(button('رفع كشف البنك').disabled).toBe(true);
  expect(host.textContent).not.toContain('الملف المختار:');
});

test('changing the selected bank clears a file before it becomes an upload attempt', async () => {
  await prepare(); await chooseFile(file());
  await change(field('البنك المستلم المؤكد'), 'other-bank'); await change(field('البنك المستلم المؤكد'), bankId);
  expect(button('رفع كشف البنك').disabled).toBe(true);
});

test('canonical unavailable catalog refreshes after options move into native review', async () => {
  let available = false;
  const get = api.get.getMockImplementation();
  api.get.mockImplementation(url => url === `${base}/bank-accounts`
    ? Promise.resolve({data: {source: 'mz2_financial_accounts', financial_source: available ? 'native' : 'unavailable', items: [bank]}})
    : get(url));
  await render(); expect(host.textContent).not.toContain('تعذر التحقق من مصدر');
  available = true; options = {...options, financial_source: 'native'}; await load();
  await change(field('البنك المستلم المؤكد'), bankId); await change(field('معرف حركة البنك'), 'native-row');
  expect(button('اعتماد مدفوع').disabled).toBe(false);
  expect(field('كشف البنك الأصلي')).toBeUndefined();
  expect(api.post).not.toHaveBeenCalled();
});

test('double click sends one upload and keeps approval disabled until response arrives', async () => {
  const wait = deferred(); api.post.mockReturnValueOnce(wait.promise);
  await prepare(); await chooseFile(file());
  await act(async () => {button('رفع كشف البنك').click(); button('رفع كشف البنك').click();});
  expect(api.post).toHaveBeenCalledTimes(1); expect(button('اعتماد مدفوع').disabled).toBe(true);
  await act(async () => wait.resolve({data: result}));
});

test('server validation message is displayed and does not become an approval', async () => {
  api.post.mockRejectedValueOnce({response: {status: 422, data: {detail: {
    code: 'operational_driver_bank_rows_invalid', message: 'الصف 7 لا يحتوي تاريخًا صالحًا'}}}});
  await prepare(); await chooseFile(file()); await click('رفع كشف البنك');
  expect(host.textContent).toContain('الصف 7 لا يحتوي تاريخًا صالحًا');
  expect(button('إعادة رفع نفس الملف')).toBeUndefined();
  expect(api.post).toHaveBeenCalledTimes(1);
});

test('invalid row codes identify the affected spreadsheet rows in Arabic', async () => {
  api.post.mockRejectedValueOnce({response: {status: 422, data: {detail: {
    code: 'operational_driver_bank_rows_invalid', errors: [
      {row_no: 7, code: 'movement_date_invalid'}, {row_no: 8, code: 'movement_credit_debit_conflict'}], error_count: 2}}}});
  await prepare(); await chooseFile(file()); await click('رفع كشف البنك');
  expect(host.textContent).toContain('الصف 7: التاريخ غير صالح');
  expect(host.textContent).toContain('الصف 8: مبالغ الوارد والصادر متعارضة');
  expect(api.post).toHaveBeenCalledTimes(1);
});

test('a generic server message retains the specific context-change explanation', async () => {
  api.post.mockRejectedValueOnce({response: {status: 409, data: {detail: {
    code: 'operational_driver_bank_context_changed', message: 'تعذر تنفيذ العملية'}}}});
  await prepare(); await chooseFile(file()); await click('رفع كشف البنك');
  expect(host.textContent).toContain('تغيّرت جلسة البنك');
  expect(host.textContent).toContain('أعد تحميل خيارات الاعتماد');
});
