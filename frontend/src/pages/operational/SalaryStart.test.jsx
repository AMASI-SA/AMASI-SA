import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import SalaryStart from './SalaryStart';
import OperationalBalances from './OperationalBalances';
import {operationalApi as api, requestId} from './api';

jest.mock('./api', () => ({operationalApi: {
  salaryStartContext: jest.fn(), previewSalaryStart: jest.fn(), approveSalaryStart: jest.fn(),
  context: jest.fn(), reports: jest.fn(), audit: jest.fn(), inventoryPurchases: jest.fn(), inventoryCatalog: jest.fn()
}, requestId: jest.fn(), entityKinds: [], messageFor: () => 'تعذر إتمام العملية', feeIncomplete: () => false}));
const scope = 'a'.repeat(64), other = 'b'.repeat(64), proof = 'c'.repeat(64);
const owner = () => ({can_manage_salary_start: true, session_scope: scope, status: 'active', started_at: '2026-11-15T23:59:59+03:00'});
const settings = () => ({source: 'operational_salary_cutover', read_only: true, session_scope: scope,
  started_at: owner().started_at, first_day: '2026-11-15', effective_first_day: '2026-11-16',
  timezone: 'Asia/Riyadh', approved: false});
const preview = () => ({first_day: '2026-11-15', business_date: '2026-11-15', session_scope: scope,
  preview_hash: proof, employee_count: 1, new_days: 1, additional_amounts: {SAR: '100.00'},
  can_approve: true, issues: [], opening_confirmation_required: true});
let root, host;
const render = async (context = owner()) => {await act(async () => root.render(<SalaryStart context={context}/>));};
const button = text => [...host.querySelectorAll('button')].find(b => b.textContent === text);
const click = async text => {await act(async () => button(text).click());};
const checkbox = async () => {await act(async () => host.querySelector('input[type="checkbox"]').click());};
const showPreview = () => click('معاينة الاستحقاق من يوم القطع');
const confirm = () => click('اعتماد بداية الرواتب من يوم القطع');
const deferred = () => {let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject};};
beforeEach(() => {
  // Each scenario owns its mock calls, implementations and queued responses.
  jest.resetAllMocks();
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  requestId.mockReturnValue('payroll-operation-1');
  api.salaryStartContext.mockResolvedValue(settings()); api.previewSalaryStart.mockResolvedValue(preview());
  api.approveSalaryStart.mockResolvedValue({ok: true, first_day: '2026-11-15', bank_money_changed: false});
});
afterEach(() => {act(() => root.unmount()); host.remove(); jest.restoreAllMocks();});

test('loading reads only context and never previews or approves automatically', async () => {
  const wait = deferred(); api.salaryStartContext.mockReturnValueOnce(wait.promise);
  await render(); expect(host.textContent).toContain('جارٍ التحقق');
  expect(api.previewSalaryStart).not.toHaveBeenCalled(); expect(api.approveSalaryStart).not.toHaveBeenCalled();
  await act(async () => wait.resolve(settings()));
  expect(host.textContent).toContain('2026-11-15'); expect(host.querySelector('input[type="date"]')).toBeNull();
});

test('cutover preview and explicit opening confirmation precede approval', async () => {
  await render(); await showPreview();
  expect(api.previewSalaryStart).toHaveBeenCalledWith({first_day: '2026-11-15'}, expect.objectContaining({signal: expect.any(AbortSignal)}));
  expect(host.textContent).toContain('100.00'); expect(button('اعتماد بداية الرواتب من يوم القطع').disabled).toBe(true);
  await confirm(); expect(api.approveSalaryStart).not.toHaveBeenCalled();
  await checkbox(); await confirm();
  expect(api.approveSalaryStart).toHaveBeenCalledWith({request_id: 'payroll-operation-1', first_day: '2026-11-15',
    expected_preview_hash: proof, expected_session_scope: scope, opening_salaries_excluded: true});
  expect(host.textContent).toContain('تم اعتماد البداية'); expect(host.textContent).toContain('ليس دفعًا للراتب');
  expect(button('اعتماد بداية الرواتب من يوم القطع')).toBeUndefined();
});

test('money remains exact and currencies are not summed in the browser', async () => {
  api.previewSalaryStart.mockResolvedValue({...preview(), additional_amounts: {SAR: '9007199254740993.25', USD: '10.33'}});
  await render(); await showPreview();
  expect(host.textContent).toContain('9007199254740993.25'); expect(host.textContent).toContain('10.33');
  expect(host.querySelectorAll('tbody tr')).toHaveLength(2);
});

test('incomplete sources expose a reason and never offer approval', async () => {
  api.previewSalaryStart.mockResolvedValue({...preview(), can_approve: false, issues: [{code: 'salary_contract_missing'}]});
  await render(); await showPreview();
  expect(host.textContent).toContain('عقد راتب غير مكتمل'); expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  expect(api.approveSalaryStart).not.toHaveBeenCalled();
});

test('blocked preview exposes exact daily evidence without approving rounding differences', async () => {
  api.previewSalaryStart.mockResolvedValue({...preview(), can_approve: false, issues: [
    {code: 'operational_salary_recorded_day_reconciliation_required', source_id: 'salary:employee:2026-11-16',
      business_date: '2026-11-16', reason: 'cutover_rounding_changed', currency: 'SAR', proposed_currency: 'SAR',
      recorded: '33.33', proposed: '33.34'},
    {code: 'employee_salary_contract_incomplete', source_id: 'employee-missing'},
    {code: 'operational_salary_recorded_day_reconciliation_required', source_id: 'salary:other:2026-11-16',
      currency: 'SAR', recorded: '10.00', proposed: null}
  ]});
  await render(); await showPreview();
  expect(host.textContent).toContain('توزيع التقريب');
  expect(host.textContent).toContain('salary:employee:2026-11-16');
  expect(host.textContent).toContain('33.33 SAR');
  expect(host.textContent).toContain('33.34 SAR');
  expect(host.textContent).toContain('employee-missing');
  expect(host.textContent).toContain('غير متاح');
  expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  expect(api.approveSalaryStart).not.toHaveBeenCalled();
});

test.each([
  {session_scope: other}, {first_day: '2026-11-01'}, {preview_hash: 'bad'},
  {additional_amounts: {SAR: 100}}, {additional_amounts: {}}, {issues: [{code: 'missing'}]},
  {business_date: '2026-11-14'}, {opening_confirmation_required: false}
])('malformed or foreign preview is not authorizable: %j', async patch => {
  api.previewSalaryStart.mockResolvedValue({...preview(), ...patch});
  await render(); await showPreview();
  expect(host.textContent).toContain('تعذر التحقق'); expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  expect(api.approveSalaryStart).not.toHaveBeenCalled();
});

test.each([{can_manage_salary_start: false}, {status: 'frozen'}, {session_scope: ''}])('forbidden context makes no payroll request: %j', async patch => {
  await render({...owner(), ...patch}); expect(api.salaryStartContext).not.toHaveBeenCalled();
  expect(host.textContent).toContain('متاح للمالك');
});

test('an already validated approved policy prevents a second approval', async () => {
  api.salaryStartContext.mockResolvedValue({...settings(), approved: true, effective_first_day: '2026-11-15'});
  await render(); expect(host.textContent).toContain('تم اعتماد البداية');
  expect(button('معاينة الاستحقاق من يوم القطع')).toBeUndefined(); expect(api.approveSalaryStart).not.toHaveBeenCalled();
});

test('a contradictory approved policy is not displayed as successful', async () => {
  api.salaryStartContext.mockResolvedValue({...settings(), approved: true});
  await render(); expect(host.textContent).toContain('تعذر التحقق');
  expect(host.textContent).not.toContain('تم اعتماد البداية');
});

test('unknown approval result retries with the identical request and locks fresh preview', async () => {
  api.approveSalaryStart.mockRejectedValueOnce(new Error('network'));
  await render(); await showPreview(); await checkbox(); await confirm();
  const original = api.approveSalaryStart.mock.calls[0][0];
  expect(button('معاينة الاستحقاق من يوم القطع').disabled).toBe(true);
  expect(host.querySelector('input').disabled).toBe(true);
  await click('إعادة إرسال طلب الاعتماد نفسه');
  expect(api.approveSalaryStart.mock.calls[1][0]).toEqual(original); expect(requestId).toHaveBeenCalledTimes(1);
});

test('double clicks during approval do not send two requests', async () => {
  const wait = deferred(); api.approveSalaryStart.mockReturnValueOnce(wait.promise);
  await render(); await showPreview(); await checkbox();
  await act(async () => {button('اعتماد بداية الرواتب من يوم القطع').click(); button('اعتماد بداية الرواتب من يوم القطع').click();});
  expect(api.approveSalaryStart).toHaveBeenCalledTimes(1);
  await act(async () => wait.resolve({ok: true, first_day: '2026-11-15', bank_money_changed: false}));
});

test.each([403, 409, 422])('definitive rejection %s invalidates preview and confirmation', async status => {
  api.approveSalaryStart.mockRejectedValueOnce({response: {status}});
  await render(); await showPreview(); await checkbox(); await confirm();
  expect(host.querySelector('input[type="checkbox"]')).toBeNull(); expect(host.textContent).not.toContain('100.00');
  expect(button('معاينة الاستحقاق من يوم القطع')).toBeUndefined();
  await click('إعادة تحميل الحالة'); await showPreview();
  expect(host.querySelector('input[type="checkbox"]').checked).toBe(false);
});

test('reloading after unknown outcome reads approved status without re-posting', async () => {
  api.approveSalaryStart.mockRejectedValueOnce(new Error('network'));
  await render(); await showPreview(); await checkbox(); await confirm();
  api.salaryStartContext.mockResolvedValue({...settings(), approved: true, effective_first_day: '2026-11-15'});
  await click('إعادة تحميل الحالة');
  expect(host.textContent).toContain('تم اعتماد البداية'); expect(api.approveSalaryStart).toHaveBeenCalledTimes(1);
});

test('late preview from a previous actor cannot leak or authorize balances', async () => {
  const wait = deferred(); api.previewSalaryStart.mockReturnValueOnce(wait.promise);
  await render(); await showPreview();
  api.salaryStartContext.mockResolvedValue({...settings(), session_scope: other});
  await render({...owner(), session_scope: other});
  await act(async () => wait.resolve(preview()));
  expect(host.textContent).not.toContain('100.00'); expect(host.querySelector('input')).toBeNull();
});

test('revoking permission clears preview and blocks approval', async () => {
  await render(); await showPreview(); await checkbox();
  await render({...owner(), can_manage_salary_start: false});
  expect(host.textContent).not.toContain('100.00'); expect(host.querySelector('input')).toBeNull();
  expect(api.approveSalaryStart).not.toHaveBeenCalled();
});

test('zero missing days are explicit and still require one policy confirmation', async () => {
  api.previewSalaryStart.mockResolvedValue({...preview(), new_days: 0, additional_amounts: {}});
  await render(); await showPreview(); expect(host.textContent).toContain('لا توجد أيام ناقصة');
  expect(button('اعتماد بداية الرواتب من يوم القطع').disabled).toBe(true);
});

test.each([true, false])('parent navigation uses explicit owner permission %s', async enabled => {
  api.context.mockResolvedValue({...owner(), can_manage_salary_start: enabled, permissions: {}});
  await act(async () => root.render(<OperationalBalances/>));
  if (enabled) {await click('بداية الرواتب'); expect(api.salaryStartContext).toHaveBeenCalledTimes(1);}
  else {expect(button('بداية الرواتب')).toBeUndefined(); expect(api.salaryStartContext).not.toHaveBeenCalled();}
});

test('employee-app surface cannot expose owner payroll setup', async () => {
  api.context.mockResolvedValue({...owner(), permissions: {}});
  await act(async () => root.render(<OperationalBalances source="employee_app"/>));
  expect(button('بداية الرواتب')).toBeUndefined(); expect(api.salaryStartContext).not.toHaveBeenCalled();
});

const visibility = async value => act(async () => {
  Object.defineProperty(document, 'visibilityState', {configurable: true, value});
  document.dispatchEvent(new Event('visibilitychange'));
});
beforeEach(() => Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'}));
afterEach(() => Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'}));

test('salary preview aborts hidden work and joins it before a visible retry', async () => {
  const wait = deferred(); api.previewSalaryStart.mockReturnValueOnce(wait.promise);
  await render(); await showPreview();
  const signal = api.previewSalaryStart.mock.calls[0][1].signal;
  await visibility('hidden'); expect(signal.aborted).toBe(true);
  await visibility('visible'); expect(api.previewSalaryStart).toHaveBeenCalledTimes(1);
  await act(async () => wait.resolve(preview()));
  expect(api.previewSalaryStart).toHaveBeenCalledTimes(2); expect(host.textContent).toContain('100.00');
  await visibility('hidden'); await visibility('visible');
  expect(api.previewSalaryStart).toHaveBeenCalledTimes(2);
});

test('salary approval remains durable while a visible read is separately cancellable', async () => {
  const wait = deferred(); api.approveSalaryStart.mockReturnValueOnce(wait.promise);
  await render(); await showPreview(); await checkbox(); await confirm();
  expect(api.approveSalaryStart.mock.calls[0]).toHaveLength(1);
  await visibility('hidden');
  await act(async () => wait.resolve({ok: true, first_day: '2026-11-15', bank_money_changed: false}));
  expect(api.approveSalaryStart).toHaveBeenCalledTimes(1); expect(host.textContent).toContain('تم اعتماد البداية');
  expect(api.salaryStartContext).toHaveBeenCalledTimes(1);
});
