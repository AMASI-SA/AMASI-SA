import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { OpeningBalances } from './OpeningBalances';
import DailyMovements from './DailyMovements';
import OperationalBalances from './OperationalBalances';
import { operationalApi as api } from './api';
jest.mock('./api', () => ({
  operationalApi: {
    context: jest.fn(),
    entities: jest.fn(),
    opening: jest.fn(),
    finish: jest.fn(),
    addEntity: jest.fn(),
    reports: jest.fn(),
    obligations: jest.fn(),
    receipt: jest.fn(),
    movement: jest.fn(),
    audit: jest.fn()
  },
  entityKinds: [['supplier', 'موردين'], ['bank', 'بنوك'], ['cash', 'صناديق']],
  requestId: jest.fn(() => `request-${Math.random()}`),
  messageFor: () => 'تعذر إتمام العملية'
}));
let host, root;
const render = async element => {
  await act(async () => root.render(element));
};
const button = text => [...host.querySelectorAll('button')].find(b => b.textContent === text);
const click = async text => {
  await act(async () => button(text).click());
};
const input = async (el, value) => {
  await act(async () => {
    Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value').set.call(el, value);
    el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', {
      bubbles: true
    }));
  });
};
const fillOpening = async () => {
  await input(host.querySelectorAll('select')[0], 'supplier');
  await input(host.querySelectorAll('select')[1], 'supplier1');
  await click('له');
  await input(host.querySelector('input'), '100.00');
};
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  jest.clearAllMocks();
  api.entities.mockImplementation(kind => Promise.resolve({
    items: [{
      id: kind + '1',
      name: 'جهة تجريبية',
      currency: 'SAR',
      kind
    }]
  }));
  api.opening.mockResolvedValue({});
  api.finish.mockResolvedValue({});
  api.obligations.mockResolvedValue({items:[]});
  api.reports.mockResolvedValue({
    parties: [],
    obligations: [],
    summary: {},
    issues: []
  });
  api.movement.mockResolvedValue({});
  api.audit.mockResolvedValue({
    items: []
  });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});
test('missing opening fields block requests', async () => {
  await render(<OpeningBalances context={{
    opening_count: 0
  }} />);
  await click('حفظ');
  expect(api.opening).not.toHaveBeenCalled();
  expect(host.querySelector('[role=alert]')).not.toBeNull();
});
test('successful opening clears form and increments counter', async () => {
  await render(<OpeningBalances context={{
    opening_count: 2
  }} />);
  await fillOpening();
  await click('حفظ');
  expect(api.opening).toHaveBeenCalledWith(expect.objectContaining({
    party_id: 'supplier1',
    direction: 'for_party',
    amount: '100.00',
    currency: 'SAR'
  }));
  expect(host.textContent).toContain('الأرصدة المدخلة: 3');
  expect(host.querySelector('input').value).toBe('');
  expect(host.querySelector('select').value).toBe('');
});
test('ambiguous failure keeps values and same retry identity', async () => {
  api.opening.mockRejectedValueOnce(new Error('network'));
  await render(<OpeningBalances context={{
    opening_count: 0
  }} />);
  await fillOpening();
  await click('حفظ');
  const first = api.opening.mock.calls[0][0];
  expect(host.querySelector('input').value).toBe('100.00');
  await click('حفظ');
  expect(api.opening.mock.calls[1][0]).toEqual(first);
});
test('save and finish uses one atomic API with last opening', async () => {
  const finished = jest.fn();
  await render(<OpeningBalances context={{
    opening_count: 0
  }} onFinished={finished} />);
  await fillOpening();
  await click('حفظ وإنهاء');
  expect(api.opening).not.toHaveBeenCalled();
  expect(api.finish).toHaveBeenCalledWith(expect.objectContaining({
    opening: expect.objectContaining({
      party_id: 'supplier1'
    })
  }));
  expect(finished).toHaveBeenCalledTimes(1);
});
test('empty finish allowed but partially entered last row blocks', async () => {
  await render(<OpeningBalances context={{
    opening_count: 2
  }} onFinished={() => {}} />);
  await input(host.querySelector('select'), 'supplier');
  await click('حفظ وإنهاء');
  expect(api.finish).not.toHaveBeenCalled();
  await input(host.querySelector('select'), '');
  await click('حفظ وإنهاء');
  expect(api.finish).toHaveBeenCalledTimes(1);
});
test('double click in same event cannot create two openings', async () => {
  let resolve;
  api.opening.mockImplementation(() => new Promise(r => {
    resolve = r;
  }));
  await render(<OpeningBalances context={{
    opening_count: 0
  }} />);
  await fillOpening();
  await act(async () => {
    button('حفظ').click();
    button('حفظ').click();
  });
  expect(api.opening).toHaveBeenCalledTimes(1);
  await act(async () => resolve({}));
});
test('same daily form submits employee source without bank file or settlement', async () => {
  await render(<DailyMovements source="employee_app" />);
  await click('وارد');
  await input(host.querySelectorAll('select')[0], 'supplier');
  await input(host.querySelectorAll('select')[1], 'supplier1');
  await input(host.querySelectorAll('select')[2], 'bank1');
  await input(host.querySelector('input'), '25');
  await click('حفظ الحركة');
  expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({
    source: 'employee_app',
    amount: '25',
    direction: 'incoming',
    bank_id: 'bank1',
    receipt_id: null,
    allocations: []
  }));
  expect(api.receipt).not.toHaveBeenCalled();
});
test('frozen system never mounts writable movement form', async () => {
  api.context.mockResolvedValue({
    status: 'frozen',
    permissions: {
      move: true,
      view: true,
      reports: true,
      manage: true
    }
  });
  await render(<OperationalBalances source="employee_app" />);
  expect(host.textContent).toContain('للقراءة والتدقيق فقط');
  expect(button('حفظ الحركة')).toBeUndefined();
});

test('movement retry preserves request identity and values',async()=>{
 api.movement.mockRejectedValueOnce(new Error('network'));
 await render(<DailyMovements source="mezan2"/>);await click('صادر');
 await input(host.querySelectorAll('select')[0],'supplier');await input(host.querySelectorAll('select')[1],'supplier1');await input(host.querySelectorAll('select')[2],'bank1');await input(host.querySelector('input'),'80.00');await click('حفظ الحركة');
 const first=api.movement.mock.calls[0][0];expect(host.querySelector('input').value).toBe('80.00');await click('حفظ الحركة');expect(api.movement.mock.calls[1][0]).toEqual(first);
});
test('correction requires a reason and does not require a bank',async()=>{
 await render(<DailyMovements/>);await click('وارد');await input(host.querySelectorAll('select')[0],'supplier');await input(host.querySelectorAll('select')[1],'supplier1');await input(host.querySelectorAll('select')[3],'correction');await input(host.querySelector('input'),'10');await click('حفظ الحركة');expect(api.movement).not.toHaveBeenCalled();expect(host.textContent).toContain('سبب التصحيح');
 await act(async()=>{const el=host.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(el,'تصحيح موثق');el.dispatchEvent(new Event('input',{bubbles:true}));});await click('حفظ الحركة');expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({kind:'correction',bank_id:'',note:'تصحيح موثق'}));
});
test('no technical identifiers or fee percentage inputs are shown',async()=>{
 await render(<OpeningBalances context={{opening_count:0}}/>);await fillOpening();expect(host.textContent).not.toMatch(/supplier1|request_id|allocation|ledger|journal|cutoff/i);
 await render(<DailyMovements/>);expect(host.textContent).not.toMatch(/نسبة العمولة|سياسة قديمة/);
});
test('read-only permission does not expose editing controls',async()=>{
 api.context.mockResolvedValue({status:'active',permissions:{view:true,reports:true,move:false,manage:false}});await render(<OperationalBalances/>);expect(button('الحركات المالية اليومية')).toBeUndefined();expect(button('حفظ')).toBeUndefined();expect(host.textContent).toContain('الأرصدة والتقارير التشغيلية');
});

test('mobile daily permission never fetches private reports or renders supplier return',async()=>{
 api.context.mockResolvedValue({status:'active',permissions:{view:true,move:true,manage:false,reports:false}});
 await render(<OperationalBalances source="employee_app"/>);
 expect(button('الأرصدة والتقارير التشغيلية')).toBeUndefined();
 expect(host.textContent).not.toContain('مرتجع مقبول من المورد');
 expect(api.reports).not.toHaveBeenCalled();expect(api.audit).not.toHaveBeenCalled();
 await input(host.querySelectorAll('select')[0],'supplier');await input(host.querySelectorAll('select')[1],'supplier1');await input(host.querySelectorAll('select')[3],'settlement');
 expect(api.obligations).toHaveBeenCalledWith('supplier','supplier1');expect(api.reports).not.toHaveBeenCalled();
});
test('report refreshes every thirty seconds and stops on unmount',async()=>{
 jest.useFakeTimers();
 try {
  api.context.mockResolvedValue({status:'active',permissions:{view:true,reports:true,move:false,manage:false}});
  await render(<OperationalBalances/>);expect(api.reports).toHaveBeenCalledTimes(1);
  await act(async()=>jest.advanceTimersByTime(30000));expect(api.reports).toHaveBeenCalledTimes(2);
  await render(<div/>);await act(async()=>jest.advanceTimersByTime(30000));expect(api.reports).toHaveBeenCalledTimes(2);
 } finally {jest.useRealTimers();}
});
