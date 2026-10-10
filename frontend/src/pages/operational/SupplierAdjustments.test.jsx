import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import SupplierAdjustments from './SupplierAdjustments';
import {operationalApi as api} from './api';
jest.mock('./api',()=>({operationalApi:{supplierAdjustmentEntry:jest.fn(),saveSupplierAdjustment:jest.fn(),context:jest.fn()},requestId:()=> 'adjustment-one',messageFor:()=> 'تعذر الحفظ'}));
let host,root;const context={session_scope:'owner:actor',status:'active',permissions:{move:true}};
const invoice={invoice_id:'invoice',invoice_number:'INV',supplier_name:'مورد',adjusted_gross:'92.00',outstanding:'92.00',credit:'0.00',lines:[{kind:'product',item_id:'p',name:'منتج',image_url:'/test.png',remaining_quantity:2,remaining_gross:'92.00'}]};
const render=async props=>{await act(async()=>root.render(<SupplierAdjustments context={context} supplierId="supplier" {...props}/>));};
const click=async text=>{await act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent===text).click());};
const input=async(label,value)=>{const el=[...host.querySelectorAll('label')].find(l=>l.textContent.startsWith(label)).querySelector('input');await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));});};
const load=async()=>{await input('رقم فاتورة المورد','INV');await click('عرض الفاتورة');};
const accept=async()=>{await input('مرجع قبول المورد','CREDIT-1');await act(async()=>host.querySelector('input[type=checkbox]').click());};
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;localStorage.clear();host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);jest.resetAllMocks();api.supplierAdjustmentEntry.mockResolvedValue(invoice);api.saveSupplierAdjustment.mockResolvedValue({...invoice,adjusted_gross:'80'});api.context.mockResolvedValue(context);});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
test('fixed gross discount looks up exact supplier invoice and creates no cash command',async()=>{await render();await load();expect(api.supplierAdjustmentEntry).toHaveBeenCalledWith('supplier','INV');await input('مبلغ الخصم','12');await accept();await click('حفظ قبول المورد');expect(api.saveSupplierAdjustment).toHaveBeenCalledWith(expect.objectContaining({invoice_id:'invoice',kind:'discount',amount:'12',lines:[],accepted:true}));expect(api.saveSupplierAdjustment.mock.calls[0][0]).not.toHaveProperty('bank_id');expect(host.textContent).toContain('دون حركة بنك أو صندوق');});
test('accepted item return sends quantity only and server determines invoice value',async()=>{await render({kind:'return'});await load();expect(host.querySelector('img').alt).toBe('منتج');await input('كمية الإرجاع','1');await accept();await click('حفظ قبول المورد');expect(api.saveSupplierAdjustment).toHaveBeenCalledWith(expect.objectContaining({kind:'return',amount:null,lines:[{kind:'product',item_id:'p',quantity:1}]}));});
test('return quantity and acceptance are enforced before write',async()=>{await render({kind:'return'});await load();await input('كمية الإرجاع','3');await click('حفظ قبول المورد');expect(api.saveSupplierAdjustment).not.toHaveBeenCalled();await accept();await click('حفظ قبول المورد');expect(api.saveSupplierAdjustment).not.toHaveBeenCalled();expect(host.textContent).toContain('اختر كميات صحيحة');});
test('lost acknowledgement survives remount and retries immutable command',async()=>{api.saveSupplierAdjustment.mockRejectedValueOnce(new Error('lost'));await render();await load();await input('مبلغ الخصم','12');await accept();await click('حفظ قبول المورد');const first=api.saveSupplierAdjustment.mock.calls[0][0];await act(async()=>root.render(<div/>));await render({kind:'return'});expect(host.querySelectorAll('input')).toHaveLength(0);expect(host.textContent).toContain('خصم · فاتورة INV');await click('إعادة محاولة الحفظ');expect(api.saveSupplierAdjustment.mock.calls[1][0]).toEqual(first);expect(localStorage.getItem('mezan.operational.supplier-adjustment.v1:owner:actor')).toBeNull();});
test('reports-only cannot save or lookup invoice input',async()=>{await render({context:{...context,permissions:{move:false,reports:true}}});expect(host.querySelector('fieldset').disabled).toBe(true);expect(api.supplierAdjustmentEntry).not.toHaveBeenCalled();expect(api.saveSupplierAdjustment).not.toHaveBeenCalled();});
test('scope change retains command without sending',async()=>{api.context.mockResolvedValue({session_scope:'changed'});await render();await load();await input('مبلغ الخصم','12');await accept();await click('حفظ قبول المورد');expect(api.saveSupplierAdjustment).not.toHaveBeenCalled();expect(localStorage.getItem('mezan.operational.supplier-adjustment.v1:owner:actor')).not.toBeNull();});
test('definitive not-applied rejection unlocks form while uncertain failure stays locked',async()=>{api.saveSupplierAdjustment.mockRejectedValueOnce({response:{data:{detail:{not_applied:true}}}});await render();await load();await input('مبلغ الخصم','12');await accept();await click('حفظ قبول المورد');expect(host.querySelector('fieldset').disabled).toBe(false);expect(localStorage.getItem('mezan.operational.supplier-adjustment.v1:owner:actor')).toBeNull();});

test('another pending operation is never overwritten by a fresh form',async()=>{await render();await load();await input('مبلغ الخصم','12');await accept();const stored={invoice_number:'OTHER',body:{expected_session_scope:'owner:actor',request_id:'earlier',kind:'discount',amount:'3'}};localStorage.setItem('mezan.operational.supplier-adjustment.v1:owner:actor',JSON.stringify(stored));await click('حفظ قبول المورد');expect(api.saveSupplierAdjustment).not.toHaveBeenCalled();expect(JSON.parse(localStorage.getItem('mezan.operational.supplier-adjustment.v1:owner:actor'))).toEqual(stored);expect(host.textContent).toContain('فاتورة OTHER');});

test('returning silver validates its own remaining quantity and leaves gold untouched',async()=>{
  const silver={...invoice.lines[0],variant_id:'silver',name:'منتج — فضي',remaining_quantity:10,remaining_gross:'100.00'};
  const gold={...invoice.lines[0],variant_id:'gold',name:'منتج — ذهبي',remaining_quantity:20,remaining_gross:'200.00'};
  api.supplierAdjustmentEntry.mockResolvedValue({...invoice,lines:[silver,gold]});
  api.saveSupplierAdjustment.mockResolvedValue({...invoice,lines:[{...silver,remaining_quantity:8,remaining_gross:'80.00'},gold]});
  await render({kind:'return'});await load();
  expect(host.querySelector('section').dir).toBe('rtl');
  await input('كمية الإرجاع — منتج — فضي','11');await accept();await click('حفظ قبول المورد');
  expect(api.saveSupplierAdjustment).not.toHaveBeenCalled();
  await input('كمية الإرجاع — منتج — فضي','2');await click('حفظ قبول المورد');
  expect(api.saveSupplierAdjustment.mock.calls[0][0].lines).toEqual([{kind:'product',item_id:'p',variant_id:'silver',quantity:2}]);
  const articles=[...host.querySelectorAll('article')];
  expect(articles[0].textContent).toContain('المتاح للإرجاع: 8');
  expect(articles[1].textContent).toContain('المتاح للإرجاع: 20');
});
