import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import InventoryPurchases from './InventoryPurchases';
import {operationalApi as api,requestId} from './api';
jest.mock('./api',()=>({operationalApi:{context:jest.fn(),inventoryCatalog:jest.fn(),inventoryPurchases:jest.fn(),inventoryPurchaseEntry:jest.fn(),entities:jest.fn(),saveInventoryPurchase:jest.fn(),movement:jest.fn()},requestId:jest.fn(),messageFor:()=> 'خطأ في API'}));
let root,host;const scope='inventory-session',context={session_scope:scope,status:'active',permissions:{move:true,reports:true}};
const product={id:'p',kind:'product',name:'منتج أ',image_url:'/test-product.png',unit:'قطعة'};
const invoice={id:'invoice',invoice_number:'INV1',invoice_date:'2026-10-07',supplier_id:'supplier',supplier_name:'مورد تجريبي',lines:[{...product,item_id:'p',quantity:3}],net:'30.00',tax:'4.50',gross:'34.50',obligation_id:'debt',settled:'0.00',outstanding:'34.50'};
const field=name=>[...host.querySelectorAll('.op-field')].find(l=>l.querySelector('span')?.textContent===name).querySelector('input,select');
const input=async(name,value)=>act(async()=>{const e=field(name);Object.getOwnPropertyDescriptor(e.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(e,value);e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input',{bubbles:true}));});
const click=async text=>act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent===text).click());
const render=async (c,mode='purchase')=>act(async()=>root.render(<InventoryPurchases context={c||context} mode={mode}/>));
async function fill(){await input('المورد','supplier');await input('رقم فاتورة المورد','INV1');await act(async()=>host.querySelector('.op-tile').click());await input('الكمية — منتج أ','3');await input('سعر الوحدة قبل الضريبة — منتج أ','10');await input('ضريبة البند — منتج أ','4.50');}
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;localStorage.clear();jest.resetAllMocks();host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);requestId.mockReturnValue('stable-request');api.context.mockResolvedValue(context);api.inventoryCatalog.mockResolvedValue({items:[product]});api.inventoryPurchases.mockResolvedValue({items:[],stock:[]});api.entities.mockImplementation(k=>Promise.resolve({items:[{id:k,name:k}]}));api.saveInventoryPurchase.mockResolvedValue(invoice);api.inventoryPurchaseEntry.mockResolvedValue(invoice);api.movement.mockResolvedValue({id:'movement'});});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
test('MZ2 product picture and integer quantity produce explicit unit price and line tax invoice, no payment',async()=>{await render();await fill();expect(host.querySelector('img').getAttribute('src')).toBe('/test-product.png');expect(host.textContent).toContain('الصافي 30.00 · الضريبة 4.50 · الإجمالي 34.50');await click('حفظ الشراء');expect(api.saveInventoryPurchase).toHaveBeenCalledWith(expect.objectContaining({supplier_id:'supplier',invoice_number:'INV1',lines:[{item_id:'p',kind:'product',quantity:3,unit_price:'10',tax:'4.50'}],request_id:'stable-request',expected_session_scope:scope}));expect(api.movement).not.toHaveBeenCalled();expect(host.querySelector('section').dir).toBe('rtl');});
test('uncertain save locks payload and persists identical retry after remount',async()=>{api.saveInventoryPurchase.mockRejectedValueOnce({response:{status:503}}).mockResolvedValue(invoice);await render();await fill();await click('حفظ الشراء');const sent=api.saveInventoryPurchase.mock.calls[0][0];expect(host.querySelector('fieldset').disabled).toBe(true);await act(async()=>root.unmount());root=createRoot(host);await render();await click('إعادة محاولة الحفظ');expect(api.saveInventoryPurchase.mock.calls[1][0]).toEqual(sent);expect(localStorage.length).toBe(0);});
test('read-only renders invoices/stock but cannot enter or pay and never loads write catalog',async()=>{api.inventoryPurchases.mockResolvedValue({items:[invoice],stock:[{...product,item_id:'p',quantity:3}]});await render({...context,permissions:{reports:true,move:false}},'reports');expect(host.textContent).toContain('INV1');expect(host.textContent).not.toContain('حفظ الشراء');expect(host.textContent).not.toContain('سداد الفاتورة');expect(api.inventoryCatalog).not.toHaveBeenCalled();expect(api.entities).not.toHaveBeenCalled();});
test('write-only saves without acquiring report access',async()=>{await render({...context,permissions:{reports:false,move:true}});await fill();await click('حفظ الشراء');expect(api.saveInventoryPurchase).toHaveBeenCalledTimes(1);expect(api.inventoryPurchases).not.toHaveBeenCalled();expect(host.textContent).not.toContain('المشتريات المسجلة');});
test('partial payment allocates only chosen invoice using selected cash account',async()=>{api.inventoryPurchases.mockResolvedValue({items:[invoice],stock:[]});await render(context,'payment');await click('سداد الفاتورة INV1');await input('مصدر السداد','cash:cash');await input('مبلغ السداد','12');await click('حفظ السداد');expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({kind:'payment',direction:'outgoing',party_type:'supplier',party_id:'supplier',source_account_type:'cash',bank_id:'cash',amount:'12',allocations:[{obligation_id:'debt',amount:'12'}],receipt_id:null}));});
test('invalid fractional quantity blocks POST and preserves form',async()=>{await render();await fill();await input('الكمية — منتج أ','1.5');await click('حفظ الشراء');expect(api.saveInventoryPurchase).not.toHaveBeenCalled();expect(host.textContent).toContain('عددًا صحيحًا موجبًا');expect(field('رقم فاتورة المورد').value).toBe('INV1');});
test('loading prevents save and rapid click has one request',async()=>{let done;api.saveInventoryPurchase.mockImplementation(()=>new Promise(resolve=>{done=resolve;}));await render();await fill();await act(async()=>{const button=[...host.querySelectorAll('button')].find(b=>b.textContent==='حفظ الشراء');button.click();button.click();});expect(api.saveInventoryPurchase).toHaveBeenCalledTimes(1);expect(host.querySelector('fieldset').disabled).toBe(true);await act(async()=>done(invoice));});
test('definitive rejected payload permits correction; no permission renders no entry or report',async()=>{api.saveInventoryPurchase.mockRejectedValue({response:{status:422}});await render();await fill();await click('حفظ الشراء');expect(host.querySelector('fieldset').disabled).toBe(false);expect(localStorage.length).toBe(0);await render({...context,permissions:{}});expect(host.textContent).toContain('لا تتوفر صلاحية');expect(host.textContent).not.toContain('INV1');});

test('write-only known invoice lookup permits allocated later payment without list/read access',async()=>{await render({...context,permissions:{move:true,reports:false}},'payment');await input('مورد الفاتورة للسداد','supplier');await input('رقم الفاتورة للسداد','INV1');await click('عرض الفاتورة للسداد');expect(api.inventoryPurchaseEntry).toHaveBeenCalledWith('supplier','INV1');await click('سداد الفاتورة INV1');await input('مصدر السداد','employee_custody:employee_custody');await input('مبلغ السداد','5');await click('حفظ السداد');expect(api.movement).toHaveBeenCalledWith(expect.objectContaining({source_account_type:'employee_custody',bank_id:'employee_custody',allocations:[{obligation_id:'debt',amount:'5'}]}));expect(api.inventoryPurchases).not.toHaveBeenCalled();});
test('component identity remains distinct and exact quantity goes to receipt',async()=>{api.inventoryCatalog.mockResolvedValue({items:[{...product,kind:'component'}]});await render();await click('مكوّن');await fill();await click('حفظ الشراء');expect(api.saveInventoryPurchase.mock.calls[0][0].lines[0]).toEqual(expect.objectContaining({kind:'component',quantity:3}));});
test('storage failure prevents financial request',async()=>{await render();await fill();const storage=jest.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw Error('storage unavailable');});await click('حفظ الشراء');expect(api.saveInventoryPurchase).not.toHaveBeenCalled();storage.mockRestore();});

test('assigned default bank is selected when opening invoice payment',async()=>{api.inventoryPurchases.mockResolvedValue({items:[invoice],stock:[]});await render({...context,operational_banks:{default_bank_id:'bank'}},'payment');await click('سداد الفاتورة INV1');expect(field('مصدر السداد').value).toBe('bank:bank');});
test('initial catalog failure is recoverable without remount and prevents entry until reload',async()=>{api.inventoryCatalog.mockRejectedValueOnce(new Error('offline')).mockResolvedValue({items:[product]});await render();expect(host.querySelector('fieldset').disabled).toBe(true);await click('إعادة تحميل بيانات المشتريات');expect(host.querySelector('fieldset').disabled).toBe(false);expect(host.querySelector('.op-tile').textContent).toContain('منتج أ');expect(api.inventoryCatalog).toHaveBeenCalledTimes(2);});


test('purchase is a single task: no history, quantity report, lookup, payment or routine refresh',async()=>{
  api.inventoryPurchases.mockResolvedValue({items:[invoice],stock:[{...product,item_id:'p',quantity:3}]});
  await render();
  expect(api.inventoryPurchases).not.toHaveBeenCalled();
  expect(host.textContent).not.toMatch(/المشتريات المسجلة|إجمالي الكميات المشتراة|سداد فاتورة معروفة|تحديث المشتريات/);
  expect([...host.querySelectorAll('.op-primary')].map(b=>b.textContent)).toEqual(['حفظ الشراء']);
  expect(field('تاريخ الفاتورة').value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  expect(api.entities.mock.calls.map(c=>c[0])).toEqual(['supplier']);
});
test('reports stay read-only even with both permissions',async()=>{
  api.inventoryPurchases.mockResolvedValue({items:[invoice],stock:[{...product,item_id:'p',quantity:3}]});
  await render(context,'reports');
  expect(host.textContent).toContain('إجمالي الكميات المشتراة');
  expect(host.textContent).toContain('INV1');
  expect(host.textContent).not.toMatch(/حفظ الشراء|حفظ السداد|سداد الفاتورة/);
  expect(api.inventoryCatalog).not.toHaveBeenCalled();
  expect(api.entities).not.toHaveBeenCalled();
});
test('type toggle and search filter the selection cards without losing selected lines',async()=>{
  api.inventoryCatalog.mockResolvedValue({items:[product,{...product,id:'c',kind:'component',name:'مكوّن ب',sku:'COMP'}]});
  await render();await fill();await click('مكوّن');await input('بحث بالاسم أو الرمز','COMP');
  expect(host.querySelectorAll('.op-tile')).toHaveLength(1);
  expect(host.querySelector('.op-tile').textContent).toContain('مكوّن ب');
  expect(field('الكمية — منتج أ').value).toBe('3');
});

test('reports cannot retry a persisted financial command and never discard it',async()=>{
  const command={type:'payment',body:{request_id:'pending-payment',expected_session_scope:scope}};
  localStorage.setItem(`mezan.operational.inventory.v1:${scope}`,JSON.stringify(command));
  await render(context,'reports');
  expect(host.textContent).toContain('تابع العملية من الموردين');
  expect(host.textContent).not.toContain('إعادة محاولة الحفظ');
  expect(api.movement).not.toHaveBeenCalled();
  expect(localStorage.length).toBe(1);
});

test('piece unit is Arabic display only and does not alter posted identity',async()=>{
  api.inventoryCatalog.mockResolvedValue({items:[{...product,unit:'piece'}]});
  await render();await fill();
  expect(host.textContent).toContain('قطعة');expect(host.textContent).not.toContain('piece');
  await click('حفظ الشراء');
  expect(api.saveInventoryPurchase.mock.calls[0][0].lines[0]).toEqual({item_id:'p',kind:'product',quantity:3,unit_price:'10',tax:'4.50'});
  api.inventoryPurchases.mockResolvedValue({items:[{...invoice,lines:[{...invoice.lines[0],unit:'piece'}]}],stock:[{...product,item_id:'p',unit:'piece',quantity:3}]});
  await render(context,'reports');
  expect(host.textContent).not.toContain('piece');
  expect(host.textContent).toContain('3 قطعة');
});
test('changing lookup clears the prior invoice payment selection',async()=>{
  await render({...context,permissions:{move:true,reports:false}},'payment');
  await input('مورد الفاتورة للسداد','supplier');await input('رقم الفاتورة للسداد','INV1');await click('عرض الفاتورة للسداد');
  await click('سداد الفاتورة INV1');expect(host.textContent).toContain('حفظ السداد');
  await input('رقم الفاتورة للسداد','INV2');
  expect(host.textContent).not.toContain('حفظ السداد');expect(host.textContent).not.toContain('سداد الفاتورة INV1');
  expect(api.movement).not.toHaveBeenCalled();
});
