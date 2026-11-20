import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import StocktakeWorkbench,{normalizeStocktakeQuantity,validateStocktakeQuantity} from './StocktakeWorkbench';
import * as service from '../../services/inventoryStocktakes';

jest.mock('../../services/inventoryStocktakes',()=>({
  loadInventoryStocktakeContext:jest.fn(),listInventoryStocktakes:jest.fn(),getInventoryStocktake:jest.fn(),
  createInventoryStocktake:jest.fn(),saveInventoryStocktake:jest.fn(),actOnInventoryStocktake:jest.fn(),postInventoryStocktake:jest.fn(),
  newStocktakeRequestId:jest.fn(()=> 'stocktake:request-001'),
  inventoryStocktakeMessage:error=>error?.response?.data?.detail?.message||error?.response?.data?.detail||'تعذر إتمام طلب الجرد. احتفظنا بالمدخلات؛ أعد المحاولة.',
}));
jest.mock('../../components/inventory/suite/InventorySuiteFrame',()=>({
  InventoryPageHeader:({title,description,children})=><header><h1>{title}</h1><p>{description}</p>{children}</header>,
  InventorySurface:({children,className})=><section className={className}>{children}</section>,
  InventorySheet:({open,title,description,children,footer,onClose,busy})=>open?<section role="dialog" aria-label={title}><h2>{title}</h2><p>{description}</p><button type="button" aria-label="إغلاق" disabled={busy} onClick={onClose}>إغلاق</button>{children}{footer}</section>:null,
}));

let host,root,context,documentValue;
const schema='g47-stocktake-v1';
const buttons=(text,scope=host)=>[...scope.querySelectorAll('button')].filter(node=>node.textContent.trim()===text);
const button=(text,scope=host)=>buttons(text,scope)[0];
const input=label=>host.querySelector(`[aria-label="${label}"]`);
const dialog=()=>host.querySelector('[role="dialog"]');
const failure=(status,message)=>({response:{status,data:{detail:{message}}}});
async function click(node){expect(node).toBeTruthy();await act(async()=>node.click());}
async function change(node,value){expect(node).toBeTruthy();await act(async()=>{const prototype=node.tagName==='SELECT'?HTMLSelectElement.prototype:node.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,value);node.dispatchEvent(new Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});}
async function render(props={}){await act(async()=>root.render(<StocktakeWorkbench {...props}/>));}
async function openSaved(){await render();await click(button('فتح الجرد'));}
function revised(changes={}){return {...documentValue,revision:documentValue.revision+1,...changes};}
function postingReady(){context.posting_enabled=true;context.posting_blockers=[];documentValue.posting_enabled=true;documentValue.posting_blockers=[];}

beforeEach(()=>{
  global.IS_REACT_ACT_ENVIRONMENT=true;host=window.document.createElement('div');window.document.body.appendChild(host);root=createRoot(host);
  context={schema_version:schema,capabilities:{view:true,count:true,review:true,post:true},posting_enabled:false,posting_blockers:[{code:'stocktake_posting_disabled',message:'ترحيل فروق الجرد غير مفعّل في هذه البيئة.'}],warehouses:[{id:'real-wh-1',name:'مستودع الرياض'}],inventory_accounts:[{entity_id:'native-inventory-account',label:'مخزون البضاعة'}],variance_accounts:[{code:'5090',name:'فروقات الجرد'}]};
  documentValue={schema_version:schema,id:'native-count-1',title:'جرد فعلي محفوظ',status:'draft',revision:2,capabilities:{...context.capabilities},posting_enabled:false,posting_blockers:[...context.posting_blockers],warehouse_ids:['real-wh-1'],counted_lines:0,variance_lines:0,total_lines:2,increase_value:'0.00',decrease_value:'0.00',updated_at:'2026-11-20T10:00:00Z',audit:[{request_id:'real-request',actor_id:'real-actor',action:'create',revision:1,at:'2026-11-20T09:00:00Z'}],lines:[
    {id:'native-row-product',name:'بلوفر أصلي',sku:'SKU-P-1',kind:'product',unit:'piece',warehouse_id:'real-wh-1',warehouse_name:'مستودع الرياض',location_id:'native-location-1',location_code:'WH01-S01-C01-001',recorded_quantity:'10',counted_quantity:null,difference:null,unit_cost:'12.50',difference_value:null,reason:'',specifications:{اللون:'أخضر',المقاس:'L'}},
    {id:'native-row-component',name:'خيط تطريز',sku:'SKU-C-2',kind:'component',unit:'gram',warehouse_id:'real-wh-1',warehouse_name:'مستودع الرياض',location_id:'native-location-2',location_code:'WH01-S01-C01-002',recorded_quantity:'2.125',counted_quantity:null,difference:null,unit_cost:null,difference_value:null,reason:''},
  ]};
  service.loadInventoryStocktakeContext.mockImplementation(async()=>context);
  service.newStocktakeRequestId.mockReturnValue('stocktake:request-001');
  service.listInventoryStocktakes.mockImplementation(async()=>({schema_version:schema,read_only:true,items:[documentValue],window_complete:true}));
  service.getInventoryStocktake.mockImplementation(async()=>documentValue);
  service.createInventoryStocktake.mockReset();service.saveInventoryStocktake.mockReset();service.actOnInventoryStocktake.mockReset();service.postInventoryStocktake.mockReset();
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();jest.clearAllMocks();});

test('normalizes Arabic digits without guessing native units and validates integral products and component precision',()=>{
  expect(normalizeStocktakeQuantity(' ٢٫١٢٥ ')).toBe('2.125');expect(normalizeStocktakeQuantity('۰')).toBe('0');
  expect(validateStocktakeQuantity('', 'product')).toBe('');expect(validateStocktakeQuantity('0','product')).toBe('');
  expect(validateStocktakeQuantity('1.125','product')).toContain('عددًا صحيحًا');expect(validateStocktakeQuantity('1.125001','component')).toBe('');
  expect(validateStocktakeQuantity('1.1250019','component')).toContain('6');expect(validateStocktakeQuantity('-2','component')).toBeTruthy();
});

test('read-only delegates never fetch the writer workspace or expose count actions',async()=>{
  await render({accessMode:'read'});expect(service.loadInventoryStocktakeContext).not.toHaveBeenCalled();expect(service.listInventoryStocktakes).not.toHaveBeenCalled();expect(host.textContent).toContain('هذه الصلاحية للعرض');expect(button('جرد جديد')).toBeUndefined();
});

test('an invalid or unavailable context fails closed before documents or forms are exposed',async()=>{
  service.loadInventoryStocktakeContext.mockResolvedValue({...context,schema_version:'unknown'});await render();expect(service.listInventoryStocktakes).not.toHaveBeenCalled();expect(host.textContent).toContain('تعذر فتح مساحة الجرد');expect(button('جرد جديد')).toBeUndefined();
});

test('creates a native scoped snapshot from selected real warehouses and immediately displays its returned rows',async()=>{
  await render();await click(button('جرد جديد'));const title=dialog().querySelector('input:not([type=checkbox])');await change(title,'جرد المستودع الفعلي');
  service.createInventoryStocktake.mockResolvedValue({...documentValue,revision:1,title:'جرد المستودع الفعلي'});
  await act(async()=>host.querySelector('#mz-count-create').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));expect(service.createInventoryStocktake).not.toHaveBeenCalled();expect(dialog().textContent).toContain('حدد مستودعًا');
  await click(dialog().querySelector('input[type=checkbox]'));await act(async()=>host.querySelector('#mz-count-create').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  expect(service.createInventoryStocktake).toHaveBeenCalledWith({request_id:'stocktake:request-001',title:'جرد المستودع الفعلي',warehouse_ids:['real-wh-1'],location_ids:[],row_ids:[]});
  expect(host.querySelectorAll('.mz-count-line')).toHaveLength(2);expect(dialog().textContent).toContain('بلوفر أصلي');expect(dialog().textContent).toContain('اللون: أخضر');expect(service.postInventoryStocktake).not.toHaveBeenCalled();
});

test('partial save sends changed native rows only; explicit zero stays zero and untouched blanks stay uncounted',async()=>{
  await openSaved();await change(input('الكمية الفعلية SKU-P-1'),'0');await change(input('سبب الفرق SKU-P-1'),'غير موجود عند الجرد');
  service.saveInventoryStocktake.mockResolvedValue(revised({counted_lines:1,variance_lines:1,lines:[{...documentValue.lines[0],counted_quantity:'0',difference:'-10',difference_value:'-125.00',reason:'غير موجود عند الجرد'},documentValue.lines[1]]}));
  await click(button('حفظ الجرد الجزئي'));expect(service.saveInventoryStocktake).toHaveBeenCalledWith('native-count-1',{request_id:'stocktake:request-001',expected_revision:2,title:'جرد فعلي محفوظ',lines:[{id:'native-row-product',counted_quantity:'0',unit:'piece',reason:'غير موجود عند الجرد'}]});
  expect(input('الكمية الفعلية SKU-C-2').value).toBe('');expect(dialog().textContent).toContain('غير مثبت');expect(service.actOnInventoryStocktake).not.toHaveBeenCalled();expect(service.postInventoryStocktake).not.toHaveBeenCalled();
});
test('partial warehouse count submits explicitly selected native location ids',async()=>{
  context.locations=[{id:'native-location-1',warehouse_id:'real-wh-1',label:'د1 / ر2 / خ3',stock_lines:2}];
  await render();await click(button('جرد جديد'));await change(dialog().querySelector('input:not([type=checkbox])'),'جرد خانة محددة');
  await click(dialog().querySelector('input[type=checkbox]'));await click(input('جرد الخانة د1 / ر2 / خ3'));
  service.createInventoryStocktake.mockResolvedValue({...documentValue,revision:1});
  await act(async()=>host.querySelector('#mz-count-create').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  expect(service.createInventoryStocktake.mock.calls[0][0].location_ids).toEqual(['native-location-1']);
});

test('clearing a counted quantity saves null, and quantities are never converted from native units',async()=>{
  documentValue.lines[0]={...documentValue.lines[0],counted_quantity:'3',difference:'-7'};await openSaved();await change(input('الكمية الفعلية SKU-P-1'),'');await change(input('الكمية الفعلية SKU-C-2'),'٢٫١٢٥');
  service.saveInventoryStocktake.mockResolvedValue(revised());await click(button('حفظ الجرد الجزئي'));
  expect(service.saveInventoryStocktake.mock.calls[0][1].lines).toEqual([{id:'native-row-product',counted_quantity:null,unit:'piece',reason:''},{id:'native-row-component',counted_quantity:'2.125',unit:'gram',reason:''}]);
});

test('fractional product quantities are retained with an error and never sent',async()=>{
  await openSaved();await change(input('الكمية الفعلية SKU-P-1'),'2.5');await click(button('حفظ الجرد الجزئي'));expect(service.saveInventoryStocktake).not.toHaveBeenCalled();expect(input('الكمية الفعلية SKU-P-1').value).toBe('2.5');expect(dialog().textContent).toContain('عددًا صحيحًا');
});

test('busy save prevents dismissal and an uncertain response retries the exact immutable command',async()=>{
  await openSaved();await change(input('الكمية الفعلية SKU-P-1'),'3');let rejectRequest;service.saveInventoryStocktake.mockImplementationOnce(()=>new Promise((resolve,reject)=>{rejectRequest=reject;}));
  await click(button('حفظ الجرد الجزئي'));expect(dialog().querySelector('[aria-label="إغلاق"]').disabled).toBe(true);expect(input('الكمية الفعلية SKU-P-1').disabled).toBe(true);
  await act(async()=>rejectRequest(Error('response lost')));expect(input('الكمية الفعلية SKU-P-1').value).toBe('3');expect(dialog().querySelector('[aria-label="إغلاق"]').disabled).toBe(true);
  const original=service.saveInventoryStocktake.mock.calls[0][1];service.saveInventoryStocktake.mockResolvedValueOnce(revised({lines:[{...documentValue.lines[0],counted_quantity:'3'},documentValue.lines[1]]}));await click(button('إعادة الطلب نفسه'));
  expect(service.saveInventoryStocktake.mock.calls[1][1]).toBe(original);expect(input('الكمية الفعلية SKU-P-1').value).toBe('3');expect(dialog().querySelector('[aria-label="إغلاق"]').disabled).toBe(false);
});

test('review submission requires explicit native accounts and a difference reason without posting',async()=>{
  documentValue.lines[0]={...documentValue.lines[0],counted_quantity:'8',difference:'-2',difference_value:'-25.00'};documentValue.counted_lines=1;documentValue.variance_lines=1;
  await openSaved();await click(button('إرسال للمراجعة'));await click(button('إرسال للمراجعة'));expect(service.actOnInventoryStocktake).not.toHaveBeenCalled();expect(dialog().textContent).toContain('اختر حساب المخزون');
  await change(input('حساب المخزون'),'native-inventory-account');await change(input('حساب فروقات الجرد'),'5090');await click(button('إرسال للمراجعة'));expect(service.actOnInventoryStocktake).not.toHaveBeenCalled();expect(dialog().textContent).toContain('اكتب سببًا للفروقات');
  await change(input('سبب إجراء الجرد'),'تلف مثبت في الفحص');service.actOnInventoryStocktake.mockResolvedValue(revised({status:'submitted',inventory_account_id:'native-inventory-account',variance_account_code:'5090',reason:'تلف مثبت في الفحص'}));await click(button('إرسال للمراجعة'));
  expect(service.actOnInventoryStocktake).toHaveBeenCalledWith('native-count-1',{request_id:'stocktake:request-001',expected_revision:2,action:'submit',reason:'تلف مثبت في الفحص',inventory_account_id:'native-inventory-account',variance_account_code:'5090'});expect(service.postInventoryStocktake).not.toHaveBeenCalled();expect(dialog().textContent).toContain('مخزون البضاعة');
});

test('fresh review capability is required even when the saved document was readable by an owner',async()=>{
  context.capabilities.review=false;documentValue.status='submitted';await openSaved();expect(button('اعتماد')).toBeUndefined();expect(button('رفض')).toBeUndefined();expect(button('إعادة للتعديل')).toBeUndefined();expect(service.actOnInventoryStocktake).not.toHaveBeenCalled();
});

test.each([['إعادة للتعديل','return','returned'],['رفض','reject','rejected']])('review %s requires and retains a reason, then records the native action',async(label,action,status)=>{
  documentValue.status='submitted';await openSaved();await click(button(label));await click(button(action==='return'?'إعادة للتعديل':'رفض الجرد'));expect(service.actOnInventoryStocktake).not.toHaveBeenCalled();
  await change(input('سبب إجراء الجرد'),'الكمية تحتاج إعادة تحقق');service.actOnInventoryStocktake.mockResolvedValue(revised({status,review_reason:'الكمية تحتاج إعادة تحقق'}));await click(button(action==='return'?'إعادة للتعديل':'رفض الجرد'));
  expect(service.actOnInventoryStocktake).toHaveBeenCalledWith('native-count-1',{request_id:'stocktake:request-001',expected_revision:2,action,reason:'الكمية تحتاج إعادة تحقق'});expect(service.postInventoryStocktake).not.toHaveBeenCalled();
});

test('approval is a separate confirmation and default-closed posting exposes its real blocker',async()=>{
  documentValue.status='submitted';await openSaved();await click(button('اعتماد'));expect(service.actOnInventoryStocktake).not.toHaveBeenCalled();service.actOnInventoryStocktake.mockResolvedValue(revised({status:'approved',posting_operation_id:'server-posting-operation-3'}));await click(button('اعتماد الجرد'));
  expect(service.actOnInventoryStocktake.mock.calls[0][1].action).toBe('approve');expect(button('مراجعة ترحيل الفروق').disabled).toBe(true);expect(dialog().textContent).toContain('ترحيل فروق الجرد غير مفعّل');expect(service.postInventoryStocktake).not.toHaveBeenCalled();
});

test('posting requires a separate checked confirmation and retries only the server-issued operation id',async()=>{
  postingReady();documentValue.status='approved';documentValue.posting_operation_id='server-issued-operation-2';await openSaved();await click(button('مراجعة ترحيل الفروق'));expect(service.postInventoryStocktake).not.toHaveBeenCalled();expect(button('ترحيل الفروق').disabled).toBe(true);
  await click(dialog().querySelector('.mz-count-post-consent input'));service.postInventoryStocktake.mockRejectedValueOnce(Error('lost response'));await click(button('ترحيل الفروق'));
  expect(service.postInventoryStocktake).toHaveBeenCalledWith('native-count-1',{operation_id:'server-issued-operation-2',expected_revision:2});const original=service.postInventoryStocktake.mock.calls[0][1];
  service.postInventoryStocktake.mockResolvedValueOnce(revised({status:'posted'}));await click(button('إعادة الطلب نفسه'));expect(service.postInventoryStocktake.mock.calls[1][1]).toBe(original);expect(button('مراجعة ترحيل الفروق')).toBeUndefined();expect(service.actOnInventoryStocktake).not.toHaveBeenCalled();
});

test('an approved document cannot post without an explicit fresh post grant',async()=>{
  postingReady();context.capabilities.post=false;documentValue.status='approved';documentValue.posting_operation_id='server-issued-operation-2';await openSaved();expect(button('مراجعة ترحيل الفروق').disabled).toBe(true);expect(dialog().textContent).toContain('صلاحية صريحة');expect(service.postInventoryStocktake).not.toHaveBeenCalled();
});

test('an approved but unposted count can be returned by a reviewer and cannot reuse its invalidated post operation',async()=>{
  postingReady();documentValue.status='approved';documentValue.posting_operation_id='server-issued-operation-2';await openSaved();expect(button('اعتماد')).toBeUndefined();await click(button('إعادة للتعديل'));await change(input('سبب إجراء الجرد'),'تغيرت المقارنة قبل الترحيل');
  service.actOnInventoryStocktake.mockResolvedValue(revised({status:'returned',posting_operation_id:null,review_reason:'تغيرت المقارنة قبل الترحيل'}));await click(button('إعادة للتعديل'));
  expect(service.actOnInventoryStocktake.mock.calls[0][1].action).toBe('return');expect(button('مراجعة ترحيل الفروق')).toBeUndefined();expect(button('حفظ الجرد الجزئي')).toBeTruthy();expect(service.postInventoryStocktake).not.toHaveBeenCalled();
});

test('revision conflict retains edits and reload needs explicit confirmation rather than overwriting',async()=>{
  await openSaved();await change(input('الكمية الفعلية SKU-P-1'),'4');service.saveInventoryStocktake.mockRejectedValueOnce(failure(409,'تغير الإصدار'));await click(button('حفظ الجرد الجزئي'));expect(input('الكمية الفعلية SKU-P-1').value).toBe('4');expect(button('إرسال للمراجعة').disabled).toBe(true);
  await click(button('قراءة النسخة المحفوظة'));expect(service.getInventoryStocktake).toHaveBeenCalledTimes(1);expect(input('الكمية الفعلية SKU-P-1').value).toBe('4');service.getInventoryStocktake.mockResolvedValueOnce(revised({lines:[{...documentValue.lines[0],counted_quantity:'6'},documentValue.lines[1]]}));await click(button('قراءة النسخة المحفوظة'));
  expect(service.getInventoryStocktake).toHaveBeenCalledTimes(2);expect(input('الكمية الفعلية SKU-P-1').value).toBe('6');expect(service.saveInventoryStocktake).toHaveBeenCalledTimes(1);
});

test('comparison refresh is explicit, uses revision CAS, and leaves the opening-stock writer unused',async()=>{
  await openSaved();await click(button('تحديث المقارنة من المستودع'));expect(service.actOnInventoryStocktake).not.toHaveBeenCalled();service.actOnInventoryStocktake.mockResolvedValue(revised());await click(button('تحديث المقارنة'));
  expect(service.actOnInventoryStocktake).toHaveBeenCalledWith('native-count-1',{request_id:'stocktake:request-001',expected_revision:2,action:'refresh_snapshot',reason:''});expect(service.saveInventoryStocktake).not.toHaveBeenCalled();expect(service.postInventoryStocktake).not.toHaveBeenCalled();
});

test('revoked permission clears open records and all saved writer actions immediately',async()=>{
  await openSaved();await change(input('الكمية الفعلية SKU-P-1'),'3');service.saveInventoryStocktake.mockRejectedValueOnce(failure(403,'سُحبت الصلاحية'));await click(button('حفظ الجرد الجزئي'));expect(dialog()).toBeNull();expect(host.textContent).not.toContain('بلوفر أصلي');expect(button('جرد جديد')).toBeUndefined();expect(button('فتح الجرد')).toBeUndefined();expect(host.textContent).toContain('سُحبت الصلاحية');
});

test('switching to a read-only scope discards the old visible writer document and never rereads it',async()=>{
  await openSaved();await render({accessMode:'read'});expect(dialog()).toBeNull();expect(host.textContent).not.toContain('بلوفر أصلي');expect(service.loadInventoryStocktakeContext).toHaveBeenCalledTimes(1);expect(service.getInventoryStocktake).toHaveBeenCalledTimes(1);
});
