import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import NativePurchaseReview from './NativePurchaseReview';
import {operationalApi as api, requestId} from './api';

jest.mock('./api',()=>({operationalApi:{supplierInvoiceReview:jest.fn(),nativePurchaseCandidates:jest.fn(),
  previewNativePurchaseLink:jest.fn(),approveNativePurchaseLink:jest.fn()},requestId:jest.fn(),messageFor:()=> 'تعذر إتمام المطابقة'}));
const SCOPE='owner-scope', HASH='a'.repeat(64), storageKey=`mezan-native-purchase-link:${SCOPE}`;
let host,root;
const render=async(props={})=>act(async()=>root.render(<NativePurchaseReview key={props.sessionScope||SCOPE} sessionScope={SCOPE} {...props}/>));
const button=text=>[...host.querySelectorAll('button')].find(b=>b.textContent===text);
const click=async text=>act(async()=>{expect(button(text)).toBeDefined();button(text).click();});
const input=label=>host.querySelector(`[aria-label="${label}"]`);
const set=async(label,value)=>act(async()=>{const el=input(label);expect(el).not.toBeNull();
  Object.getOwnPropertyDescriptor(el.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(el,value);
  el.dispatchEvent(new Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));});
const check=async label=>act(async()=>input(label).click());
function candidate(){return {session_scope:SCOPE,read_only:true,invoice_id:'invoice1',supplier_name:'مورد تجريبي',
  source:{source_verified:true,native_invoice_gross_sar:'80.00',native_settled_sar:'25.00',native_outstanding_sar:'55.00',
    payment_journals:[{payment_operation_id:'pay1',amount_sar:'25.00',bank_account_id:'bank1'}]},
  existing_obligations:[{id:'old-obligation',confirmed:'80.00'}],existing_movements:[{id:'old-payment',amount:'25.00',bank_id:'bank1'}]};}
function preview(extra={}){return {session_scope:SCOPE,read_only:true,invoice_id:'invoice1',can_approve:true,preview_hash:HASH,
  before:{supplier_outstanding_sar:'0.00',banks:{bank1:'1000.00'}},after:{supplier_outstanding_sar:'55.00',banks:{bank1:'975.00'}},
  supplier_difference_sar:'55.00',bank_differences_sar:{bank1:'-25.00'},unrecorded_money_confirmation_required:true,...extra};}
function approval(body){return {ok:true,invoice_id:'invoice1',request_id:body.request_id,supplier_difference_sar:'55.00',
  actual_bank_transfer_executed:false,native_ledger_changed:false};}
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
  jest.resetAllMocks();sessionStorage.clear();requestId.mockReturnValue('one-intent');
  api.supplierInvoiceReview.mockResolvedValue({session_scope:SCOPE,read_only:true,native_purchase_review:[{invoice_id:'invoice1',source_declared_gross_sar:'80.00',supplier_id:'supplier1'}]});
  api.nativePurchaseCandidates.mockResolvedValue(candidate());api.previewNativePurchaseLink.mockResolvedValue(preview());
  api.approveNativePurchaseLink.mockImplementation(body=>Promise.resolve(approval(body)));
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
async function choose(){await render();await click('مطابقة فواتير الشراء والسداد');await set('فاتورة الشراء للمطابقة','invoice1');}
async function newPreview(){await choose();await set('طريقة ربط مستحق الفاتورة','new');await set('سداد pay1','__new__');
  await set('مرجع مراجعة المطابقة','invoice + bank proof');await click('معاينة الفرق قبل الاعتماد');}
async function approveNew(){await newPreview();await check('تأكيد هويات المطابقة');await check('تأكيد المبالغ غير المسجلة');await click('اعتماد الفرق التشغيلي المعروض');}

test('read-only preview shows exact before/after and requires two explicit confirmations',async()=>{
  await render();expect(api.supplierInvoiceReview).not.toHaveBeenCalled();await newPreview();
  expect(api.approveNativePurchaseLink).not.toHaveBeenCalled();expect(host.textContent).toContain('1,000.00');expect(host.textContent).toContain('975.00');
  expect(button('اعتماد الفرق التشغيلي المعروض').disabled).toBe(true);await check('تأكيد هويات المطابقة');
  expect(button('اعتماد الفرق التشغيلي المعروض').disabled).toBe(true);await check('تأكيد المبالغ غير المسجلة');await click('اعتماد الفرق التشغيلي المعروض');
  expect(api.approveNativePurchaseLink).toHaveBeenCalledTimes(1);const body=api.approveNativePurchaseLink.mock.calls[0][0];
  expect(body).toEqual({request_id:'one-intent',selection:{invoice_id:'invoice1',existing_obligation_ids:[],payment_links:[{payment_operation_id:'pay1',movement_id:null}],evidence_ref:'invoice + bank proof'},expected_preview_hash:HASH,expected_session_scope:SCOPE,confirm_reviewed_identities:true,confirm_unrecorded_money:true});
  expect(body).not.toHaveProperty('amount');expect(sessionStorage.getItem(storageKey)).toBeNull();expect(host.textContent).toContain('لم يُنفذ تحويل بنكي جديد');
});

test('existing debt and payment are deliberately chosen and zero differences stay zero',async()=>{
  api.previewNativePurchaseLink.mockResolvedValue(preview({before:{supplier_outstanding_sar:'55.00',banks:{bank1:'975.00'}},after:{supplier_outstanding_sar:'55.00',banks:{bank1:'975.00'}},supplier_difference_sar:'0.00',bank_differences_sar:{bank1:'0.00'},unrecorded_money_confirmation_required:false}));
  await choose();await set('طريقة ربط مستحق الفاتورة','existing');
  await act(async()=>host.querySelector('input[type="checkbox"]').click());await set('سداد pay1','old-payment');await set('مرجع مراجعة المطابقة','exact saved documents');await click('معاينة الفرق قبل الاعتماد');
  expect(api.previewNativePurchaseLink).toHaveBeenCalledWith(expect.objectContaining({existing_obligation_ids:['old-obligation'],payment_links:[{payment_operation_id:'pay1',movement_id:'old-payment'}]}));
  expect(input('تأكيد المبالغ غير المسجلة')).toBeNull();await check('تأكيد هويات المطابقة');await click('اعتماد الفرق التشغيلي المعروض');expect(api.approveNativePurchaseLink).toHaveBeenCalledTimes(1);
});

test('selection starts unassigned and cannot infer identity by equal values',async()=>{
  await choose();expect(input('طريقة ربط مستحق الفاتورة').value).toBe('');expect(input('سداد pay1').value).toBe('');
  expect(button('معاينة الفرق قبل الاعتماد').disabled).toBe(true);expect(api.previewNativePurchaseLink).not.toHaveBeenCalled();
});

test('changing evidence invalidates the prior preview and confirmations',async()=>{
  await newPreview();await check('تأكيد هويات المطابقة');await set('مرجع مراجعة المطابقة','new evidence');
  expect(host.querySelector('[aria-label="فروق المطابقة"]')).toBeNull();expect(api.approveNativePurchaseLink).not.toHaveBeenCalled();
});

test('source incompleteness prevents approval without a fabricated zero',async()=>{
  const c=candidate();c.source={...c.source,source_verified:false,native_invoice_gross_sar:null};api.nativePurchaseCandidates.mockResolvedValue(c);
  await choose();expect(host.textContent).toContain('المصدر غير مكتمل');expect(button('معاينة الفرق قبل الاعتماد').disabled).toBe(true);expect(api.approveNativePurchaseLink).not.toHaveBeenCalled();
});

test('blocked preview explains source issue and never offers approval',async()=>{
  api.previewNativePurchaseLink.mockResolvedValue(preview({can_approve:false,issues:['native_link_existing_payment_allocation_required']}));
  await newPreview();expect(host.textContent).toContain('لن نعيد كتابة الحركة القديمة');expect(button('اعتماد الفرق التشغيلي المعروض')).toBeUndefined();
});

test.each(['scope','missing-bank','invalid-amount'])('malformed %s preview is not accepted',async kind=>{
  const p=preview();if(kind==='scope')p.session_scope='other';if(kind==='missing-bank')delete p.bank_differences_sar.bank1;if(kind==='invalid-amount')p.after.supplier_outstanding_sar=null;
  api.previewNativePurchaseLink.mockResolvedValue(p);await newPreview();expect(button('اعتماد الفرق التشغيلي المعروض')).toBeUndefined();expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

test('ambiguous approval retry survives reload with the SAME saved intent',async()=>{
  api.approveNativePurchaseLink.mockRejectedValueOnce(new Error('network')).mockImplementation(body=>Promise.resolve(approval(body)));
  // mockImplementation above retains the queued one-time rejection.
  await approveNew();const first=api.approveNativePurchaseLink.mock.calls[0][0];expect(JSON.parse(sessionStorage.getItem(storageKey))).toEqual(first);
  await act(async()=>root.unmount());root=createRoot(host);await render();await click('مطابقة فواتير الشراء والسداد');await click('إعادة إرسال طلب المطابقة نفسه');
  expect(api.approveNativePurchaseLink.mock.calls[1][0]).toEqual(first);expect(requestId).toHaveBeenCalledTimes(1);expect(sessionStorage.getItem(storageKey)).toBeNull();
});

test('a postcommit 403 stays ambiguous and does not create a second intent',async()=>{
  api.approveNativePurchaseLink.mockRejectedValue({response:{status:403,data:{detail:{code:'native_link_owner_required'}}}});
  await approveNew();expect(sessionStorage.getItem(storageKey)).not.toBeNull();expect(button('إعادة إرسال طلب المطابقة نفسه')).toBeDefined();
});

test('a proven stale preview discards the pending intent and requires fresh inspection',async()=>{
  api.approveNativePurchaseLink.mockRejectedValue({response:{status:409,data:{detail:{code:'native_link_preview_stale'}}}});
  await approveNew();expect(sessionStorage.getItem(storageKey)).toBeNull();expect(button('اعتماد الفرق التشغيلي المعروض')).toBeUndefined();
});

test('two fast approval clicks send one request and retain intent until acknowledgement',async()=>{
  let resolve;api.approveNativePurchaseLink.mockImplementation(()=>new Promise(r=>resolve=r));await newPreview();await check('تأكيد هويات المطابقة');await check('تأكيد المبالغ غير المسجلة');
  const b=button('اعتماد الفرق التشغيلي المعروض');await act(async()=>{b.click();b.click();});expect(api.approveNativePurchaseLink).toHaveBeenCalledTimes(1);
  await act(async()=>resolve(approval(api.approveNativePurchaseLink.mock.calls[0][0])));expect(sessionStorage.getItem(storageKey)).toBeNull();
});

test('corrupt stored request blocks new approval instead of silently losing pending money',async()=>{
  sessionStorage.setItem(storageKey,'{corrupt');await render();await click('مطابقة فواتير الشراء والسداد');
  expect(host.textContent).toContain('لا تمسح الطلب');expect(api.supplierInvoiceReview).not.toHaveBeenCalled();expect(api.approveNativePurchaseLink).not.toHaveBeenCalled();
});

test('late data from a different authenticated scope is not displayed',async()=>{
  let resolve;api.supplierInvoiceReview.mockImplementation(()=>new Promise(r=>resolve=r));await render();await click('مطابقة فواتير الشراء والسداد');
  await render({sessionScope:'other-owner'});await act(async()=>resolve({session_scope:SCOPE,read_only:true,native_purchase_review:[{invoice_id:'private-invoice'}]}));
  expect(host.textContent).not.toContain('private-invoice');expect(input('فاتورة الشراء للمطابقة')).toBeNull();
});

test('mismatched success acknowledgement retains the request for safe replay',async()=>{
  api.approveNativePurchaseLink.mockImplementation(body=>Promise.resolve({...approval(body),request_id:'other-request'}));await approveNew();
  expect(sessionStorage.getItem(storageKey)).not.toBeNull();expect(host.textContent).not.toContain('تم اعتماد المطابقة التشغيلية');
});

test('empty list is explicit and does not display zero obligations as matched',async()=>{
  api.supplierInvoiceReview.mockResolvedValue({session_scope:SCOPE,read_only:true,native_purchase_review:[]});await render();await click('مطابقة فواتير الشراء والسداد');
  expect(host.textContent).toContain('لا توجد فواتير شراء');expect(api.approveNativePurchaseLink).not.toHaveBeenCalled();
});
