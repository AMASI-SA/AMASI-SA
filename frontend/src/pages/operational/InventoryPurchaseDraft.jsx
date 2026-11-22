import React, {useEffect, useRef, useState} from 'react';
import {Check, PencilSimple, Trash} from '@phosphor-icons/react';
import {Field, Notice} from './OpeningBalances';
import {operationalApi as api, requestId, messageFor} from './api';
import {InventoryProductPicker, InventoryProductPhoto} from '../../components/inventory/suite/InventoryProductFields';
import {inventoryConfiguration} from '../../lib/inventoryProductConfiguration';
import PurchaseConfiguration from './PurchaseConfiguration';
import {inventoryIdentity} from './inventoryIdentity';
import {assignedQuantity} from './inventoryPersonalizations';
import {makePurchaseCommand, purchaseLinePayload, purchaseLineFingerprint, purchaseTotals, purchaseMoney, purchaseSettings, purchaseToday} from './inventoryPurchaseWorkflow';
import './inventoryPurchaseDraft.css';

export function readPurchaseDraft(key) {
  try {
    const saved = JSON.parse(localStorage.getItem(key) || 'null');
    return saved?.version === 1 && Array.isArray(saved.form?.lines) ? saved : null;
  } catch { return null; }
}
export function draftDescription(line) {
  const config = line.purchase_configuration;
  if (!config) return [line.variant_name,...(line.personalizations||[]).map(row=>(row.values||[]).map(value=>(line.customization_fields?.find(field=>field.id===value.option_id)?.name||'تخصيص')+': '+value.value).join(' · ')+' × '+row.quantity)].filter(Boolean).join(' · ') || 'بالمواصفات المحددة';
  const labels = line.purchase_option_labels || [
    ...(config.selections || []).map(selection => {
      const option = line.options?.find(item => item.id === selection.option_id);
      return {name:option?.name || selection.option_id,value:option?.values?.find(item => item.id === selection.value_id)?.name || ''};
    }),
    ...(config.inputs || []).map(input => ({name:line.options?.find(item => item.id === input.option_id)?.name || input.option_id,value:input.value})),
  ];
  const deferred = (config.deferred_option_ids || []).map(id => (line.options?.find(item => item.id === id)?.name || id)+': عند طلب العميل');
  return [...labels.map(item => item.name+': '+item.value),...deferred,
    ...(!deferred.length && config.state === 'raw' ? ['التخصيص عند طلب العميل'] : [])].join(' · ') || 'بالمواصفات المحددة';
}

export default function InventoryPurchaseDraft({form,onChange,catalog,locations,suppliers,disabled,context,draftKey,onApprove,initialDraft}) {
  const [editor,setEditor] = useState(initialDraft?.editor || null);
  const [kind,setKind] = useState(initialDraft?.editor?.kind || 'product'), [search,setSearch] = useState('');
  const [settings,setSettings] = useState({...purchaseSettings(),...initialDraft?.settings});
  const [error,setError] = useState(''), [storageError,setStorageError] = useState(''), [message,setMessage] = useState('');
  const [guard,setGuard] = useState(null), [review,setReview] = useState(false), [undo,setUndo] = useState(null);
  const [accounts,setAccounts] = useState([]), [candidates,setCandidates] = useState([]), [paymentLoading,setPaymentLoading] = useState(false);
  const [excludedPayments,setExcludedPayments] = useState([]);
  const editorRef = useRef(null), ids = useRef(0);
  const owner = context.can_manage_inventory_opening === true;
  const initialEditor = useRef(editor ? JSON.stringify(initialDraft?.form?.lines?.find(line=>line.draft_key===editor.draft_key) || null) : '');
  const dirty = editor && JSON.stringify(editor) !== initialEditor.current;
  let total = null; try { total = purchaseTotals(form.lines); } catch {}
  const setForm = patch => {onChange({...form,...patch});setReview(false);};
  const setOption = patch => {setSettings(current => ({...current,...patch}));setReview(false);};
  useEffect(() => {
    if (disabled) return;
    try {localStorage.setItem(draftKey,JSON.stringify({version:1,form,editor,settings}));setStorageError('');}
    catch {setStorageError('تعذر حفظ المسودة على هذا الجهاز. احتفظ بالصفحة مفتوحة حتى ينجح الحفظ.');}
  }, [draftKey,form,editor,settings,disabled]);
  useEffect(() => {
    if (!dirty) return;
    const warn = event => {event.preventDefault();event.returnValue='';};
    window.addEventListener('beforeunload',warn);
    return () => window.removeEventListener('beforeunload',warn);
  }, [dirty]);
  useEffect(() => {
    setCandidates([]);setExcludedPayments([]);setError('');setPaymentLoading(false);
    if (settings.payment_mode === 'credit' || disabled) return;
    if (settings.payment_mode === 'existing' && (!owner || !form.supplier_id)) return;
    const controller = new AbortController();let active = true;
    setPaymentLoading(true);
    const call = settings.payment_mode === 'existing'
      ? api.inventoryPurchasePaymentCandidates(form.supplier_id,{signal:controller.signal}).then(result => {if(active){setCandidates(result.items || []);setExcludedPayments(result.excluded || []);}})
      : Promise.all(['bank','cash','employee_custody'].map(accountKind => api.entities(accountKind,{signal:controller.signal}).then(result => result.items.map(item => ({...item,kind:accountKind}))))).then(result => {if(active)setAccounts(result.flat());});
    call.catch(reason => {if(active)setError(messageFor(reason));}).finally(() => {if(active)setPaymentLoading(false);});
    return () => {active=false;controller.abort();};
  }, [settings.payment_mode,form.supplier_id,disabled,owner]);
  function focusEditor() {requestAnimationFrame(() => {editorRef.current?.scrollIntoView?.({block:'start',behavior:'smooth'});editorRef.current?.focus?.();});}
  function open(item) {
    setReview(false);setError('');setMessage('');
    const line = item.draft_key ? {...item} : {...item,item_id:item.id,quantity:'1',unit_price:'',tax:'0',location_id:'',
      draft_key:requestId()+':'+(++ids.current),...(Array.isArray(item.options)?{purchase_configuration:inventoryConfiguration(item)}:{})};
    setEditor(line);initialEditor.current=JSON.stringify(line);setKind(line.kind);focusEditor();
  }
  function navigate(action) {if(dirty){setGuard({action});return;}action();}
  function saveLine() {
    if (!editor) return false;
    try {
      purchaseLinePayload(editor);
      const duplicate = form.lines.find(line => line.draft_key !== editor.draft_key && purchaseLineFingerprint(line) === purchaseLineFingerprint(editor));
      if (duplicate) throw Error('الصنف بالمواصفات نفسها موجود في المسودة؛ افتحه وعدّل كميته.');
      const existing = form.lines.some(line => line.draft_key === editor.draft_key);
      setForm({lines:existing?form.lines.map(line => line.draft_key===editor.draft_key?editor:line):[editor,...form.lines]});
      setEditor(null);initialEditor.current='';setSearch('');setGuard(null);setError('');
      setMessage(existing?'تم تحديث الصنف في مكانه داخل المسودة.':'أُضيف الصنف إلى أول المسودة. يمكنك إضافة الصنف التالي.');
      focusEditor();return true;
    } catch(reason) {setError(reason.message);return false;}
  }
  function checkInvoice() {
    if (editor) {setError('احفظ الصنف المفتوح في المسودة أو ألغِ إدخاله أولًا.');focusEditor();return;}
    try {makePurchaseCommand(form,settings,context.session_scope,accounts,candidates,()=> 'review-only');setError('');setReview(true);}
    catch(reason) {setError(reason.message);}
  }
  return <>
    <fieldset className="op-card mz-invoice-draft" disabled={disabled}>
      <legend>فاتورة شراء المخزون</legend>
      <div className="mz-invoice-meta">
        <Field label="المورد"><select value={form.supplier_id} onChange={event => {setForm({supplier_id:event.target.value});setOption({movement_id:''});}}><option value="">اختر المورد</option>{suppliers.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
        <Field label="رقم فاتورة المورد"><input value={form.invoice_number} onChange={event => setForm({invoice_number:event.target.value})}/></Field>
        <Field label="تاريخ الفاتورة"><input type="date" max={purchaseToday()} value={form.invoice_date} onChange={event => setForm({invoice_date:event.target.value})}/></Field>
      </div>
      <Notice error={error || storageError} message={message}/>
      <section className="mz-invoice-editor" tabIndex="-1" ref={editorRef} aria-label="إضافة صنف إلى مسودة الفاتورة">
        <header><h3>{editor?'مواصفات الصنف':'إضافة صنف للفاتورة'}</h3><span>صنف واحد في كل مرة</span></header>
        <div className="mz-invoice-kinds" aria-label="نوع المخزون">{[['product','منتج'],['component','مكوّن']].map(([value,label]) => <button type="button" key={value} aria-label={label} aria-pressed={kind===value} className={kind===value?'is-selected':''} onClick={() => navigate(() => {setKind(value);setEditor(null);setSearch('');initialEditor.current='';})}>{kind===value&&<Check size={17} aria-hidden="true"/>}{label}</button>)}</div>
        {!editor ? <InventoryProductPicker items={catalog.filter(item=>item.kind===kind).map(item=>({...item,picker_id:inventoryIdentity(item)}))} query={search} onQueryChange={setSearch} label="بحث بالاسم أو الرمز" initialOpen optionClassName="op-tile" onPick={open}/> : <>
          <div className="mz-product-selected"><InventoryProductPhoto product={editor}/><div><h3>{editor.name}</h3><span><Check size={14} aria-hidden="true"/> مختار · <bdi>{editor.sku || 'غير محدد'}</bdi> · {editor.unit==='piece'?'قطعة':editor.unit}</span></div><button type="button" onClick={()=>navigate(()=>{setEditor(null);initialEditor.current='';})}>تغيير الصنف</button></div>
          <PurchaseConfiguration line={editor} index={0} locations={locations} locationOptional onChange={(key,value)=>{setEditor(current=>({...current,[key]:value}));setReview(false);}}/>
          {!!editor.customization_issues?.length&&<p role="alert">خيارات المنتج غير مكتملة في ميزان 2؛ يلزم استكمال تعريف الخيارات قبل تخصيصها.</p>}
          {!editor.purchase_configuration&&editor.kind==='product'&&!!editor.customization_fields?.length&&!editor.customization_issues?.length&&<LegacyPersonalizations line={editor} onChange={setEditor}/>}
          <div className="mz-invoice-meta">
            <Field label={'الكمية — '+editor.name}><input type="number" min="1" max="100000" step="1" value={editor.quantity} onChange={event=>setEditor({...editor,quantity:event.target.value})}/></Field>
            <Field label={'سعر الوحدة قبل الضريبة — '+editor.name}><input inputMode="decimal" value={editor.unit_price} onChange={event=>setEditor({...editor,unit_price:event.target.value})}/></Field>
            <Field label={'ضريبة البند — '+editor.name}><input inputMode="decimal" value={editor.tax} onChange={event=>setEditor({...editor,tax:event.target.value})}/></Field>
          </div>
          <div className="mz-invoice-actions"><button type="button" className="op-primary" onClick={saveLine}><Check size={17}/>حفظ الصنف في المسودة</button><button type="button" onClick={()=>navigate(()=>{setEditor(null);initialEditor.current='';})}>إلغاء إدخال الصنف</button></div>
        </>}
      </section>
      {guard&&<section className="mz-invoice-guard" role="alert"><strong>توجد تعديلات على الصنف المفتوح.</strong><div className="mz-invoice-actions"><button type="button" onClick={()=>{const action=guard.action;if(saveLine())action();}}>حفظ ومتابعة</button><button type="button" onClick={()=>{const action=guard.action;setGuard(null);action();}}>تجاهل التعديل ومتابعة</button><button type="button" onClick={()=>setGuard(null)}>البقاء في الصنف</button></div></section>}
      <section className="mz-invoice-lines" aria-label="مسودة الفاتورة"><header><h3>مسودة الفاتورة <span>{form.lines.length} أصناف</span></h3><small>الأحدث في البداية · يمكنك فتح أي صنف لتعديله</small></header>
        {!form.lines.length&&<p className="mz-invoice-empty">أكمل مواصفات الصنف ثم احفظه هنا.</p>}
        {form.lines.map((line,index)=><article className="mz-invoice-row" key={line.draft_key}><InventoryProductPhoto product={line}/><div className="mz-invoice-row-details"><strong>{line.name}</strong><p>{draftDescription(line)}</p><small>{line.quantity} × {line.unit_price} ر.س{line.location_id?' · '+(locations.find(item=>item.id===line.location_id)?.name || 'موقع محدد'):''}</small></div><strong className="mz-invoice-row-total">{purchaseMoney(Number(line.quantity)*purchaseCentsSafe(line.unit_price)+purchaseCentsSafe(line.tax))} ر.س</strong><div className="mz-invoice-row-actions"><button type="button" aria-label={'تعديل '+line.name+' '+(index+1)} onClick={()=>navigate(()=>open(line))}><PencilSimple size={17}/>تعديل</button><button type="button" aria-label={'حذف '+line.name+' '+(index+1)} onClick={()=>{if(editor?.draft_key===line.draft_key){setError('أغلق تعديل الصنف قبل حذفه.');return;}setUndo({line,index});setForm({lines:form.lines.filter(item=>item.draft_key!==line.draft_key)});}}><Trash size={17}/></button></div></article>)}
        {undo&&<p role="status">حُذف {undo.line.name}. <button type="button" onClick={()=>{const lines=[...form.lines];lines.splice(undo.index,0,undo.line);setForm({lines});setUndo(null);}}>تراجع عن الحذف</button></p>}
      </section>
      <section className="mz-invoice-settings"><h3>استلام البضاعة</h3>
        <div className="mz-invoice-segments">{[['none','لم أستلم بعد'],['partial','استلمت جزءًا'],['all','استلمت الكل']].map(([value,label])=><label key={value} className={settings.receipt_mode===value?'is-selected':''}><input type="radio" name="invoice-receipt" checked={settings.receipt_mode===value} disabled={value!=='none'&&!owner} onChange={()=>setOption({receipt_mode:value})}/>{label}</label>)}</div>
        {settings.receipt_mode==='none'?<p>ستظهر الفاتورة في «بانتظار الاستلام». عند وصول البضاعة افتحها واضغط «تسجيل استلام».</p>:<><Field label="تاريخ استلام البضاعة"><input type="date" min={form.invoice_date} max={purchaseToday()} value={settings.receipt_date} onChange={event=>setOption({receipt_date:event.target.value})}/></Field>{settings.receipt_mode==='partial'&&form.lines.map(line=><div className="mz-invoice-receipt-line" key={line.draft_key}><span>{line.name} · المشتراة {line.quantity}</span><Field label={'المستلم الآن — '+line.name}><input type="number" min="0" max={line.quantity} step="1" value={settings.receipt_quantities[line.draft_key] || ''} onChange={event=>setOption({receipt_quantities:{...settings.receipt_quantities,[line.draft_key]:event.target.value}})}/></Field></div>)}<p>تضاف الكميات المستلمة فقط إلى المخزون في المواقع المحددة لكل صنف.</p></>}
      </section>
      <section className="mz-invoice-settings"><h3>سداد المورد</h3>
        <div className="mz-invoice-segments">{[['credit','آجل — لم أسدد'],['full','سددت كامل المبلغ'],['partial','سددت جزءًا'],['existing','ربط دفعة سابقة']].map(([value,label])=><label key={value} className={settings.payment_mode===value?'is-selected':''}><input type="radio" name="invoice-payment" checked={settings.payment_mode===value} disabled={value==='existing'&&!owner} onChange={()=>setOption({payment_mode:value,movement_id:''})}/>{label}</label>)}</div>
        {settings.payment_mode!=='credit'&&<><p>{settings.payment_mode==='existing'?'ربط المبلغ المتاح بالفاتورة فقط؛ لا ينشئ خصمًا جديدًا من البنك أو رصيد المورد.':'سجّل المبلغ المدفوع فعليًا ومصدره؛ هذا التسجيل لا ينفذ تحويلًا بنكيًا.'}</p>{paymentLoading&&<p role="status">جارٍ تحميل مصادر السداد…</p>}<div className="mz-invoice-meta">
          {settings.payment_mode==='existing'?<Field label="الدفعة السابقة"><select value={settings.movement_id} disabled={paymentLoading} onChange={event=>setOption({movement_id:event.target.value})}><option value="">اختر دفعة المورد</option>{candidates.map(item=><option key={item.id} value={item.id}>{item.business_date} · {item.reference || item.bank_name || 'دفعة'} · المتاح {item.available} ر.س</option>)}</select></Field>:<Field label="مصدر السداد"><select value={settings.payment_account} disabled={paymentLoading} onChange={event=>setOption({payment_account:event.target.value})}><option value="">اختر بنكًا أو صندوقًا أو عهدة</option>{accounts.map(item=><option key={item.kind+':'+item.id} value={item.kind+':'+item.id}>{item.name}</option>)}</select></Field>}
          <Field label="مبلغ السداد"><input inputMode="decimal" disabled={settings.payment_mode==='full'} value={settings.payment_mode==='full'?purchaseMoney(total?.gross || 0):settings.payment_amount} onChange={event=>setOption({payment_amount:event.target.value})}/></Field>
          <Field label={settings.payment_mode==='existing'?'تاريخ ربط الدفعة':'تاريخ السداد'}><input type="date" min={[form.invoice_date,settings.payment_mode==='existing'&&candidates.find(item=>item.id===settings.movement_id)?.business_date].filter(Boolean).sort().at(-1)} max={purchaseToday()} value={settings.payment_date} onChange={event=>setOption({payment_date:event.target.value})}/></Field>
        </div>{settings.payment_mode!=='existing'&&<Field label="مرجع السداد الاختياري"><input value={settings.payment_reference} onChange={event=>setOption({payment_reference:event.target.value})}/></Field>}{settings.payment_mode==='existing'&&!paymentLoading&&!candidates.length&&<p>لا توجد دفعة سابقة متاحة لهذا المورد. يمكنك حفظ الفاتورة آجلة وسدادها لاحقًا.</p>}</>}
        {settings.payment_mode==='existing'&&!paymentLoading&&!!excludedPayments.length&&<div className="mz-invoice-payment-exclusions"><p>دفعات تحتاج مراجعة قبل ربطها:</p>{excludedPayments.map(item=><p key={item.reason}>{item.reason} · {item.count} دفعة</p>)}</div>}
      </section>
      <Field label="ملاحظات اختيارية"><textarea rows="2" maxLength="1000" value={form.note} onChange={event=>setForm({note:event.target.value})}/></Field>
      <div className="mz-invoice-footer"><span>{total?'الصافي '+purchaseMoney(total.net)+' · الضريبة '+purchaseMoney(total.tax)+' · الإجمالي '+purchaseMoney(total.gross)+' ريال':'أكمل أسعار الأصناف'}</span><button type="button" className="op-primary" onClick={checkInvoice} disabled={paymentLoading}>مراجعة الفاتورة</button></div>
      {review&&<section className="mz-invoice-review" aria-label="مراجعة الفاتورة قبل الاعتماد"><h3>مراجعة واعتماد الفاتورة</h3><p>{suppliers.find(item=>item.id===form.supplier_id)?.name} · {form.invoice_number} · {form.lines.length} أصناف · {purchaseMoney(total.gross)} ر.س</p><p>{settings.receipt_mode==='none'?'بانتظار الاستلام':settings.receipt_mode==='partial'?'استلام جزئي':'استلام كامل'} · {settings.payment_mode==='credit'?'غير مسددة':settings.payment_mode==='existing'?'ربط دفعة مسجلة سابقًا':'تسجيل السداد المحدد'}</p><p>اعتماد الفاتورة يسجل التزام المورد. الاستلام والسداد ينفذان حسب اختيارك أعلاه.</p><div className="mz-invoice-actions"><button type="button" className="op-primary" onClick={()=>{try{onApprove(makePurchaseCommand(form,settings,context.session_scope,accounts,candidates,requestId));setReview(false);}catch(reason){setError(reason.message);}}}>اعتماد الفاتورة</button><button type="button" onClick={()=>setReview(false)}>العودة للتعديل</button></div></section>}
    </fieldset>
  </>;
}
function purchaseCentsSafe(value) {const number=Number(value);return Number.isFinite(number)?Math.round(number*100):0;}

function LegacyPersonalizations({line,onChange}) {
  const change=(index,patch)=>onChange({...line,personalizations:(line.personalizations||[]).map((row,i)=>i===index?{...row,...patch}:row)});
  return <section aria-label={'تخصيص خيارات — '+line.name}><h4>توزيع الكمية حسب خيارات المنتج</h4><p>الكمية المشتراة {line.quantity} · المخصص {assignedQuantity(line)} · غير المخصص {Math.max(0,Number(line.quantity)-assignedQuantity(line))}</p>
    {(line.personalizations||[]).map((row,index)=><div className="op-card" key={index}>{line.customization_fields.map(field=>{const Control=field.type==='textarea'?'textarea':'input';return <Field key={field.id} label={field.name+(field.required?' *':'')+' '+(index+1)+' — '+line.name}><Control maxLength={field.type==='textarea'?1000:100} value={row.values.find(value=>value.option_id===field.id)?.value||''} onChange={event=>change(index,{values:[...row.values.filter(value=>value.option_id!==field.id),{option_id:field.id,value:event.target.value}]})}/></Field>;})}<Field label={'كمية التخصيص '+(index+1)+' — '+line.name}><input type="number" min="1" step="1" value={row.quantity} onChange={event=>change(index,{quantity:event.target.value})}/></Field><button type="button" onClick={()=>onChange({...line,personalizations:line.personalizations.filter((_,i)=>i!==index)})}>إزالة التخصيص {index+1} — {line.name}</button></div>)}
    <button type="button" disabled={(line.personalizations||[]).length>=100} onClick={()=>onChange({...line,personalizations:[...(line.personalizations||[]),{values:[],quantity:'1'}]})}>إضافة تخصيص — {line.name}</button>
  </section>;
}
