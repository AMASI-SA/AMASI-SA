import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import InventoryPurchases from './InventoryPurchases';
import {inventoryIdentity} from './inventoryIdentity';
import {inventoryPersonalizations} from './inventoryPersonalizations';
import {purchaseConfigurationPayload} from './PurchaseConfiguration';
import {operationalApi as api,requestId} from './api';
jest.mock('./api',()=>({operationalApi:{context:jest.fn(),inventoryCatalog:jest.fn(),inventoryPurchases:jest.fn(),inventoryPurchaseEntry:jest.fn(),entities:jest.fn(),saveInventoryPurchase:jest.fn(),movement:jest.fn()},requestId:jest.fn(),messageFor:()=> 'خطأ في API'}));
let root,host;const scope='inventory-session',context={session_scope:scope,status:'active',permissions:{move:true,reports:true}};
const fields=[{id:'name',name:'الاسم',type:'text',required:true}];
const product={customization_fields:fields,id:'p',kind:'product',name:'منتج أ',image_url:'/test-product.png',unit:'قطعة'};
const invoice={id:'invoice',invoice_number:'INV1',invoice_date:'2026-10-07',supplier_id:'supplier',supplier_name:'مورد تجريبي',lines:[{...product,item_id:'p',quantity:3}],net:'30.00',tax:'4.50',gross:'34.50',obligation_id:'debt',settled:'0.00',outstanding:'34.50'};
const field=name=>[...host.querySelectorAll('.op-field')].find(l=>l.querySelector('span')?.textContent===name).querySelector('input,select,textarea');
const input=async(name,value)=>act(async()=>{const e=field(name);Object.getOwnPropertyDescriptor(e.tagName==='SELECT'?HTMLSelectElement.prototype:e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(e,value);e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input',{bubbles:true}));});
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

test('same product silver ten and gold twenty stay separate through save, retry and report readback in RTL',async()=>{
  const silver={...product,variant_id:'silver',variant_name:'فضي',name:'منتج أ — فضي'};
  const gold={...product,variant_id:'gold',variant_name:'ذهبي',name:'منتج أ — ذهبي'};
  api.inventoryCatalog.mockResolvedValue({items:[silver,gold]});
  await render();await input('المورد','supplier');await input('رقم فاتورة المورد','COLORS');
  await act(async()=>host.querySelectorAll('.op-tile')[0].click());
  expect(host.querySelectorAll('.op-tile')[0].disabled).toBe(true);
  expect(host.querySelectorAll('.op-tile')[1].disabled).toBe(false);
  await act(async()=>host.querySelectorAll('.op-tile')[1].click());
  await input('الكمية — منتج أ — فضي','10');await input('سعر الوحدة قبل الضريبة — منتج أ — فضي','5');
  await input('الكمية — منتج أ — ذهبي','20');await input('سعر الوحدة قبل الضريبة — منتج أ — ذهبي','6');
  api.saveInventoryPurchase.mockRejectedValueOnce({response:{status:503}});
  await click('حفظ الشراء');
  const payload=api.saveInventoryPurchase.mock.calls[0][0];
  expect(payload.lines).toEqual([
    {item_id:'p',kind:'product',variant_id:'silver',quantity:10,unit_price:'5',tax:'0'},
    {item_id:'p',kind:'product',variant_id:'gold',quantity:20,unit_price:'6',tax:'0'}
  ]);
  await act(async()=>root.unmount());root=createRoot(host);await render();await click('إعادة محاولة الحفظ');
  expect(api.saveInventoryPurchase.mock.calls[1][0]).toEqual(payload);
  const lines=[{...silver,item_id:'p',quantity:10,returned_quantity:2,remaining_quantity:8},{...gold,item_id:'p',quantity:20,returned_quantity:0,remaining_quantity:20}];
  api.inventoryPurchases.mockResolvedValue({items:[{...invoice,invoice_number:'COLORS',lines}],stock:lines});
  await render(context,'reports');
  expect(host.querySelector('section').dir).toBe('rtl');
  expect(host.textContent).toContain('منتج أ — فضي · المشتراة 10 قطعة · المرتجعة 2 · المتبقية 8');
  expect(host.textContent).toContain('منتج أ — ذهبي · المشتراة 20 قطعة · المرتجعة 0 · المتبقية 20');
});

test('inventory identity cannot collide across delimiter-containing product and variant IDs',()=>{
  expect(inventoryIdentity({kind:'product',id:'p:x',variant_id:'y'})).not.toBe(inventoryIdentity({kind:'product',item_id:'p',variant_id:'x:y'}));
  expect(inventoryIdentity({kind:'product',id:'p'})).not.toBe(inventoryIdentity({kind:'product',id:'p',variant_id:'null'}));
});

const gold={...product,variant_id:'gold',name:'منتج أ — ذهبي'};
const silver={...product,variant_id:'silver',name:'منتج أ — فضي'};
async function addName(line,row,name,quantity){await click(`إضافة تخصيص — ${line.name}`);await input(`الاسم * ${row} — ${line.name}`,name);await input(`كمية التخصيص ${row} — ${line.name}`,quantity);}
test('names distribute original gold10/silver5 without inflating quantity or price and retry stays frozen',async()=>{
  api.inventoryCatalog.mockResolvedValue({items:[gold,silver]});
  await render();await input('المورد','supplier');await input('رقم فاتورة المورد','NAMES');
  await act(async()=>host.querySelectorAll('.op-tile')[0].click());
  await act(async()=>host.querySelectorAll('.op-tile')[1].click());
  await input(`الكمية — ${gold.name}`,'10');await input(`سعر الوحدة قبل الضريبة — ${gold.name}`,'10');
  await input(`الكمية — ${silver.name}`,'5');await input(`سعر الوحدة قبل الضريبة — ${silver.name}`,'10');
  await addName(gold,1,' عبير ','4');await addName(silver,1,'عبير','1');await addName(silver,2,'روان','3');
  expect(host.textContent).toContain('الكمية المشتراة 10 · المخصص 4 · غير المخصص 6');
  expect(host.textContent).toContain('الكمية المشتراة 5 · المخصص 4 · غير المخصص 1');
  expect(host.textContent).toContain('الإجمالي 150.00');
  api.saveInventoryPurchase.mockRejectedValueOnce({response:{status:503}});
  await click('حفظ الشراء');const body=api.saveInventoryPurchase.mock.calls[0][0];
  expect(body.lines).toEqual([
    {item_id:'p',kind:'product',variant_id:'gold',quantity:10,unit_price:'10',tax:'0',personalizations:[{values:[{option_id:'name',value:'عبير'}],quantity:4}]},
    {item_id:'p',kind:'product',variant_id:'silver',quantity:5,unit_price:'10',tax:'0',personalizations:[{values:[{option_id:'name',value:'عبير'}],quantity:1},{values:[{option_id:'name',value:'روان'}],quantity:3}]}
  ]);
  expect(host.querySelector('fieldset').disabled).toBe(true);
  await act(async()=>root.unmount());root=createRoot(host);await render();await click('إعادة محاولة الحفظ');
  expect(api.saveInventoryPurchase.mock.calls[1][0]).toEqual(body);
});
test('over-allocation and duplicate normalized name fail before request while removal permits save',async()=>{
  await render();await fill();await addName(product,1,'عبير','4');await click('حفظ الشراء');
  expect(api.saveInventoryPurchase).not.toHaveBeenCalled();expect(host.textContent).toContain('يتجاوز كمية');
  await input('كمية التخصيص 1 — منتج أ','1');await addName(product,2,'  عبير  ','1');await click('حفظ الشراء');
  expect(api.saveInventoryPurchase).not.toHaveBeenCalled();expect(host.textContent).toContain('التخصيص مكرر');
  await click('إزالة التخصيص 2 — منتج أ');await click('حفظ الشراء');
  expect(api.saveInventoryPurchase.mock.calls[0][0].lines[0].personalizations).toEqual([{values:[{option_id:'name',value:'عبير'}],quantity:1}]);
});
test('reports preserve original name allocations after unnamed returns, not remaining named stock',async()=>{
  api.inventoryPurchases.mockResolvedValue({items:[{...invoice,lines:[{...gold,item_id:'p',quantity:10,returned_quantity:2,remaining_quantity:8,personalizations:[{name:'عبير',quantity:4}],unallocated_quantity:6}]}],stock:[]});
  await render(context,'reports');expect(host.textContent).toContain('توزيع الكمية المشتراة الأصلي حسب الخيارات');
  expect(host.textContent).toContain('عبير · 4 قطعة');expect(host.textContent).toContain('بدون تخصيص · 6 قطعة');
  expect(host.textContent).toContain('المتبقية 8');expect(host.textContent).not.toContain('إضافة تخصيص');
});
test('components have no personal-name controls',async()=>{
  api.inventoryCatalog.mockResolvedValue({items:[{...product,kind:'component'}]});await render();await click('مكوّن');await fill();
  expect(host.textContent).not.toContain('إضافة تخصيص');await click('حفظ الشراء');
  expect(api.saveInventoryPurchase.mock.calls[0][0].lines[0]).not.toHaveProperty('personalizations');
});
test.each([
  [{name:' ',quantity:1}], [{name:'x'.repeat(101),quantity:1}], [{name:'x',quantity:0}], [{name:'x',quantity:1.5}],
  [{name:'Cafe\u0301',quantity:1},{name:'CAFÉ',quantity:1}],
  Array.from({length:101},(_,i)=>({name:String(i),quantity:1}))
].map(rows=>[rows]))('invalid name distribution rejects before persistence: %#',rows=>{
  expect(()=>inventoryPersonalizations({kind:'product',customization_fields:fields,quantity:200,personalizations:rows.map(r=>({quantity:r.quantity,values:[{option_id:'name',value:r.name}]}))})).toThrow();
});
test('legacy empty distributions remain absent and canonical name whitespace is stable',()=>{
  expect(inventoryPersonalizations({kind:'product',quantity:10,personalizations:[]})).toEqual({});
  expect(inventoryPersonalizations({kind:'product',customization_fields:fields,quantity:10,personalizations:[{values:[{option_id:'name',value:'  عبير   خالد  '}],quantity:'4'}]})).toEqual({personalizations:[{values:[{option_id:'name',value:'عبير خالد'}],quantity:4}]});
});

test('product-defined name letter and multiline note share one quantity allocation',async()=>{
  const definitions=[...fields,{id:'letter',name:'الحرف',type:'string',required:true},{id:'note',name:'ملاحظة الطباعة',type:'textarea',required:false}];
  api.inventoryCatalog.mockResolvedValue({items:[{...product,customization_fields:definitions}]});
  await render();await fill();await click('إضافة تخصيص — منتج أ');
  await input('الاسم * 1 — منتج أ','عبير');await click('حفظ الشراء');
  expect(api.saveInventoryPurchase).not.toHaveBeenCalled();expect(host.textContent).toContain('أكمل الحرف');
  await input('الحرف * 1 — منتج أ','ع');await input('ملاحظة الطباعة 1 — منتج أ','نقش فضي');
  await input('كمية التخصيص 1 — منتج أ','2');await click('حفظ الشراء');
  expect(api.saveInventoryPurchase.mock.calls[0][0].lines[0].personalizations).toEqual([{quantity:2,values:[{option_id:'name',value:'عبير'},{option_id:'letter',value:'ع'},{option_id:'note',value:'نقش فضي'}]}]);
  expect(api.saveInventoryPurchase.mock.calls[0][0].lines[0].quantity).toBe(3);
});

test('no generic name editor is invented for products without customization definitions',async()=>{
  api.inventoryCatalog.mockResolvedValue({items:[{...product,customization_fields:[]}]});
  await render();await fill();expect(host.textContent).not.toContain('إضافة تخصيص');
  await click('حفظ الشراء');expect(api.saveInventoryPurchase.mock.calls[0][0].lines[0]).not.toHaveProperty('personalizations');
});

test('unknown options reject, while the same name with a different letter is distinct',()=>{
  const line={kind:'product',quantity:2,customization_fields:[...fields,{id:'letter',name:'الحرف',type:'text',required:true}]};
  const row=letter=>({quantity:1,values:[{option_id:'name',value:'عبير'},{option_id:'letter',value:letter}]});
  expect(inventoryPersonalizations({...line,personalizations:[row('ع'),row('ر')]}).personalizations).toHaveLength(2);
  expect(()=>inventoryPersonalizations({...line,personalizations:[{quantity:1,values:[{option_id:'foreign',value:'x'}]}]})).toThrow();
});

test('canonical saved labels render in reports without substituting technical option IDs',async()=>{
  api.inventoryPurchases.mockResolvedValue({items:[{...invoice,lines:[{...product,quantity:3,personalizations:[{quantity:2,values:[{option_id:'private-id',option_name:'الحرف',type:'text',value:'ع'}]}],unallocated_quantity:1}]}],stock:[]});
  await render(context,'reports');expect(host.textContent).toContain('الحرف: ع · 2 قطعة');expect(host.textContent).not.toContain('private-id');
});

test('canonical raw gold10 and ready Abeer100 stay separate purchase lines with MZ2 location, not actual stock',async()=>{
 const options=[{id:'color',name:'اللون',type:'select',required:true,values:[{id:'gold',name:'ذهبي'},{id:'silver',name:'فضي'}]},{id:'name',name:'الاسم',type:'text',required:true}];
 api.inventoryCatalog.mockResolvedValue({items:[],products:[{id:'p',kind:'product',name:'سلسال',unit:'piece',options}],locations:[{id:'loc',name:'الرف الأول'}]});
 let n=0;requestId.mockImplementation(()=>`req-${++n}`);
 await render();await input('المورد','supplier');await input('رقم فاتورة المورد','CONFIG1');
 await act(async()=>host.querySelector('.op-tile').click());
 await input('موقع تسجيل الشراء 1 — سلسال','loc');await input('اللون 1 — سلسال','gold');
 await input('الكمية — سلسال','10');await input('سعر الوحدة قبل الضريبة — سلسال','15');
 await act(async()=>host.querySelector('.op-tile').click());
 await input('حالة المنتج 2 — سلسال','ready');await input('موقع تسجيل الشراء 2 — سلسال','loc');await input('اللون 2 — سلسال','silver');await input('الاسم * 2 — سلسال','عبير');
 const quantityInputs=[...host.querySelectorAll('.op-field')].filter(l=>l.querySelector('span')?.textContent==='الكمية — سلسال').map(l=>l.querySelector('input'));
 const priceInputs=[...host.querySelectorAll('.op-field')].filter(l=>l.querySelector('span')?.textContent==='سعر الوحدة قبل الضريبة — سلسال').map(l=>l.querySelector('input'));
 await act(async()=>{for(const [e,v] of [[quantityInputs[1],'100'],[priceInputs[1],'2']]){Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,v);e.dispatchEvent(new Event('input',{bubbles:true}));}});
 await click('حفظ الشراء');const sent=api.saveInventoryPurchase.mock.calls[0][0];expect(sent.lines).toHaveLength(2);
 expect(sent.lines[0]).toEqual(expect.objectContaining({quantity:10,unit_price:'15',location_id:'loc',purchase_configuration:{state:'raw',selections:[{option_id:'color',value_id:'gold'}],inputs:[]}}));
 expect(sent.lines[1]).toEqual(expect.objectContaining({quantity:100,location_id:'loc',purchase_configuration:{state:'ready',selections:[{option_id:'color',value_id:'silver'}],inputs:[{option_id:'name',value:'عبير'}]}}));
 expect(api.movement).not.toHaveBeenCalled();
});
test('configuration requires a location and purchase reports are explicit and searchable',async()=>{
 expect(()=>purchaseConfigurationPayload({purchase_configuration:{state:'raw',selections:[],inputs:[]}})).toThrow('اختر موقع');
 api.inventoryPurchases.mockResolvedValue({items:[invoice,{...invoice,id:'second',invoice_number:'SECOND',supplier_name:'مورد آخر'}],stock:[]});
 await render(context,'reports');expect(host.textContent).toContain('المخزون الفعلي غير مثبت');
 await input('بحث في فواتير المشتريات','SECOND');expect(host.textContent).not.toContain('مورد تجريبي');expect(host.textContent).toContain('مورد آخر');
 await input('تاريخ المشتريات','2026-10-09');expect(host.textContent).not.toContain('مورد آخر');
});
test('configured line identity preserves exact supplier return target',()=>{
 const a={kind:'product',item_id:'p',purchase_line_key:'raw-gold'},b={...a,purchase_line_key:'ready-silver'};
 expect(inventoryIdentity(a)).not.toBe(inventoryIdentity(b));
});
