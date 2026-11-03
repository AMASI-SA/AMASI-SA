import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import DailyMovements from './DailyMovements';
import ExternalOperationControls, {externalOperations, externalOperationPatch, ExternalBalanceReceipt} from './ExternalOperationControls';
import {operationalApi as api, requestId} from './api';
import {pendingMovementKey} from './pendingMovementStorage';

jest.mock('./api',()=>({
  operationalApi:{context:jest.fn(),entities:jest.fn(),obligations:jest.fn(),movement:jest.fn(),inventoryCatalog:jest.fn(),inventoryPurchases:jest.fn()},
  entityKinds:[['external_person','جهات خارجية'],['supplier','موردين']],
  requestId:jest.fn(),messageFor:()=> 'تعذر إتمام العملية',
}));
const SCOPE='test-owner:test-actor';
let host,root;
const render=async node=>act(async()=>root.render(node));
const set=async(el,value)=>act(async()=>{
  Object.getOwnPropertyDescriptor(el.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(el,value);
  el.dispatchEvent(new Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));
});
const click=async text=>act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent===text).click());
const cardClick=async text=>act(async()=>{
  const matches=[...host.querySelectorAll('button.op-tile')].filter(b=>b.querySelector('strong')?.textContent===text);
  expect(matches).toHaveLength(1);
  matches[0].click();
});
const choose=()=>host.querySelector('[aria-label="عملية الجهة الخارجية"]');
const labelSelect=text=>[...host.querySelectorAll('label')].find(l=>l.textContent.startsWith(text))?.querySelector('select');
const receipt=operation=>({operation,currency:'SAR',account_id:'bank1',account_kind:'bank',
  before:{receivable_sar:'0.00',payable_sar:'0.00',account_balance_sar:'1000.00'},
  after:{receivable_sar:'0.00',payable_sar:'100.00',account_balance_sar:'1100.00'}});
beforeEach(()=>{
  global.IS_REACT_ACT_ENVIRONMENT=true;
  host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
  jest.resetAllMocks();localStorage.clear();
  requestId.mockImplementation(()=> 'unique-request');
  api.context.mockResolvedValue({session_scope:SCOPE,status:'active',permissions:{view:true,move:true,reports:true}});
  api.entities.mockImplementation(kind=>Promise.resolve({items:[{id:kind==='external_person'?'person':kind+'1',name:kind==='external_person'?'الجهة التجريبية':kind+' حساب',currency:'SAR',kind}]}));
  api.obligations.mockResolvedValue({items:[]});
  api.inventoryCatalog.mockResolvedValue({items:[]});api.inventoryPurchases.mockResolvedValue({items:[],stock:[]});
  api.movement.mockResolvedValue({});
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});

async function prepare(action, cards=true){
  await render(<DailyMovements cards={cards} storageScope={SCOPE}/>);
  if(cards){await cardClick('الجهات الخارجية');await cardClick('الجهة التجريبية');}
  else {await set(host.querySelector('select'),'external_person');}
  if(action)await set(choose(),action);
  if(!cards)await set(labelSelect('الجهة'),'person');
  const account=cards?labelSelect('البنك'):labelSelect(action==='borrow'||action==='collect_lending'?'الاستلام في':'الدفع من');
  await set(account,cards?'bank1':'bank:bank1');
  await set(host.querySelector('input[inputmode="decimal"]'),'100');
}

test.each(externalOperations)('explicit $value has fixed direction/type and is submitted once',async action=>{
  await prepare(action.value);
  expect(labelSelect('اتجاه الحركة').disabled).toBe(true);
  expect(host.textContent).toContain(action.direction==='incoming'?'الاستلام في':'الدفع من');
  await click('حفظ الحركة');
  expect(api.movement).toHaveBeenCalledTimes(1);
  expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({external_operation:action.value,direction:action.direction,kind:action.kind,amount:'100',party_type:'external_person',party_id:'person',bank_id:'bank1',allocations:[]}));
});

test('a new external movement requires an explicit purpose',async()=>{
  await prepare(null);await click('حفظ الحركة');
  expect(host.textContent).toContain('حدد هل العملية إنشاء سلفة أو سداد مستحق');
  expect(api.movement).not.toHaveBeenCalled();
});

test('generic movement is explicit and does not send a fake loan operation',async()=>{
  await prepare('general');await click('حفظ الحركة');
  expect(api.movement).toHaveBeenCalledTimes(1);
  expect(api.movement.mock.calls[0][0]).not.toHaveProperty('external_operation');
});

test('advanced entry also submits the explicit loan and prevents a direction override',async()=>{
  await prepare('lend',false);
  expect([...host.querySelectorAll('button')].find(b=>b.textContent==='وارد').disabled).toBe(true);
  expect(labelSelect('نوع الحركة').disabled).toBe(true);
  await click('حفظ الحركة');
  expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({external_operation:'lend',direction:'outgoing',kind:'payment',source_account_type:'bank'}));
});

test('confirmed server result shows real before/after values without creating another request',async()=>{
  api.movement.mockResolvedValue({external_balance_snapshot:receipt('borrow')});
  await prepare('borrow');await click('حفظ الحركة');
  const table=host.querySelector('[aria-label="أثر حركة الجهة الخارجية"]');
  expect(table.textContent).toContain('للجهة علينا');expect(table.textContent).toContain('1100.00');
  expect(table.textContent).toContain('1000.00');expect(table.textContent).toContain('100.00');
  expect(api.movement).toHaveBeenCalledTimes(1);
});

test('write-only acknowledgement does not invent balance figures',async()=>{
  api.movement.mockResolvedValue({id:'recorded'});
  await prepare('borrow');await click('حفظ الحركة');
  expect(host.querySelector('[aria-label="أثر حركة الجهة الخارجية"]')).toBeNull();
  expect(host.textContent).toContain('تم حفظ الحركة');
});

test('ambiguous retry retains the operation and exact request after a reload',async()=>{
  api.movement.mockRejectedValueOnce(new Error('network')).mockResolvedValue({external_balance_snapshot:receipt('borrow')});
  await prepare('borrow');await click('حفظ الحركة');const first=api.movement.mock.calls[0][0];
  expect(choose().disabled).toBe(true);
  await act(async()=>root.unmount());root=createRoot(host);
  await render(<DailyMovements cards storageScope={SCOPE}/>);
  await click('إعادة محاولة الحركة');expect(api.movement.mock.calls[1][0]).toEqual(first);
  expect(localStorage.getItem(pendingMovementKey(SCOPE))).toBeNull();
});

test('old pending payload is retried unchanged without guessing loan intent',async()=>{
  const old={request_id:'old-request',expected_session_scope:SCOPE,party_type:'external_person',party_id:'person',bank_id:'bank1',source_account_type:'bank',direction:'incoming',kind:'collection',currency:'SAR',amount:'50',allocations:[],reference:'old'};
  localStorage.setItem(pendingMovementKey(SCOPE),JSON.stringify({version:1,scope:SCOPE,payload:old}));
  await render(<DailyMovements cards storageScope={SCOPE}/>);await click('إعادة محاولة الحركة');
  expect(api.movement).toHaveBeenCalledWith(old);
  expect(api.movement.mock.calls[0][0]).not.toHaveProperty('external_operation');
});

test('missing receipt amounts are not displayed as zero',async()=>{
  await render(<ExternalBalanceReceipt snapshot={{...receipt('lend'),before:{},after:{}}}/>);
  expect(host.textContent).toContain('غير متوفر');expect(host.textContent).not.toContain('0.00');
});

test.each(externalOperations)('operation patch $value does not change financial amounts/accounts',action=>{
  expect(externalOperationPatch(action.value)).toEqual({external_operation:action.value,direction:action.direction,kind:action.kind});
});
