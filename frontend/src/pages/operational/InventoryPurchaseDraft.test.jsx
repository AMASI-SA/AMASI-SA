import React,{act,useState} from 'react';
import {createRoot} from 'react-dom/client';
import InventoryPurchaseDraft,{readPurchaseDraft} from './InventoryPurchaseDraft';
import {operationalApi as api,requestId} from './api';
import {purchaseToday} from './inventoryPurchaseWorkflow';

jest.mock('./api',()=>({
  operationalApi:{entities:jest.fn(),inventoryPurchasePaymentCandidates:jest.fn(),saveInventoryPurchase:jest.fn(),movement:jest.fn(),saveInventoryPurchaseReceipt:jest.fn(),inventoryOptionRoles:jest.fn(),saveInventoryOptionRoles:jest.fn()},
  requestId:jest.fn(),messageFor:error=>error.message||'خطأ في API',
}));

const draftKey='mezan.operational.inventory.v1:draft-test:draft';
const scope='draft-test';
const context={session_scope:scope,can_manage_inventory_opening:true,permissions:{move:true},status:'active'};
const rules=[{option_id:'emb',value_id:'yes',operator:'='}];
const fur={id:'fur',kind:'product',name:'فروة',sku:'AMS-FUR',configuration_contract_version:2,physical_option_ids:['size'],options:[
  {id:'size',name:'المقاس',type:'radio',required:true,stock_identity:true,values:[{id:'two',name:'سنتان'}]},
  {id:'emb',name:'تطريز الاسم',type:'radio',required:true,defer_allowed:true,values:[{id:'yes',name:'نعم'},{id:'no',name:'لا'}]},
  {id:'shape',name:'شكل التطريز',type:'radio',required:true,defer_allowed:true,visibility_rules:rules,values:[{id:'heart',name:'قلب'}]},
  {id:'name',name:'الاسم على الفروة',type:'text',required:true,defer_allowed:true,visibility_rules:rules,values:[]},
  {id:'note',name:'ملاحظة الصنف',type:'textarea',required:false,defer_allowed:true,values:[]},
]};
const necklace={id:'necklace',kind:'product',name:'سلسال',sku:'AMS-NECKLACE',configuration_contract_version:2,physical_option_ids:['color'],options:[
  {id:'color',name:'اللون',type:'radio',required:true,stock_identity:true,values:[{id:'gold',name:'ذهبي'}]},
]};
const component={id:'thread',kind:'component',name:'خيط تطريز',sku:'CMP-1',options:[]};
const catalog=[fur,necklace,component];
const suppliers=[{id:'supplier',name:'مورد تجريبي'}];
const locations=[{id:'shelf',name:'A-1',warehouse_name:'المخزن'}];
const initialForm=()=>({supplier_id:'supplier',invoice_number:'INV-TEST',invoice_date:purchaseToday(),lines:[],note:''});
let host,root,onApprove,sequence;

function Harness({restore=false,disabled=false,catalogItems=catalog,sessionContext=context}){
  const [saved]=useState(()=>restore?readPurchaseDraft(draftKey):null);
  const [form,setForm]=useState(()=>saved?.form||initialForm());
  return <InventoryPurchaseDraft form={form} onChange={setForm} initialDraft={saved} catalog={catalogItems} locations={locations} suppliers={suppliers} disabled={disabled} context={sessionContext} draftKey={draftKey} onApprove={onApprove}/>;
}
const render=async props=>act(async()=>root.render(<Harness {...props}/>));
const button=label=>[...host.querySelectorAll('button')].find(element=>element.getAttribute('aria-label')===label||element.textContent===label);
const click=async label=>act(async()=>{const element=button(label)||host.querySelector(`[aria-label="${label}"]`);if(!element)throw Error('Missing control: '+label);element.click();});
const control=label=>host.querySelector(`[aria-label="${label}"]`)||[...host.querySelectorAll('.op-field')].find(element=>element.querySelector('span')?.textContent===label)?.querySelector('input,select,textarea');
const input=async(label,value)=>act(async()=>{
  const element=control(label);if(!element)throw Error('Missing field: '+label);
  const prototype=element.tagName==='SELECT'?HTMLSelectElement.prototype:element.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype,'value').set.call(element,value);
  element.dispatchEvent(new Event(element.tagName==='SELECT'?'change':'input',{bubbles:true}));
});
const persisted=()=>JSON.parse(localStorage.getItem(draftKey));
const rows=()=>[...host.querySelectorAll('.mz-invoice-row')];
async function configureFur(name='فاطمة'){
  await click('اختيار فروة');await click('المقاس: سنتان');await click('تطريز الاسم: نعم');await click('شكل التطريز: قلب');
  await input('الاسم على الفروة · البند 1',name);await input('الكمية — فروة','2');await input('سعر الوحدة قبل الضريبة — فروة','85');
}
async function addFur(){await configureFur();await click('حفظ الصنف في المسودة');}
async function addNecklace(){await click('اختيار سلسال');await click('اللون: ذهبي');await input('الكمية — سلسال','3');await input('سعر الوحدة قبل الضريبة — سلسال','25');await click('حفظ الصنف في المسودة');}

beforeEach(()=>{
  global.IS_REACT_ACT_ENVIRONMENT=true;localStorage.clear();jest.resetAllMocks();sequence=0;
  requestId.mockImplementation(()=> 'draft-request-'+(++sequence));onApprove=jest.fn();
  api.entities.mockResolvedValue({items:[]});api.inventoryPurchasePaymentCandidates.mockResolvedValue({items:[]});
  host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});

test('supplier number and date are one compact group and selected kind has an explicit mark',async()=>{
  await render();
  const metadata=host.querySelector('.mz-invoice-draft > .mz-invoice-meta');
  expect([...metadata.querySelectorAll('.op-field > span')].map(element=>element.textContent)).toEqual(['المورد','رقم فاتورة المورد','تاريخ الفاتورة']);
  expect(button('منتج').getAttribute('aria-pressed')).toBe('true');
  expect(button('منتج').querySelector('svg')).not.toBeNull();
  expect(button('مكوّن').getAttribute('aria-pressed')).toBe('false');
  await click('مكوّن');
  expect(button('مكوّن').getAttribute('aria-pressed')).toBe('true');
  expect(button('مكوّن').querySelector('svg')).not.toBeNull();
  expect(button('اختيار خيط تطريز')).toBeDefined();
  expect(button('اختيار فروة')).toBeUndefined();
});

test('saving one configured item creates only a compact local draft row with its choices',async()=>{
  await render();await addFur();
  expect(host.querySelectorAll('.mz-invoice-editor')).toHaveLength(1);
  expect(host.querySelectorAll('.mz-purchase-configuration')).toHaveLength(0);
  expect(rows()).toHaveLength(1);expect(rows()[0].textContent).toContain('المقاس: سنتان');
  expect(rows()[0].textContent).toContain('الاسم على الفروة: فاطمة');
  expect(rows()[0].textContent).toContain('شكل التطريز: قلب');
  expect(rows()[0].textContent).toContain('170.00');
  expect(button('اختيار سلسال')).toBeDefined();
  expect(persisted().form.lines).toHaveLength(1);expect(persisted().editor).toBeNull();
  expect(api.saveInventoryPurchase).not.toHaveBeenCalled();expect(api.movement).not.toHaveBeenCalled();expect(api.saveInventoryPurchaseReceipt).not.toHaveBeenCalled();expect(onApprove).not.toHaveBeenCalled();
});

test('newest item is first and editing an older item preserves its position and identity',async()=>{
  await render();await addFur();await addNecklace();
  expect(rows().map(row=>row.querySelector('.mz-invoice-row-details > strong').textContent)).toEqual(['سلسال','فروة']);
  const before=persisted().form.lines.map(line=>line.draft_key);
  await click('تعديل فروة 2');expect(control('الاسم على الفروة · البند 1').value).toBe('فاطمة');
  await input('الكمية — فروة','4');await click('حفظ الصنف في المسودة');
  expect(persisted().form.lines.map(line=>line.draft_key)).toEqual(before);
  expect(persisted().form.lines.map(line=>line.quantity)).toEqual(['3','4']);
  expect(rows()).toHaveLength(2);expect(rows()[1].textContent).toContain('340.00');
});

test('unsaved edits prompt before switching kind and discard restores the saved row',async()=>{
  await render();await addFur();await click('تعديل فروة 1');await input('الكمية — فروة','9');await click('مكوّن');
  expect(host.querySelector('.mz-invoice-guard')).not.toBeNull();expect(control('الكمية — فروة').value).toBe('9');
  expect(persisted().form.lines[0].quantity).toBe('2');
  await click('البقاء في الصنف');expect(host.querySelector('.mz-invoice-guard')).toBeNull();
  await click('مكوّن');await click('تجاهل التعديل ومتابعة');
  expect(button('مكوّن').getAttribute('aria-pressed')).toBe('true');
  await click('تعديل فروة 1');expect(control('الكمية — فروة').value).toBe('2');
});

test('save-and-continue saves the current item before opening the next kind',async()=>{
  await render();await configureFur();await click('مكوّن');await click('حفظ ومتابعة');
  expect(rows()).toHaveLength(1);expect(persisted().editor).toBeNull();
  expect(button('مكوّن').getAttribute('aria-pressed')).toBe('true');
  expect(button('اختيار خيط تطريز')).toBeDefined();expect(onApprove).not.toHaveBeenCalled();
});

test('location may wait for receipt and review does not submit an invoice',async()=>{
  await render();await addFur();
  expect(persisted().form.lines[0].location_id).toBe('');
  await click('مراجعة الفاتورة');
  expect(host.querySelector('[aria-label="مراجعة الفاتورة قبل الاعتماد"]').textContent).toContain('بانتظار الاستلام');
  expect(onApprove).not.toHaveBeenCalled();expect(api.saveInventoryPurchase).not.toHaveBeenCalled();
  await click('اعتماد الفاتورة');
  expect(onApprove).toHaveBeenCalledTimes(1);
  const command=onApprove.mock.calls[0][0];
  expect(command.receipt).toBeNull();expect(command.payment).toBeNull();
  expect(command.body.receipt_tracking_version).toBe(1);
  expect(command.body.lines[0]).not.toHaveProperty('location_id');
});

test('an open item blocks invoice review even before any edits',async()=>{
  await render();await addFur();await click('اختيار سلسال');await click('مراجعة الفاتورة');
  expect(host.textContent).toContain('احفظ الصنف المفتوح في المسودة أو ألغِ إدخاله أولًا.');
  expect(host.querySelector('[aria-label="مراجعة الفاتورة قبل الاعتماد"]')).toBeNull();
  expect(onApprove).not.toHaveBeenCalled();
  await click('إلغاء إدخال الصنف');await click('مراجعة الفاتورة');
  expect(host.querySelector('[aria-label="مراجعة الفاتورة قبل الاعتماد"]')).not.toBeNull();
});

test('choosing no clears dependent name and style while optional blank remains ready',async()=>{
  await render();await configureFur();await click('تطريز الاسم: لا');
  expect(control('الاسم على الفروة · البند 1')).toBeUndefined();
  expect(host.querySelector('[aria-label="شكل التطريز: قلب"]')).toBeNull();
  await click('حفظ الصنف في المسودة');
  const config=persisted().form.lines[0].purchase_configuration;
  expect(config.state).toBe('ready');expect(config.inputs).toEqual([]);expect(config.deferred_option_ids).toEqual([]);
  expect(config.selections).toContainEqual({option_id:'emb',value_id:'no'});
  expect(rows()[0].textContent).toContain('تطريز الاسم: لا');expect(rows()[0].textContent).not.toContain('فاطمة');
});

test('per-field name deferral appears in summary alongside a fixed shape',async()=>{
  await render();await configureFur();await click('الاسم على الفروة: يُحدد لاحقًا');await click('حفظ الصنف في المسودة');
  expect(persisted().form.lines[0].purchase_configuration.deferred_option_ids).toEqual(['name']);
  expect(rows()[0].textContent).toContain('شكل التطريز: قلب');
  expect(rows()[0].textContent).toContain('الاسم على الفروة: عند طلب العميل');
  expect(rows()[0].textContent).not.toContain('فاطمة');
});

test('unfinished required options block line save without creating a draft or API write',async()=>{
  await render();await click('اختيار فروة');await input('سعر الوحدة قبل الضريبة — فروة','85');await click('حفظ الصنف في المسودة');
  expect(rows()).toHaveLength(0);expect(persisted().editor).not.toBeNull();
  expect(api.saveInventoryPurchase).not.toHaveBeenCalled();
  await click('المقاس: سنتان');await click('تطريز الاسم: نعم');await click('شكل التطريز: قلب');await click('حفظ الصنف في المسودة');
  expect(rows()).toHaveLength(0);expect(host.textContent).toContain('أكمل خيارات المنتج المطلوبة');
});

test('local draft restores invoice metadata, saved rows and the unfinished editor',async()=>{
  await render();await addNecklace();await configureFur('عبير');await input('رقم فاتورة المورد','RESTORED-17');
  await input('ملاحظات اختيارية','مسودة للاختبار');
  await act(async()=>root.unmount());root=createRoot(host);await render({restore:true});
  expect(control('رقم فاتورة المورد').value).toBe('RESTORED-17');expect(control('ملاحظات اختيارية').value).toBe('مسودة للاختبار');
  expect(rows()).toHaveLength(1);expect(rows()[0].textContent).toContain('سلسال');
  expect(control('الاسم على الفروة · البند 1').value).toBe('عبير');expect(control('سعر الوحدة قبل الضريبة — فروة').value).toBe('85');
  expect(api.saveInventoryPurchase).not.toHaveBeenCalled();expect(onApprove).not.toHaveBeenCalled();
  await click('حفظ الصنف في المسودة');expect(rows()).toHaveLength(2);
});

test('restored unsaved editor still prompts before its entered information is discarded',async()=>{
  await render();await configureFur('عبير');
  await act(async()=>root.unmount());root=createRoot(host);await render({restore:true});
  await click('مكوّن');
  expect(host.querySelector('.mz-invoice-guard')).not.toBeNull();
  expect(control('الاسم على الفروة · البند 1').value).toBe('عبير');
  expect(persisted().editor.purchase_configuration.inputs[0].value).toBe('عبير');
});

function notePolicy(revision=0){
  const roles={size:'physical',emb:'customization',shape:'customization',name:'customization',note:revision?'informational':'customization'};
  const product={...fur,configuration_issues:[],inventory_option_policy_revision:revision,options:fur.options.map(option=>({...option,
    inventory_role:roles[option.id],role_locked:option.id==='size',...(roles[option.id]==='informational'?{defer_allowed:false}:{})}))};
  return {product_id:'fur',revision,catalog_fingerprint:'catalog-'+revision,roles,options:product.options,product};
}

test('classification updates the open item, preserves money and blocks unchanged older draft rows until explicit review',async()=>{
  api.inventoryOptionRoles.mockResolvedValue(notePolicy());api.saveInventoryOptionRoles.mockResolvedValue(notePolicy(1));
  await render();await configureFur('فاطمة');await input('ملاحظة الصنف · البند 1','ملاحظة التغليف الأولى');await click('حفظ الصنف في المسودة');
  const oldLine=JSON.parse(JSON.stringify(persisted().form.lines[0]));
  await configureFur('عبير');await input('ملاحظة الصنف · البند 1','ملاحظة التغليف الثانية');
  await input('موقع تسجيل الشراء 1 — فروة','shelf');await input('ضريبة البند — فروة','25.50');
  await click('تصنيف الخيارات');await input('تصنيف ملاحظة الصنف','informational');await click('حفظ التصنيف');
  const current=persisted().editor;
  expect(current).toMatchObject({quantity:'2',unit_price:'85',tax:'25.50',location_id:'shelf',inventory_option_policy_revision:1});
  expect(current.purchase_configuration.inputs).toEqual([{option_id:'name',value:'عبير'}]);
  expect(persisted().form.lines[0]).toEqual(oldLine);
  expect(rows()[0].textContent).toContain('تغيّر تصنيف الخيارات');
  await click('حفظ الصنف في المسودة');await click('مراجعة الفاتورة');
  expect(host.querySelector('.mz-invoice-review')).toBeNull();expect(onApprove).not.toHaveBeenCalled();
  await click('تعديل فروة 2');
  expect(persisted().editor.purchase_configuration.inputs).toEqual([{option_id:'name',value:'فاطمة'}]);
  await click('حفظ الصنف في المسودة');await click('مراجعة الفاتورة');await click('اعتماد الفاتورة');
  expect(onApprove).toHaveBeenCalledTimes(1);
  expect(persisted().form.lines.map(line=>line.inventory_option_policy_revision)).toEqual([1,1]);
  expect(api.saveInventoryPurchase).not.toHaveBeenCalled();expect(api.saveInventoryPurchaseReceipt).not.toHaveBeenCalled();
});

test('a restored older draft is reviewed against the current catalog without silently rewriting its fields',async()=>{
  await render();await configureFur();await input('ملاحظة الصنف · البند 1','تعليمات جانبية');await click('حفظ الصنف في المسودة');
  const before=JSON.parse(JSON.stringify(persisted().form.lines));
  await act(async()=>root.unmount());root=createRoot(host);
  await render({restore:true,catalogItems:[notePolicy(1).product,necklace,component]});
  expect(persisted().form.lines).toEqual(before);
  await click('مراجعة الفاتورة');expect(host.querySelector('.mz-invoice-review')).toBeNull();
  await click('تعديل فروة 1');expect(persisted().editor.purchase_configuration.inputs).toEqual([{option_id:'name',value:'فاطمة'}]);
  await click('حفظ الصنف في المسودة');await click('مراجعة الفاتورة');expect(host.querySelector('.mz-invoice-review')).not.toBeNull();
});

test('staff purchase entry does not expose the owner option-classification mutation',async()=>{
  await render({sessionContext:{...context,can_manage_inventory_opening:false}});await configureFur();
  expect(button('تصنيف الخيارات')).toBeUndefined();expect(api.inventoryOptionRoles).not.toHaveBeenCalled();
});

test('an older dirty-navigation prompt cannot unmount an open classification editor or leave the draft locked',async()=>{
  api.inventoryOptionRoles.mockResolvedValue(notePolicy());
  await render();await configureFur();await click('مكوّن');
  expect(host.querySelector('.mz-invoice-guard')).not.toBeNull();
  await click('تصنيف الخيارات');
  expect(button('تجاهل التعديل ومتابعة').disabled).toBe(true);
  await click('تجاهل التعديل ومتابعة');expect(persisted().editor.id).toBe('fur');
  await click('إلغاء التصنيف');await click('تجاهل التعديل ومتابعة');
  expect(persisted().editor).toBeNull();expect(button('مكوّن').getAttribute('aria-pressed')).toBe('true');
  await click('منتج');await addNecklace();expect(rows()).toHaveLength(1);
});

test('a product with incomplete cached options can open classification and apply the returned complete catalog',async()=>{
  const incomplete={...fur,options:[],configuration_issues:['خيارات المنتج غير مكتملة في ميزان 2']};
  api.inventoryOptionRoles.mockResolvedValue(notePolicy());api.saveInventoryOptionRoles.mockResolvedValue(notePolicy(1));
  await render({catalogItems:[incomplete]});await click('اختيار فروة');
  await input('الكمية — فروة','8');await input('سعر الوحدة قبل الضريبة — فروة','37.50');
  expect(button('تصنيف الخيارات')).toBeDefined();
  await click('تصنيف الخيارات');await input('تصنيف ملاحظة الصنف','informational');await click('حفظ التصنيف');
  expect(persisted().editor.configuration_issues).toEqual([]);
  expect(persisted().editor).toMatchObject({quantity:'8',unit_price:'37.50',inventory_option_policy_revision:1});
  expect(host.querySelector('[aria-label="المقاس: سنتان"]')).not.toBeNull();
  expect(api.saveInventoryPurchase).not.toHaveBeenCalled();
});
