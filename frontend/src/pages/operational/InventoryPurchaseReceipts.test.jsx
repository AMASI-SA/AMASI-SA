import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import InventoryPurchaseReceipts from './InventoryPurchaseReceipts';
import {operationalApi as api,requestId} from './api';
import {purchaseToday} from './inventoryPurchaseWorkflow';
import {persistInventoryCommand,readInventoryCommands} from './inventoryCommandStorage';

jest.mock('./api',()=>({operationalApi:{
  inventoryPurchases:jest.fn(),inventoryPurchaseReceivingContext:jest.fn(),
  previewInventoryPurchaseReceipt:jest.fn(),saveInventoryPurchaseReceipt:jest.fn(),
  movement:jest.fn(),saveInventoryPurchase:jest.fn(),
},requestId:jest.fn(),messageFor:error=>error.response?.data?.detail?.message||error.message||'تعذر إتمام الاستلام'}));

const scope='s'.repeat(64),context={session_scope:scope,can_manage_inventory_opening:true,status:'active',permissions:{move:true,reports:true}};
const key='mezan.inventory.purchase.receiving.v1:'+scope;
const baseLine={line_id:'fur-line',item_id:'fur',kind:'product',name:'فروة',unit:'piece',quantity:5,received_quantity:0,pending_receipt_quantity:5,returned_quantity:0,
  purchase_configuration:{state:'raw',selections:[{option_id:'size',value_id:'two'}],inputs:[],deferred_option_ids:['emb']},
  purchase_option_labels:[{option_id:'size',name:'المقاس',value:'سنتان'}],deferred_option_labels:[{option_id:'emb',name:'تطريز الاسم'}]};
const base={id:'invoice-one',invoice_number:'INV-ONE',supplier_id:'supplier',supplier_name:'مورد الاختبار',invoice_date:'2026-01-01',gross:'100.00',settled:'0.00',outstanding:'100.00',
  receipt_tracking_version:1,receipt_status:'not_received',can_receive:true,receipt_history:[],lines:[baseLine,{line_id:'necklace-line',item_id:'necklace',kind:'product',name:'سلسال',unit:'piece',quantity:3,received_quantity:0,pending_receipt_quantity:3,returned_quantity:0}]};
const receipt={id:'receipt-original',receipt_date:'2026-01-02',note:'دفعة سابقة',lines:[{line_id:'fur-line',name:'فروة',quantity:2,unit:'piece',location_name:'A-1'}]};
const partial={...base,id:'invoice-partial',invoice_number:'INV-PARTIAL',receipt_status:'partial',receipt_history:[receipt],lines:[{...baseLine,received_quantity:2,pending_receipt_quantity:3}]};
const paid={...base,id:'invoice-paid',invoice_number:'INV-PAID',settled:'100.00',outstanding:'0.00',lines:[{...baseLine}]};
const received={...base,id:'invoice-received',invoice_number:'INV-RECEIVED',receipt_status:'received',can_receive:false,lines:[{...baseLine,received_quantity:5,pending_receipt_quantity:0}]};
const closed={...base,id:'invoice-closed',invoice_number:'INV-CLOSED',receipt_status:'closed',can_receive:false,receipt_history:[receipt],lines:[{...baseLine,received_quantity:2,pending_receipt_quantity:0,returned_quantity:3}]};
const historical={...base,id:'invoice-old',invoice_number:'INV-OLD',receipt_tracking_version:undefined,receipt_status:'unproven',can_receive:false,lines:[{...baseLine}]};
const storageLocations=[{id:'shelf-a',name:'A-1',warehouse_name:'الرئيسي'},{id:'shelf-b',name:'B-2',warehouse_name:'الرئيسي'}];
let host,root,invoices,writerDisabled,operationalActive,sequence;
const clone=value=>JSON.parse(JSON.stringify(value));
const row=number=>[...host.querySelectorAll('.mz-receipt-list > article')].find(article=>article.querySelector('h3').textContent.startsWith(number+' ·'));
const button=label=>[...host.querySelectorAll('button')].find(element=>element.textContent===label);
const field=label=>[...host.querySelectorAll('.op-field')].find(element=>element.querySelector('span')?.textContent===label)?.querySelector('input,select,textarea');
const render=async(props={})=>act(async()=>root.render(<InventoryPurchaseReceipts context={context} {...props}/>));
const click=async label=>act(async()=>{const control=button(label);if(!control)throw Error('Missing button: '+label);control.click();});
const open=async number=>act(async()=>row(number).querySelector('button').click());
const input=async(label,value)=>act(async()=>{
  const control=field(label);if(!control)throw Error('Missing field: '+label);
  const prototype=control.tagName==='SELECT'?HTMLSelectElement.prototype:control.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype,'value').set.call(control,value);
  control.dispatchEvent(new Event(control.tagName==='SELECT'?'change':'input',{bubbles:true}));
});
async function choosePartial(){await open('INV-ONE');await input('المستلم الآن — فروة','2');await input('موقع الاستلام — فروة','shelf-a');}
function applyReceipt(invoiceId,body){
  const invoice=invoices.find(item=>item.id===invoiceId);
  invoice.lines=invoice.lines.map(line=>{const got=body.lines.find(selected=>selected.line_id===line.line_id)?.quantity||0;return {...line,received_quantity:line.received_quantity+got,pending_receipt_quantity:line.pending_receipt_quantity-got};});
  invoice.can_receive=invoice.lines.some(line=>line.pending_receipt_quantity>0);
  invoice.receipt_status=invoice.can_receive?'partial':'received';
  invoice.receipt_history=[...invoice.receipt_history,{id:'receipt-'+body.request_id,receipt_date:body.receipt_date,note:body.note,lines:body.lines.map(selected=>({line_id:selected.line_id,name:invoice.lines.find(line=>line.line_id===selected.line_id).name,unit:'piece',quantity:selected.quantity,location_name:storageLocations.find(location=>location.id===selected.location_id).name}))}];
  return {status:'approved',id:'batch-'+body.request_id,invoice:clone(invoice)};
}
beforeEach(()=>{
  global.IS_REACT_ACT_ENVIRONMENT=true;localStorage.clear();jest.resetAllMocks();writerDisabled=false;operationalActive=true;sequence=0;
  invoices=clone([base,partial,paid,received,closed,historical]);requestId.mockImplementation(()=> 'receipt-request-'+(++sequence));
  api.inventoryPurchases.mockImplementation(async()=>({items:clone(invoices),stock:[]}));
  api.inventoryPurchaseReceivingContext.mockImplementation(async id=>({invoice:clone(invoices.find(item=>item.id===id)),locations:storageLocations,writer_disabled:writerDisabled,operational_active:operationalActive}));
  api.previewInventoryPurchaseReceipt.mockResolvedValue({preview_hash:'verified-preview',writer_disabled:false,fulfillment_eligible:false});
  api.saveInventoryPurchaseReceipt.mockImplementation(async(id,body)=>applyReceipt(id,body));
  host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});

test('waiting queue includes unpaid and fully paid pending invoices while history never offers receive',async()=>{
  await render();
  expect([...host.querySelectorAll('.mz-receipt-list h3')].map(element=>element.textContent.split(' · ')[0])).toEqual(['INV-ONE','INV-PARTIAL','INV-PAID']);
  expect(row('INV-PAID').textContent).toContain('المسدد 100.00 · المتبقي للمورد 0.00');
  expect(row('INV-PAID').querySelector('button').textContent).toBe('تسجيل استلام');
  expect(row('INV-PARTIAL').textContent).toContain('مستلمة جزئيًا');
  await click('جميع الفواتير');
  expect(row('INV-OLD').textContent).toContain('الاستلام غير مثبت');
  expect(row('INV-OLD').textContent).toContain('يلزم مطابقة المخزون');
  expect(row('INV-OLD').querySelector('button')).toBeNull();
  expect(api.inventoryPurchaseReceivingContext).not.toHaveBeenCalled();
});

test('closed after supplier return is distinct from fully received and has history only',async()=>{
  await render();await click('جميع الفواتير');
  expect(row('INV-CLOSED').textContent).toContain('لا توجد كمية متبقية للاستلام');
  expect(row('INV-CLOSED').textContent).toContain('المستلمة 2 · المرتجعة للمورد 3 · المتبقية للاستلام 0');
  expect(row('INV-CLOSED').querySelector('button').textContent).toBe('عرض سجل الاستلام');
  await open('INV-CLOSED');
  expect(button('حفظ الاستلام وإضافة الكمية').disabled).toBe(true);
  expect(field('المستلم الآن — فروة')).toBeUndefined();
  expect(host.querySelector('.mz-receipt-form .mz-receipt-history').textContent).toContain('فروة · 2 قطعة · A-1');
  await click('المستلمة');
  expect(row('INV-RECEIVED')).toBeDefined();expect(row('INV-CLOSED')).toBeUndefined();
});

test('non-owner cannot load receiving lists or details even through a direct invoice link',async()=>{
  await render({context:{...context,can_manage_inventory_opening:false},initialInvoiceId:base.id});
  expect(host.textContent).toContain('للمالك فقط');
  expect(api.inventoryPurchases).not.toHaveBeenCalled();expect(api.inventoryPurchaseReceivingContext).not.toHaveBeenCalled();
  expect(api.previewInventoryPurchaseReceipt).not.toHaveBeenCalled();expect(api.saveInventoryPurchaseReceipt).not.toHaveBeenCalled();
});

test('per-line partial receipt uses chosen quantity location date and original invoice line IDs',async()=>{
  await render();await choosePartial();await input('المستلم الآن — سلسال','1');await input('موقع الاستلام — سلسال','shelf-b');
  await input('تاريخ الاستلام',purchaseToday());await input('ملاحظة الاستلام الاختيارية','دفعة أولى');
  await click('حفظ الاستلام وإضافة الكمية');
  const expected={request_id:'receipt-request-1',expected_session_scope:scope,receipt_date:purchaseToday(),note:'دفعة أولى',lines:[{line_id:'fur-line',quantity:2,location_id:'shelf-a'},{line_id:'necklace-line',quantity:1,location_id:'shelf-b'}]};
  expect(api.previewInventoryPurchaseReceipt).toHaveBeenCalledWith(base.id,expected);
  expect(api.saveInventoryPurchaseReceipt).toHaveBeenCalledWith(base.id,{...expected,preview_hash:'verified-preview'});
  expect(api.movement).not.toHaveBeenCalled();expect(api.saveInventoryPurchase).not.toHaveBeenCalled();
  expect(row('INV-ONE').textContent).toContain('المستلمة 2 · المتبقية للاستلام 3');
  const history=host.querySelector('.mz-receipt-form .mz-receipt-history');
  expect(history.textContent).toContain('فروة · 2 قطعة · A-1');expect(history.textContent).toContain('سلسال · 1 قطعة · B-2');
  expect(row('INV-ONE').textContent).toContain('المسدد 0.00 · المتبقي للمورد 100.00');
});

test('receipt of fully paid invoice does not change supplier or cash amounts',async()=>{
  await render();await open('INV-PAID');await input('المستلم الآن — فروة','1');await input('موقع الاستلام — فروة','shelf-a');await click('حفظ الاستلام وإضافة الكمية');
  expect(row('INV-PAID').textContent).toContain('المسدد 100.00 · المتبقي للمورد 0.00');
  expect(api.saveInventoryPurchaseReceipt).toHaveBeenCalledTimes(1);expect(api.movement).not.toHaveBeenCalled();
});

test.each([['zero','0','shelf-a'],['fraction','1.5','shelf-a'],['excess','6','shelf-a'],['negative','-1','shelf-a'],['location','1','']])('invalid %s receipt never reaches preview or stock writer',async(_label,quantity,location)=>{
  await render();await open('INV-ONE');await input('المستلم الآن — فروة',quantity);
  if(location)await input('موقع الاستلام — فروة',location);
  await click('حفظ الاستلام وإضافة الكمية');
  expect(api.previewInventoryPurchaseReceipt).not.toHaveBeenCalled();expect(api.saveInventoryPurchaseReceipt).not.toHaveBeenCalled();
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

test('a receipt date before the invoice is rejected without a writer call',async()=>{
  await render();await choosePartial();await input('تاريخ الاستلام','2025-12-31');await click('حفظ الاستلام وإضافة الكمية');
  expect(host.textContent).toContain('راجع تاريخ الاستلام');expect(api.previewInventoryPurchaseReceipt).not.toHaveBeenCalled();
});

test.each(['writer','operational'])('disabled %s admission preserves read-only history without accepting stock',async kind=>{
  if(kind==='writer')writerDisabled=true;else operationalActive=false;
  await render({initialInvoiceId:partial.id});
  expect(host.querySelector('.mz-receipt-form').disabled).toBe(true);
  expect(host.querySelector('.mz-receipt-history').textContent).toContain('دفعة سابقة');
  await click('حفظ الاستلام وإضافة الكمية');
  expect(api.previewInventoryPurchaseReceipt).not.toHaveBeenCalled();expect(api.saveInventoryPurchaseReceipt).not.toHaveBeenCalled();
});

test('unknown response preserves the identical previewed command across remount and retries it once',async()=>{
  api.saveInventoryPurchaseReceipt.mockRejectedValueOnce({response:{status:503}});
  await render();await choosePartial();await click('حفظ الاستلام وإضافة الكمية');
  const [invoiceId,body]=api.saveInventoryPurchaseReceipt.mock.calls[0];
  expect(host.querySelector('.mz-receipt-form').disabled).toBe(true);
  expect(button('إعادة محاولة الاستلام')).toBeDefined();
  await act(async()=>root.unmount());root=createRoot(host);await render();
  await click('إعادة محاولة الاستلام');
  expect(api.saveInventoryPurchaseReceipt.mock.calls[1]).toEqual([invoiceId,body]);
  expect(api.previewInventoryPurchaseReceipt).toHaveBeenCalledTimes(1);
  expect(button('إعادة محاولة الاستلام')).toBeUndefined();
  expect(api.movement).not.toHaveBeenCalled();
});

test('unknown response can retry after fresh server details show the receipt already consumed all remaining quantity',async()=>{
  let committed;
  api.saveInventoryPurchaseReceipt.mockImplementationOnce(async(id,body)=>{committed=applyReceipt(id,body);throw {response:{status:503}};}).mockImplementationOnce(async()=>committed);
  await render();await open('INV-PAID');await input('المستلم الآن — فروة','5');await input('موقع الاستلام — فروة','shelf-a');await click('حفظ الاستلام وإضافة الكمية');
  const original=api.saveInventoryPurchaseReceipt.mock.calls[0];
  await act(async()=>root.unmount());root=createRoot(host);await render();
  expect(host.querySelector('.mz-receipt-form').textContent).toContain('مستلمة بالكامل');
  await click('إعادة محاولة الاستلام');
  expect(api.saveInventoryPurchaseReceipt.mock.calls[1]).toEqual(original);
  expect(api.previewInventoryPurchaseReceipt).toHaveBeenCalledTimes(1);
  expect(host.querySelector('.mz-receipt-form .mz-receipt-history').textContent).toContain('سجل الاستلام · 1 دفعات');
});

test('legacy saved receipt command remains recoverable with the same request identity',async()=>{
  const command={invoice_id:base.id,body:{request_id:'legacy-receipt-request',expected_session_scope:scope,preview_hash:'legacy-preview',receipt_date:purchaseToday(),note:'',lines:[{line_id:'fur-line',quantity:1,location_id:'shelf-a'}]}};
  localStorage.setItem(key,JSON.stringify(command));
  await render();await click('إعادة محاولة الاستلام');
  expect(api.saveInventoryPurchaseReceipt).toHaveBeenCalledWith(command.invoice_id,command.body);
  expect(api.previewInventoryPurchaseReceipt).not.toHaveBeenCalled();
  expect(button('إعادة محاولة الاستلام')).toBeUndefined();
});

test('definitively not-applied preview change refreshes remaining quantities and allows a corrected new receipt',async()=>{
  api.saveInventoryPurchaseReceipt.mockImplementationOnce(async()=>{
    invoices[0].lines[0].received_quantity=3;invoices[0].lines[0].pending_receipt_quantity=2;invoices[0].receipt_status='partial';
    throw {response:{status:409,data:{detail:{code:'purchase_receipt_preview_changed',not_applied:true,message:'تغيرت بيانات الاستلام'}}}};
  });
  await render();await choosePartial();await click('حفظ الاستلام وإضافة الكمية');
  const first=api.saveInventoryPurchaseReceipt.mock.calls[0][1];
  expect(button('إعادة محاولة الاستلام')).toBeUndefined();
  expect(host.querySelector('.mz-receipt-form').disabled).toBe(false);
  expect(api.inventoryPurchaseReceivingContext.mock.calls.length).toBeGreaterThanOrEqual(2);
  expect(field('المستلم الآن — فروة').max).toBe('2');
  await input('المستلم الآن — فروة','1');await input('موقع الاستلام — فروة','shelf-b');await click('حفظ الاستلام وإضافة الكمية');
  const second=api.saveInventoryPurchaseReceipt.mock.calls[1][1];
  expect(second.request_id).not.toBe(first.request_id);expect(second.lines).toEqual([{line_id:'fur-line',quantity:1,location_id:'shelf-b'}]);
  expect(api.previewInventoryPurchaseReceipt).toHaveBeenCalledTimes(2);
});

test('definitively rejected receipt opens the next durable command invoice before its identical retry',async()=>{
  const command=(invoiceId,id,preview)=>({invoice_id:invoiceId,body:{request_id:id,expected_session_scope:scope,preview_hash:preview,
    receipt_date:purchaseToday(),note:'',lines:[{line_id:'fur-line',quantity:1,location_id:'shelf-a'}]}});
  const first=command(base.id,'a-pending-receipt','a'.repeat(64)),second=command(paid.id,'b-pending-receipt','b'.repeat(64));
  persistInventoryCommand(key,first);persistInventoryCommand(key,second);
  api.saveInventoryPurchaseReceipt.mockRejectedValueOnce({response:{status:409,data:{detail:{code:'purchase_receipt_preview_changed',not_applied:true,message:'تغيرت بيانات الاستلام'}}}});
  await render();
  expect(host.querySelector('.mz-receipt-form > legend').textContent).toContain('INV-ONE');
  await click('إعادة محاولة الاستلام');
  expect(api.saveInventoryPurchaseReceipt.mock.calls[0]).toEqual([first.invoice_id,first.body]);
  expect(readInventoryCommands(key,()=>{})).toEqual([second]);
  expect(api.inventoryPurchaseReceivingContext).toHaveBeenLastCalledWith(second.invoice_id,{signal:expect.any(AbortSignal)});
  expect(host.querySelector('.mz-receipt-form > legend').textContent).toContain('INV-PAID');
  expect(host.querySelector('.mz-receipt-form').disabled).toBe(true);
  await click('إعادة محاولة الاستلام');
  expect(api.saveInventoryPurchaseReceipt.mock.calls[1]).toEqual([second.invoice_id,second.body]);
  expect(api.previewInventoryPurchaseReceipt).not.toHaveBeenCalled();
  expect(readInventoryCommands(key,()=>{})).toEqual([]);
  expect(invoices.find(invoice=>invoice.id===base.id).lines[0].received_quantity).toBe(0);
  expect(invoices.find(invoice=>invoice.id===paid.id).lines[0].received_quantity).toBe(1);
  expect(api.movement).not.toHaveBeenCalled();
});

test('local persistence failure after preview cannot send the stock write',async()=>{
  await render();await choosePartial();
  const storage=jest.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw Error('storage unavailable');});
  try{await click('حفظ الاستلام وإضافة الكمية');expect(api.previewInventoryPurchaseReceipt).toHaveBeenCalledTimes(1);expect(api.saveInventoryPurchaseReceipt).not.toHaveBeenCalled();}
  finally{storage.mockRestore();}
});

test('history comes from fresh saved receipt readback and explicit refresh does not follow focus events',async()=>{
  await render({initialInvoiceId:partial.id});
  expect(host.querySelector('.mz-receipt-form .mz-receipt-history').textContent).toContain('دفعة سابقة');
  const lists=api.inventoryPurchases.mock.calls.length,details=api.inventoryPurchaseReceivingContext.mock.calls.length;
  await act(async()=>window.dispatchEvent(new Event('focus')));
  expect(api.inventoryPurchases).toHaveBeenCalledTimes(lists);expect(api.inventoryPurchaseReceivingContext).toHaveBeenCalledTimes(details);
  await click('تحديث فواتير الاستلام');
  expect(api.inventoryPurchases).toHaveBeenCalledTimes(lists+1);expect(api.inventoryPurchaseReceivingContext).toHaveBeenCalledTimes(details+1);
});
