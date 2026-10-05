import React, { useEffect, useRef, useState } from 'react';
import { operationalApi as api, requestId, messageFor } from './api';
import { EntityPicker, KindPicker, Field, Notice } from './OpeningBalances';
import AddOperationalEntity from './AddOperationalEntity';
import {readPendingMovement,savePendingMovement,clearPendingMovement} from './pendingMovementStorage';

// Only a recurring estimate may become payable as part of its explicit payment.
// Decimal cents avoid a rounding discrepancy between the displayed availability
// and the server's allocation ceiling.
const decimalCents = value => {
  const parts = /^(\d+)(?:\.(\d{1,2}))?$/.exec(String(value ?? ''));
  return parts ? BigInt(parts[1]) * 100n + BigInt((parts[2] || '').padEnd(2, '0')) : null;
};
const allocatable = obligation => {
  if (obligation.kind === 'recurring' && obligation.available_to_pay != null) return decimalCents(obligation.available_to_pay);
  const confirmed = decimalCents(obligation.outstanding);
  const expected = obligation.kind === 'recurring' ? decimalCents(obligation.expected) : 0n;
  return confirmed == null || expected == null ? null : confirmed + expected;
};
const displayCents = value => `${value / 100n}.${String(value % 100n).padStart(2, '0')}`;


export default function DailyMovements({source='mezan2',canManage=false,storageScope=null,onScopeChanged=()=>{},onSaved=()=>{}}) {
  const empty={direction:'',party_type:'',party_id:'',bank_id:'',source_account_type:'bank_auto',amount:'',currency:'',kind:'payment',order_number:'',note:'',reference:'',actual_fee_amount:''};
  const [recovery] = useState(() => {
    try { return {payload:readPendingMovement(storageScope),error:''}; }
    catch { return {payload:null,error:'تعذر استعادة سجل الحركة المحفوظة. تحقق من تسجيل الدخول وإتاحة التخزين قبل المتابعة.'}; }
  });
  const [pendingPayload,setPendingPayload]=useState(recovery.payload);
  const restoredForm = payload => Object.fromEntries(Object.entries(empty).map(([key,value])=>[key,payload?.[key] ?? value]));
  const [form,setForm]=useState(()=>restoredForm(recovery.payload)),[accounts,setAccounts]=useState([]),[obligations,setObligations]=useState([]),[allocations,setAllocations]=useState(()=>Object.fromEntries((recovery.payload?.allocations||[]).map(a=>[a.obligation_id,a.amount]))),[file,setFile]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(recovery.error),[message,setMessage]=useState(''),[adding,setAdding]=useState(false),[entityVersion,setEntityVersion]=useState(0);
  const pending=useRef(null),receipt=useRef(null),lock=useRef(false),fileInput=useRef(null);
  useEffect(()=>{
    let alive=true;
    Promise.all(['bank','cash','employee_custody'].map(kind=>api.entities(kind).then(r=>r.items.map(item=>({...item,kind}))))).then(rows=>{if(alive)setAccounts(rows.flat());}).catch(e=>{if(alive)setError(messageFor(e));});
    return()=>{alive=false;};
  },[]);
  useEffect(()=>{
    let alive=true;setObligations([]);
    if(!form.party_type||!form.party_id||(form.kind!=='settlement'&&form.party_type!=='operating_expense'))return;
    api.obligations(form.party_type,form.party_id).then(r=>{if(alive)setObligations(r.items||[]);}).catch(e=>{if(alive)setError(messageFor(e));});
    return()=>{alive=false;};
  },[form.party_type,form.party_id,form.kind]);
  const change=patch=>{if(pendingPayload||recovery.error)return;setForm(f=>({...f,...patch}));pending.current=null;setError('');setMessage('');};
  const expenseOrWithdrawal=['operating_expense','owner_withdrawal'].includes(form.party_type);
  const custody=form.party_type==='employee_custody';
  const chooseType=kind=>{
    change({party_type:kind,party_id:'',currency:'',bank_id:'',source_account_type:'bank_auto',kind:kind==='employee_custody'&&form.direction==='incoming'?'collection':'payment',direction:['operating_expense','owner_withdrawal'].includes(kind)?'outgoing':form.direction});
    setAllocations({});setAdding(false);
  };
  const eligible=obligations.filter(o=>o.party_type===form.party_type&&o.party_id===form.party_id&&o.currency===form.currency&&(allocatable(o) ?? 0n)>0n&&o.direction===(form.direction==='incoming'?'receivable':'payable'));
  const availableAccounts=accounts.filter(a=>(!form.currency||a.currency===form.currency)&&(a.kind!=='employee_custody'||(form.party_type==='operating_expense'&&form.kind!=='settlement')));
  const movementKinds=expenseOrWithdrawal?[['payment','دفع'],...(form.party_type==='operating_expense'&&eligible.some(o=>o.kind==='recurring')?[['settlement','تسوية التزام دوري']]:[]),['correction','تصحيح موثق']]:custody?[[form.direction==='incoming'?'collection':'payment',form.direction==='incoming'?'إرجاع عهدة':'تمويل عهدة'],['correction','تصحيح موثق']]:[['payment','دفع'],['collection','تحصيل'],['settlement','تسوية مستحق'],['transfer','تحويل'],['refund','استرداد'],['wallet_funding','تمويل محفظة'],['correction','تصحيح موثق']];
  const submit=async()=>{
    if(lock.current||recovery.error)return;
    let payload=pendingPayload;
    if(!payload){
      if(!form.direction||!form.party_id||(form.kind!=='correction'&&!form.bank_id)||!form.currency||!/^\d+(\.\d{1,2})?$/.test(form.amount)||Number(form.amount)<=0){setError('أكمل الاتجاه والجهة والحساب والمبلغ.');return;}
      if(form.kind==='correction'&&!form.note.trim()){setError('أدخل سبب التصحيح.');return;}
    }
    const selected=(form.kind==='settlement'?eligible:[]).filter(o=>allocations[o.id]).map(o=>({obligation_id:o.id,amount:allocations[o.id]}));
    if(!payload&&selected.some(a=>!/^\d+(\.\d{1,2})?$/.test(a.amount)||Number(a.amount)<=0)){setError('أدخل مبلغ تسوية صحيحًا.');return;}
    lock.current=true;setBusy(true);setError('');
    try{
      if(!payload){
        let existing;
        try {existing=readPendingMovement(storageScope);}
        catch {setError('تعذر قراءة سجل الحركات المحفوظ. لم تُرسل الحركة.');return;}
        if(existing){
          setPendingPayload(existing);setForm(restoredForm(existing));setAllocations(Object.fromEntries((existing.allocations||[]).map(a=>[a.obligation_id,a.amount])));
          setError('توجد حركة محفوظة لم تُحسم نتيجتها. راجعها ثم أعد محاولتها قبل إدخال حركة أخرى.');return;
        }
        if(file&&!receipt.current){
          const fresh=await api.context();
          if(fresh.session_scope!==storageScope){setError('تغيّر نطاق الجلسة؛ لم يُرفع الإيصال.');onScopeChanged();return;}
          receipt.current=await api.receipt(file);
        }
        pending.current ||= requestId();
        payload={...form,bank_id:form.kind==='correction'?null:form.bank_id,source_account_type:form.kind==='correction'?'bank_auto':form.source_account_type,actual_fee_amount:form.kind==='settlement'&&form.party_type==='provider'?(form.actual_fee_amount||'0.00'):'0.00',request_id:pending.current,expected_session_scope:storageScope,source,receipt_id:receipt.current?.id||null,order_number:form.order_number||null,allocations:selected};
        try {savePendingMovement(storageScope,payload);}
        catch {setError('تعذر حفظ الحركة بأمان على هذا الجهاز. لم تُرسل الحركة؛ أتح التخزين ثم أعد المحاولة.');return;}
        setPendingPayload(payload);
      }
      if(payload.expected_session_scope!==storageScope){setError('الحركة المحفوظة لا تطابق نطاق الجلسة؛ يلزم التحقق من نتيجتها قبل المتابعة.');return;}
      const fresh=await api.context();
      if(fresh.session_scope!==storageScope){setError('تغيّر نطاق الجلسة؛ احتفظنا بالحركة في نطاقها الأصلي.');onScopeChanged();return;}
      await api.movement(payload);
      try {clearPendingMovement(storageScope);}
      catch {setError('تم تأكيد الحركة لكن تعذر تحديث سجل الجهاز. أعد محاولة الحركة نفسها لإكمال التحقق.');return;}
      setPendingPayload(null);setForm(empty);setFile(null);setAllocations({});setObligations([]);if(fileInput.current)fileInput.current.value='';pending.current=null;receipt.current=null;setMessage('تم حفظ الحركة');onSaved();
    }catch(e){
      if(e?.response?.data?.detail?.not_applied===true||([400,422].includes(e?.response?.status)&&e?.response?.data?.detail?.not_applied!==false)){
        try {clearPendingMovement(storageScope);setPendingPayload(null);pending.current=null;}
        catch {setError('تعذر تحديث سجل الجهاز. احتفظنا بالحركة دون تغيير حتى يمكن التحقق.');return;}
      }
      setError(messageFor(e));
    }finally{setBusy(false);lock.current=false;}
  };
  return <section className="op-card"><h2>الحركات المالية اليومية</h2><p className="op-muted">سجّل الحركة وأرفق الإيصال عند الحاجة.</p><Notice error={error} message={message}/>{pendingPayload&&<p role="status" className="op-muted">هناك حركة محفوظة لم تُحسم نتيجتها. المدخلات مقفلة؛ إعادة المحاولة ترسل الحركة نفسها دون تكرار أثرها.</p>}<fieldset disabled={busy||Boolean(pendingPayload)||Boolean(recovery.error)}>
    <Field label="اتجاه الحركة"><span className="op-segment">{[['incoming','وارد'],['outgoing','صادر']].map(([value,label])=><button key={value} disabled={expenseOrWithdrawal&&form.kind!=='correction'&&value==='incoming'} aria-pressed={form.direction===value} onClick={()=>{change({direction:value,...(custody&&form.kind!=='correction'?{kind:value==='incoming'?'collection':'payment'}:{})});setAllocations({});}}>{label}</button>)}</span></Field>
    <KindPicker value={form.party_type} onChange={chooseType}/>
    <EntityPicker key={entityVersion} kind={form.party_type} value={form.party_id} onChange={(id,item)=>{change({party_id:id,currency:item?.currency||'',bank_id:'',source_account_type:'bank_auto'});setAllocations({});}}/>
    {canManage&&['external_person','operating_expense','cash'].includes(form.party_type)&&<button type="button" className="op-link" onClick={()=>setAdding(true)}>+ إضافة {form.party_type==='external_person'?'جهة خارجية':form.party_type==='cash'?'صندوق':'نوع مصروف تشغيلي'}</button>}
    {adding&&<AddOperationalEntity kind={form.party_type} onCancel={()=>setAdding(false)} onAdded={row=>{change({party_id:row.id,currency:row.currency});setAdding(false);setEntityVersion(v=>v+1);if(row.kind==='cash')setAccounts(a=>[...a,row]);}}/>}
    <Field label={form.party_type==='operating_expense'?'مصدر الصرف: بنك أو صندوق أو عهدة':'البنك أو الصندوق'}><select disabled={form.kind==='correction'} value={form.bank_id?`${form.source_account_type}:${form.bank_id}`:''} onChange={e=>{const chosen=availableAccounts.find(a=>`${a.kind}:${a.id}`===e.target.value);change({bank_id:chosen?.id||'',source_account_type:chosen?.kind||'bank_auto'});}}><option value="">اختر الحساب</option>{availableAccounts.map(a=><option key={`${a.kind}:${a.id}`} value={`${a.kind}:${a.id}`}>{a.kind==='employee_custody'?'عهدة · ':''}{a.name}</option>)}</select></Field>
    {form.source_account_type==='employee_custody'&&<p className="op-muted">يُخصم المبلغ من العهدة، دون خروج جديد من البنك.</p>}
    <Field label="نوع الحركة"><select value={form.kind} onChange={e=>{change({kind:e.target.value,...((e.target.value==='correction'||(e.target.value==='settlement'&&form.source_account_type==='employee_custody'))?{bank_id:'',source_account_type:'bank_auto'}:{})});setAllocations({});}}>{movementKinds.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></Field>
    <Field label={`المبلغ${form.currency?' · '+form.currency:''}`}><input inputMode="decimal" value={form.amount} onChange={e=>change({amount:e.target.value})}/></Field>
    {form.kind==='settlement'&&<div><h3>المستحقات المراد تسويتها</h3>{eligible.length?eligible.map((o,index)=><Field key={o.id} label={`${o.label||o.name||'مستحق '+(index+1)} · ${o.kind==='recurring'&&(decimalCents(o.expected)??0n)>0n?'التزام دوري تقديري — يؤكد الجزء المدفوع عند الحفظ · المتاح '+displayCents(allocatable(o)):'المتبقي المؤكد '+o.outstanding} ${o.currency}`}><input inputMode="decimal" value={allocations[o.id]||''} onChange={e=>{setAllocations(a=>({...a,[o.id]:e.target.value}));pending.current=null;}}/></Field>):<p>لا توجد مستحقات متاحة للتسوية.</p>}</div>}
    {form.kind==='settlement'&&form.party_type==='provider'&&<Field label="العمولة الفعلية المقتطعة من التسوية"><input inputMode="decimal" value={form.actual_fee_amount} onChange={e=>change({actual_fee_amount:e.target.value})}/></Field>}
    <Field label="رقم الطلب (اختياري)"><input value={form.order_number} onChange={e=>change({order_number:e.target.value})}/></Field>
    <Field label="مرجع الحركة"><input value={form.reference} onChange={e=>change({reference:e.target.value})}/></Field>
    <Field label="الإيصال"><input ref={fileInput} type="file" accept="image/jpeg,image/png,application/pdf" onChange={e=>{setFile(e.target.files[0]||null);receipt.current=null;pending.current=null;}}/></Field>
    <Field label="ملاحظة"><textarea value={form.note} onChange={e=>change({note:e.target.value})}/></Field>
  </fieldset><button className="op-primary" disabled={busy||Boolean(recovery.error)} onClick={submit}>{busy?'جارٍ الحفظ…':pendingPayload?'إعادة محاولة الحركة':'حفظ الحركة'}</button></section>;
}
