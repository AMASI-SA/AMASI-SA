import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { OpeningBalances } from './OpeningBalances';
import DailyMovementsComponent from './DailyMovements';
import {pendingMovementKey} from './pendingMovementStorage';
const DailyMovements = props => <DailyMovementsComponent storageScope="test-owner:test-actor" {...props}/>;
import OperationalBalances from './OperationalBalances';
import { operationalApi as api, requestId } from './api';
jest.mock('./api', () => ({
  operationalApi: {
    context: jest.fn(),
    inventoryPurchases: jest.fn(),
    inventoryCatalog: jest.fn(),
    inventoryPurchaseEntry: jest.fn(),
    entities: jest.fn(),
    opening: jest.fn(),
    finish: jest.fn(),
    addEntity: jest.fn(),
    reports: jest.fn(),
    obligations: jest.fn(),
    receipt: jest.fn(),
    movement: jest.fn(),
    movements: jest.fn(),
    audit: jest.fn()
  },
  entityKinds: [['supplier', 'موردين'], ['bank', 'بنوك'], ['cash', 'صناديق'],['employee_custody','عهد الموظفين'],['operating_expense','مصروفات تشغيلية'],['owner_withdrawal','سحوبات المالك/المدير'],['external_person','جهات خارجية']],
  requestId: jest.fn(() => `request-${Math.random()}`),
  messageFor: () => 'تعذر إتمام العملية', feeIncomplete: issue => /fee/.test(issue?.code||'')
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
  // CRA resetMocks clears the factory implementation before every case.
  requestId.mockImplementation(() => `request-${Math.random()}`);
  localStorage.clear();
  api.context.mockResolvedValue({session_scope:"test-owner:test-actor",status:"active",permissions:{move:true}});
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
  api.movements.mockResolvedValue({items:[]});
  api.inventoryPurchases.mockResolvedValue({items:[],stock:[]});
  api.inventoryCatalog.mockResolvedValue({items:[]});
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
  await input(host.querySelectorAll('select')[2], 'bank:bank1');
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
  api.context.mockResolvedValue({session_scope:'test-owner:test-actor',
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
 await input(host.querySelectorAll('select')[0],'supplier');await input(host.querySelectorAll('select')[1],'supplier1');await input(host.querySelectorAll('select')[2],'bank:bank1');await input(host.querySelector('input'),'80.00');await click('حفظ الحركة');
 const first=api.movement.mock.calls[0][0];expect(host.querySelector('input').value).toBe('80.00');await click('إعادة محاولة الحركة');expect(api.movement.mock.calls[1][0]).toEqual(first);
});
test('correction requires a reason and does not require a bank',async()=>{
 await render(<DailyMovements/>);await click('وارد');await input(host.querySelectorAll('select')[0],'supplier');await input(host.querySelectorAll('select')[1],'supplier1');await input(host.querySelectorAll('select')[3],'correction');await input(host.querySelector('input'),'10');await click('حفظ الحركة');expect(api.movement).not.toHaveBeenCalled();expect(host.textContent).toContain('سبب التصحيح');
 await act(async()=>{const el=host.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(el,'تصحيح موثق');el.dispatchEvent(new Event('input',{bubbles:true}));});await click('حفظ الحركة');expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({kind:'correction',bank_id:null,note:'تصحيح موثق'}));
});
test('no technical identifiers or fee percentage inputs are shown',async()=>{
 await render(<OpeningBalances context={{opening_count:0}}/>);await fillOpening();expect(host.textContent).not.toMatch(/supplier1|request_id|allocation|ledger|journal|cutoff/i);
 await render(<DailyMovements/>);expect(host.textContent).not.toMatch(/نسبة العمولة|سياسة قديمة/);
});
test('read-only permission does not expose editing controls',async()=>{
 api.context.mockResolvedValue({session_scope:'test-owner:test-actor',status:'active',permissions:{view:true,reports:true,move:false,manage:false}});await render(<OperationalBalances/>);expect(button('الحركات المالية اليومية')).toBeUndefined();expect(button('حفظ')).toBeUndefined();expect(host.textContent).toContain('الأرصدة والتقارير التشغيلية');
});

test('mobile daily permission never fetches private reports or renders supplier return',async()=>{
 api.context.mockResolvedValue({session_scope:'test-owner:test-actor',status:'active',permissions:{view:true,move:true,manage:false,reports:false}});
 await render(<OperationalBalances source="employee_app"/>);
 expect(button('الأرصدة والتقارير التشغيلية')).toBeUndefined();
 expect(host.textContent).not.toContain('مرتجع مقبول من المورد');
 expect(api.reports).not.toHaveBeenCalled();expect(api.audit).not.toHaveBeenCalled();
 await click('حركات أخرى');await input(host.querySelectorAll('select')[0],'supplier');await input(host.querySelectorAll('select')[1],'supplier1');await input(host.querySelectorAll('select')[3],'settlement');
 expect(api.obligations).toHaveBeenCalledWith('supplier','supplier1');expect(api.reports).not.toHaveBeenCalled();
});
test('report refreshes every thirty seconds and stops on unmount',async()=>{
 jest.useFakeTimers();
 try {
  api.context.mockResolvedValue({session_scope:'test-owner:test-actor',status:'active',permissions:{view:true,reports:true,move:false,manage:false}});
  await render(<OperationalBalances/>);expect(api.reports).toHaveBeenCalledTimes(1);
  await act(async()=>jest.advanceTimersByTime(30000));expect(api.reports).toHaveBeenCalledTimes(2);
  await render(<div/>);await act(async()=>jest.advanceTimersByTime(30000));expect(api.reports).toHaveBeenCalledTimes(2);
 } finally {jest.useRealTimers();}
});

test('expense paid from custody sends one source account without a second bank payment',async()=>{
 await render(<DailyMovements/>);
 await input(host.querySelectorAll('select')[0],'operating_expense');await input(host.querySelectorAll('select')[1],'operating_expense1');
 await input(host.querySelectorAll('select')[2],'employee_custody:employee_custody1');await input(host.querySelector('input'),'500.00');await click('حفظ الحركة');
 expect(api.movement).toHaveBeenCalledTimes(1);expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({direction:'outgoing',party_type:'operating_expense',kind:'payment',source_account_type:'employee_custody',bank_id:'employee_custody1',amount:'500.00'}));
});
test('custody funding and return use payment and collection with bank cash accounts only',async()=>{
 await render(<DailyMovements/>);await input(host.querySelectorAll('select')[0],'employee_custody');await input(host.querySelectorAll('select')[1],'employee_custody1');await click('صادر');await input(host.querySelectorAll('select')[2],'bank:bank1');await input(host.querySelector('input'),'5000');await click('حفظ الحركة');
 expect(api.movement).toHaveBeenLastCalledWith(expect.objectContaining({party_type:'employee_custody',kind:'payment',direction:'outgoing',source_account_type:'bank',amount:'5000'}));
 await input(host.querySelectorAll('select')[0],'employee_custody');await input(host.querySelectorAll('select')[1],'employee_custody1');await click('وارد');await input(host.querySelectorAll('select')[2],'cash:cash1');await input(host.querySelector('input'),'100');await click('حفظ الحركة');
 expect(api.movement).toHaveBeenLastCalledWith(expect.objectContaining({party_type:'employee_custody',kind:'collection',direction:'incoming',source_account_type:'cash',amount:'100'}));
});
test('owner withdrawal is a distinct outgoing type and cannot select employee custody',async()=>{
 await render(<DailyMovements/>);await input(host.querySelectorAll('select')[0],'owner_withdrawal');await input(host.querySelectorAll('select')[1],'owner_withdrawal1');
 expect([...host.querySelectorAll('select')[2].options].map(o=>o.value)).not.toContain('employee_custody:employee_custody1');expect(button('وارد').disabled).toBe(true);
 await input(host.querySelectorAll('select')[2],'cash:cash1');await input(host.querySelector('input'),'2000');await click('حفظ الحركة');expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({party_type:'owner_withdrawal',direction:'outgoing',source_account_type:'cash',amount:'2000'}));
});
test('external entity creation in movements requires manage and retains retry identity',async()=>{
 api.addEntity.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce({id:'external_person1',kind:'external_person',name:'جهة جديدة',currency:'SAR'});
 await render(<DailyMovements canManage={true}/>);await input(host.querySelectorAll('select')[0],'external_person');await click('+ إضافة جهة خارجية');await input(host.querySelector('.op-add input'),'جهة جديدة');await click('إضافة');
 const first=api.addEntity.mock.calls[0];expect(host.querySelector('.op-add input').value).toBe('جهة جديدة');await click('إضافة');expect(api.addEntity.mock.calls[1]).toEqual(first);expect(host.querySelector('.op-add')).toBeNull();
 await render(<DailyMovements canManage={false}/>);expect(button('+ إضافة جهة خارجية')).toBeUndefined();
});
test('incomplete refund fee policy uses the exact owner-facing message',async()=>{
 api.context.mockResolvedValue({session_scope:'test-owner:test-actor',status:'active',permissions:{view:true,reports:true,move:false,manage:false}});
 api.reports.mockResolvedValue({parties:[],obligations:[],summary:{},issues:[{code:'refund_fee_treatment_incomplete'}]});
 await render(<OperationalBalances/>);expect(host.textContent).toContain('إعداد العمولة غير مكتمل');expect(host.textContent).not.toContain('refund_fee_treatment_incomplete');
});

test('recurring estimate is explicitly allocatable while other estimates stay excluded and retry remains stable',async()=>{
 api.obligations.mockResolvedValue({items:[
  {id:'recurring-rent',kind:'recurring',party_type:'external_person',party_id:'external_person1',currency:'SAR',direction:'payable',outstanding:'20.10',expected:'100.20',label:'إيجار الشهر'},
  {id:'recurring-closed',kind:'recurring',party_type:'external_person',party_id:'external_person1',currency:'SAR',direction:'payable',outstanding:'0.00',expected:'200.00',available_to_pay:'0.00',label:'التزام مستنفد'},
  {id:'unconfirmed-other',kind:'supplier',party_type:'external_person',party_id:'external_person1',currency:'SAR',direction:'payable',outstanding:'0.00',expected:'500.00',label:'تقدير غير دوري'},
 ]});
 api.movement.mockRejectedValueOnce(new Error('network'));
 await render(<DailyMovements/>);await click('صادر');await input(host.querySelectorAll('select')[0],'external_person');await input(host.querySelectorAll('select')[1],'external_person1');await input(host.querySelectorAll('select')[2],'bank:bank1');await input(host.querySelectorAll('select')[3],'settlement');
 expect(host.textContent).toContain('التزام دوري تقديري — يؤكد الجزء المدفوع عند الحفظ');expect(host.textContent).toContain('المتاح 120.30 SAR');expect(host.textContent).not.toContain('تقدير غير دوري');expect(host.textContent).not.toContain('التزام مستنفد');
 await input(host.querySelectorAll('input')[0],'75.25');await input(host.querySelectorAll('input')[1],'75.25');await click('حفظ الحركة');
 const first=api.movement.mock.calls[0][0];expect(first.allocations).toEqual([{obligation_id:'recurring-rent',amount:'75.25'}]);expect(first.kind).toBe('settlement');await click('إعادة محاولة الحركة');expect(api.movement.mock.calls[1][0]).toEqual(first);
});

test('native recurring expense category exposes explicit settlement instead of silently paying as advance',async()=>{
 api.obligations.mockResolvedValue({items:[{id:'native-rent:day',kind:'recurring',party_type:'operating_expense',party_id:'operating_expense1',currency:'SAR',direction:'payable',outstanding:'0.00',expected:'100.00',available_to_pay:'100.00',pending_confirmation:true,label:'إيجار الفرع'}]});
 await render(<DailyMovements/>);await input(host.querySelectorAll('select')[0],'operating_expense');await input(host.querySelectorAll('select')[1],'operating_expense1');
 expect(api.obligations).toHaveBeenCalledWith('operating_expense','operating_expense1');expect(host.textContent).toContain('تسوية التزام دوري');
 await input(host.querySelectorAll('select')[2],'bank:bank1');await input(host.querySelectorAll('select')[3],'settlement');await input(host.querySelectorAll('input')[0],'40.00');await input(host.querySelectorAll('input')[1],'40.00');await click('حفظ الحركة');
 expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({kind:'settlement',party_type:'operating_expense',source_account_type:'bank',amount:'40.00',allocations:[{obligation_id:'native-rent:day',amount:'40.00'}]}));
});

const fillMovement = async () => {
 await click('صادر');await input(host.querySelectorAll('select')[0],'supplier');await input(host.querySelectorAll('select')[1],'supplier1');await input(host.querySelectorAll('select')[2],'bank:bank1');await input(host.querySelector('input'),'80.00');
};

const cardClick=async text=>{await act(async()=>[...host.querySelectorAll('.op-tile')].find(b=>b.textContent.includes(text)).click());};
test.each(['supplier','employee'])('card %s payment supports cash, date, no receipt and exact retry',async kind=>{
 await render(<DailyMovements cards/>);
 expect(host.querySelectorAll('.op-tile')).toHaveLength(11);
 await cardClick(kind==='supplier'?'الموردون':'الموظفون');await cardClick('جهة تجريبية');
 await input(host.querySelectorAll('select')[0],'cash');await input(host.querySelectorAll('select')[1],'cash1');
 await input(host.querySelector('input[inputmode="decimal"]'),'125.50');
 await input(host.querySelector('input[type=date]'),'2026-10-07');
 api.movement.mockRejectedValueOnce(new Error('response lost'));
 await click('حفظ الحركة');const first=api.movement.mock.calls[0][0];
 expect(first).toEqual(expect.objectContaining({party_type:kind,kind:'payment',direction:'outgoing',bank_id:'cash1',source_account_type:'cash',business_date:'2026-10-07',amount:'125.50',receipt_id:null}));
 expect(host.querySelector('fieldset').disabled).toBe(true);
 await click('إعادة محاولة الحركة');expect(api.movement.mock.calls[1][0]).toEqual(first);
 expect(host.querySelectorAll('.op-tile')).toHaveLength(11);expect(api.receipt).not.toHaveBeenCalled();
});
test('cash card is cash-only supplier or employee payment',async()=>{
 await render(<DailyMovements cards canCreateCash/>);await cardClick('الصناديق');
 expect(button('+ إضافة صندوق')).toBeDefined();await cardClick('جهة تجريبية');
 await input(host.querySelector('select'),'employee');await cardClick('جهة تجريبية');
 expect(host.querySelectorAll('select')).toHaveLength(1);expect(host.querySelector('select').disabled).toBe(true);expect(host.querySelector('select').value).toBe('cash1');
 expect(host.textContent).toContain('السداد كاش');expect(host.textContent).not.toContain('التمويل من');
 await input(host.querySelector('input[inputmode="decimal"]'),'200');await click('حفظ الحركة');
 expect(api.movement).toHaveBeenLastCalledWith(expect.objectContaining({kind:'payment',party_type:'employee',party_id:'employee1',direction:'outgoing',bank_id:'cash1',source_account_type:'cash'}));
});
test.each(['supplier','cash'])('custody card %s operation has one source and no inventory shortcut',async operation=>{
 await render(<DailyMovements cards/>);await cardClick('العهد');await cardClick('جهة تجريبية');
 expect(host.textContent).not.toContain('غير متاحة حتى اعتماد إثبات');
 if(operation==='supplier') {await click('سداد مورد من العهدة');await cardClick('جهة تجريبية');expect(host.querySelector('select').disabled).toBe(true);}
 else {await click('نقل العهدة إلى صندوق');await input(host.querySelector('select'),'cash1');expect([...host.querySelector('select').options].map(o=>o.value)).not.toContain('bank1');}
 await input(host.querySelector('input[inputmode="decimal"]'),'40');await click('حفظ الحركة');
 expect(api.movement).toHaveBeenLastCalledWith(expect.objectContaining(operation==='supplier'?{kind:'payment',party_type:'supplier',bank_id:'employee_custody1',source_account_type:'employee_custody',direction:'outgoing'}:{kind:'collection',party_type:'employee_custody',party_id:'employee_custody1',bank_id:'cash1',source_account_type:'cash',direction:'incoming'}));
});
test('non-owner cash card hides creation and platforms keep incoming bank form',async()=>{
 await render(<DailyMovements cards/>);await cardClick('الصناديق');expect(button('+ إضافة صندوق')).toBeUndefined();await click('العودة للعمليات');
 await cardClick('التسويات');await cardClick('جهة تجريبية');expect(host.querySelectorAll('select')).toHaveLength(1);
 expect(host.textContent).toContain('المبلغ الوارد');await input(host.querySelector('select'),'bank1');await input(host.querySelector('input[inputmode="decimal"]'),'450');await click('حفظ الحركة');
 expect(api.movement).toHaveBeenLastCalledWith(expect.objectContaining({kind:'collection',party_type:'provider',direction:'incoming',receipt_id:null}));
});
test('lost response survives remount and retries exact durable payload with locked edits',async()=>{
 api.movement.mockRejectedValueOnce(new Error('response lost'));
 await render(<DailyMovements/>);await fillMovement();await click('حفظ الحركة');
 const first=api.movement.mock.calls[0][0];expect(JSON.parse(localStorage.getItem(pendingMovementKey('test-owner:test-actor'))).payload).toEqual(first);
 await render(<div/>);await render(<DailyMovements/>);
 expect(host.querySelector('input').value).toBe('80.00');expect(host.querySelector('fieldset').disabled).toBe(true);
 await input(host.querySelector('input'),'90.00');expect(host.querySelector('input').value).toBe('80.00');
 await click('إعادة محاولة الحركة');expect(api.movement.mock.calls[1][0]).toEqual(first);expect(api.receipt).not.toHaveBeenCalled();expect(localStorage.getItem(pendingMovementKey('test-owner:test-actor'))).toBeNull();expect(host.querySelector('fieldset').disabled).toBe(false);
});
test('pending payload is isolated by authoritative session scope',async()=>{
 api.movement.mockRejectedValueOnce(new Error('response lost'));
 await render(<DailyMovements/>);await fillMovement();await click('حفظ الحركة');
 await render(<div/>);await render(<DailyMovements storageScope="different-tenant-actor"/>);
 expect(host.querySelector('input').value).toBe('');expect(button('إعادة محاولة الحركة')).toBeUndefined();expect(localStorage.getItem(pendingMovementKey('test-owner:test-actor'))).not.toBeNull();
});
test('storage failure blocks post and missing session scope fails closed',async()=>{
 await render(<DailyMovements/>);await fillMovement();
 const spy=jest.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('quota');});
 try {await click('حفظ الحركة');expect(api.movement).not.toHaveBeenCalled();expect(host.textContent).toContain('لم تُرسل الحركة');} finally {spy.mockRestore();}
 await render(<div/>);await render(<DailyMovements storageScope={null}/>);expect(button('حفظ الحركة').disabled).toBe(true);
});
test('conflict retains exact pending request but definitive validation rejection permits correction',async()=>{
 api.movement.mockRejectedValueOnce({response:{status:409}}).mockRejectedValueOnce({response:{status:422}});
 await render(<DailyMovements/>);await fillMovement();await click('حفظ الحركة');const first=api.movement.mock.calls[0][0];
 expect(host.querySelector('fieldset').disabled).toBe(true);await click('إعادة محاولة الحركة');expect(api.movement.mock.calls[1][0]).toEqual(first);expect(localStorage.getItem(pendingMovementKey('test-owner:test-actor'))).toBeNull();expect(host.querySelector('fieldset').disabled).toBe(false);
 await input(host.querySelector('input'),'70.00');await click('حفظ الحركة');expect(api.movement.mock.calls[2][0].request_id).not.toBe(first.request_id);expect(api.movement.mock.calls[2][0].amount).toBe('70.00');
});
test('switching custody expense to recurring settlement clears and excludes custody source',async()=>{
 api.obligations.mockResolvedValue({items:[{id:'rent',kind:'recurring',party_type:'operating_expense',party_id:'operating_expense1',currency:'SAR',direction:'payable',outstanding:'0.00',expected:'100.00',available_to_pay:'100.00'}]});
 await render(<DailyMovements/>);await input(host.querySelectorAll('select')[0],'operating_expense');await input(host.querySelectorAll('select')[1],'operating_expense1');await input(host.querySelectorAll('select')[2],'employee_custody:employee_custody1');await input(host.querySelectorAll('select')[3],'settlement');
 const account=host.querySelectorAll('select')[2];expect(account.value).toBe('');expect([...account.options].map(o=>o.value)).not.toContain('employee_custody:employee_custody1');await input(host.querySelector('input'),'40');await click('حفظ الحركة');expect(api.movement).not.toHaveBeenCalled();
});

test('definitive business rejection unlocks amount without losing original proof',async()=>{
 api.movement.mockRejectedValueOnce({response:{status:409,data:{detail:{code:'operational_custody_insufficient',not_applied:true}}}});
 await render(<DailyMovements/>);await fillMovement();await input(host.querySelector('input'),'600');await click('حفظ الحركة');
 const first=api.movement.mock.calls[0][0];expect(first.expected_session_scope).toBe('test-owner:test-actor');expect(host.querySelector('fieldset').disabled).toBe(false);expect(localStorage.getItem(pendingMovementKey('test-owner:test-actor'))).toBeNull();
 await input(host.querySelector('input'),'500');await click('حفظ الحركة');expect(api.movement.mock.calls[1][0].amount).toBe('500');expect(api.movement.mock.calls[1][0].request_id).not.toBe(first.request_id);
});
test('scope change after lost response never replays old intent under new owner',async()=>{
 api.movement.mockRejectedValueOnce(new Error('response lost'));
 await render(<DailyMovements/>);await fillMovement();await click('حفظ الحركة');const first=api.movement.mock.calls[0][0];
 api.context.mockResolvedValue({session_scope:'different-owner',status:'active',permissions:{move:true}});
 await click('إعادة محاولة الحركة');expect(api.movement).toHaveBeenCalledTimes(1);expect(JSON.parse(localStorage.getItem(pendingMovementKey('test-owner:test-actor'))).payload).toEqual(first);expect(host.querySelector('fieldset').disabled).toBe(true);
});

test('successful Web save rereads persisted history through the existing API',async()=>{
 api.movements.mockResolvedValueOnce({items:[]}).mockResolvedValue({items:[{id:'saved-operation',name:'المورد المحفوظ',amount:'50.00',currency:'SAR',direction:'outgoing',source:'mezan2',reference:'WEB-SAVED'}]});
 await render(<OperationalBalances/>);await click('حركات أخرى');await fillMovement();await click('حفظ الحركة');
 expect(api.movement).toHaveBeenCalledTimes(1);expect(api.movements).toHaveBeenCalledTimes(2);
 expect(host.textContent).toContain('WEB-SAVED');expect(host.querySelectorAll('tbody tr')).toHaveLength(1);
});

test('manual operational movement has no receipt upload requirement',async()=>{
 await render(<DailyMovements/>);
 expect(host.querySelector('input[type=file]')).toBeNull();
 await fillMovement();await click('حفظ الحركة');
 expect(api.receipt).not.toHaveBeenCalled();
 expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({receipt_id:null}));
 expect(host.textContent).toContain('تم حفظ الحركة');
});


test('supplier card retains dedicated inventory invoice settlement outside purchase page', async()=>{
  api.entities.mockImplementation(kind=>Promise.resolve({items:kind==='supplier'?[{id:'supplier',name:'مورد المخزون',currency:'SAR'}]:[]}));
  api.inventoryPurchases.mockResolvedValue({items:[],stock:[]});
  await render(<DailyMovements cards context={{session_scope:'test-owner:test-actor',status:'active',permissions:{move:true,reports:false}}}/>);
  await click('▣الموردون');
  await click('مورد المخزون');
  await input(host.querySelector('select'),'inventory-payment');
  expect(host.textContent).toContain('سداد فاتورة مورد');
  expect(host.textContent).not.toContain('حفظ الشراء');
  expect([...host.querySelectorAll('select')][0].value).toBe('supplier');
  await click('العودة للمورد');
  expect(host.textContent).toContain('حفظ الحركة');
});

test('purchase navigation excludes reports and main reports contain read-only inventory',async()=>{
  api.context.mockResolvedValue({session_scope:'test-owner:test-actor',status:'active',permissions:{move:true,reports:true}});
  await render(<OperationalBalances/>);
  expect(host.textContent).toContain('فواتير وكميات المشتريات');
  expect(host.textContent).not.toContain('حفظ الشراء');
  await click('شراء المخزون');
  expect(host.textContent).toContain('حفظ الشراء');
  expect(host.textContent).not.toContain('فواتير وكميات المشتريات');
  expect(host.textContent).not.toContain('إجمالي الكميات المشتراة');
});


test.each([['المصاريف اليومية','operating_expense'],['الإعلانات','ad_account'],['شركات الشحن','courier'],['مناديب المتجر','store_driver'],['سحوبات المالك','owner_withdrawal']])('enabled %s card saves existing outgoing contract without receipt',async(label,kind)=>{
 await render(<DailyMovements cards/>);await cardClick(label);await cardClick('جهة تجريبية');
 const account=[...host.querySelectorAll('select')].find(s=>[...s.options].some(o=>o.value==='bank1'));
 await input(account,'bank1');await input(host.querySelector('input[inputmode="decimal"]'),'25');await click('حفظ الحركة');
 expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({party_type:kind,direction:'outgoing',kind:'payment',amount:'25',receipt_id:null}));
});
test('shipping collection card switches to incoming without a second financial contract',async()=>{
 await render(<DailyMovements cards/>);await cardClick('شركات الشحن');await cardClick('جهة تجريبية');
 await input(host.querySelector('select'),'incoming');const account=[...host.querySelectorAll('select')].find(s=>[...s.options].some(o=>o.value==='bank1'));
 await input(account,'bank1');await input(host.querySelector('input[inputmode="decimal"]'),'30');await click('حفظ الحركة');
 expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({party_type:'courier',direction:'incoming',kind:'collection',amount:'30'}));
});
test('custody inventory navigation settles exact invoice only from selected custody',async()=>{
 api.inventoryPurchaseEntry.mockResolvedValue({id:'inv',obligation_id:'inventory-purchase:inv',supplier_id:'supplier1',supplier_name:'مورد',invoice_number:'INV',invoice_date:'2026-10-07',net:'10',tax:'0',gross:'10',outstanding:'10',settled:'0',lines:[]});
 await render(<DailyMovements cards context={{session_scope:'test-owner:test-actor',status:'active',permissions:{move:true,reports:false}}}/>);
 await cardClick('العهد');await cardClick('جهة تجريبية');await click('سداد فاتورة مخزون من العهدة');
 await input(host.querySelector('select'),'supplier1');await input(host.querySelector('input'),'INV');await click('عرض الفاتورة للسداد');await click('سداد الفاتورة INV');
 const account=[...host.querySelectorAll('select')].find(s=>[...s.options].some(o=>o.value==='employee_custody:employee_custody1'));
 expect(account.disabled).toBe(true);expect(account.value).toBe('employee_custody:employee_custody1');expect([...account.options].some(o=>o.value.includes('bank:'))).toBe(false);
 await click('حفظ السداد');expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({source_account_type:'employee_custody',bank_id:'employee_custody1',allocations:[{obligation_id:'inventory-purchase:inv',amount:'10'}]}));
});

test.each(['prepaid','postpaid'])('advertising %s only offers approved wallet mode',async mode=>{
 const previous=api.entities.getMockImplementation();api.entities.mockImplementation(kind=>kind==='ad_account'?Promise.resolve({items:[{id:'ad_account1',name:'حساب إعلاني',currency:'SAR',funding_mode:mode}]}):previous(kind));
 await render(<DailyMovements cards/>);await cardClick('الإعلانات');await cardClick('حساب إعلاني');
 const operation=host.querySelector('select');expect([...operation.options].some(o=>o.value==='wallet_funding')).toBe(mode==='prepaid');
 if(mode==='prepaid')await input(operation,'wallet_funding');
 const account=[...host.querySelectorAll('select')].find(s=>[...s.options].some(o=>o.value==='bank1'));
 await input(account,'bank1');await input(host.querySelector('input[inputmode="decimal"]'),'30');await click('حفظ الحركة');
 expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({party_type:'ad_account',direction:'outgoing',kind:mode==='prepaid'?'wallet_funding':'payment'}));
});
