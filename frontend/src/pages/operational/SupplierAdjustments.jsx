import React,{useState,useRef} from 'react';
import {operationalApi as api,requestId,messageFor} from './api';
import {Field,Notice} from './OpeningBalances';
const today=()=>{const d=new Date();return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10);};
export default function SupplierAdjustments({context,supplierId,kind='discount'}){
 const scope=context.session_scope,key=`mezan.operational.supplier-adjustment.v1:${scope}`;
 const [recovery]=useState(()=>{try{const c=JSON.parse(localStorage.getItem(key)||'null');if(c&&(!c.body?.request_id||c.body.expected_session_scope!==scope||!c.invoice_number||!['discount','return'].includes(c.body.kind)))throw Error();return {command:c};}catch{return {error:'تعذر قراءة العملية المحفوظة؛ لا يمكن الإرسال قبل استعادة التخزين.'};}});
 const [pending,setPending]=useState(recovery.command),[invoice,setInvoice]=useState(null),[number,setNumber]=useState(''),[amount,setAmount]=useState(''),[quantities,setQuantities]=useState({}),[reference,setReference]=useState(''),[day,setDay]=useState(today),[accepted,setAccepted]=useState(false),[note,setNote]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(recovery.error||''),[message,setMessage]=useState('');
 const lock=useRef(false),writable=context.status==='active'&&context.permissions?.move===true,disabled=busy||!!pending||!!recovery.error||!writable;
 async function lookup(){if(disabled||!number.trim())return;setBusy(true);setError('');setInvoice(null);setQuantities({});try{setInvoice(await api.supplierAdjustmentEntry(supplierId,number.trim()));}catch(e){setError(messageFor(e));}finally{setBusy(false);}}
 async function save(){if(lock.current||!writable||recovery.error)return;lock.current=true;setBusy(true);setError('');setMessage('');try{
  let command=pending;
  if(!command){
   const stored=JSON.parse(localStorage.getItem(key)||'null');
   if(stored){if(stored.body?.expected_session_scope!==scope||!stored.body?.request_id||!['discount','return'].includes(stored.body.kind)||!stored.invoice_number)throw Error('سجل العملية المحفوظة غير صالح؛ لم تُرسل حركة.');setPending(stored);throw Error('توجد عملية محفوظة؛ راجعها ثم أعد محاولتها بدل إنشاء عملية أخرى.');}
   if(!invoice||!accepted||!reference.trim()||!day)throw Error('أكمل الفاتورة ومرجع وتاريخ قبول المورد، ثم أكد القبول الفعلي.');
   const lines=invoice.lines.filter(l=>Number(quantities[`${l.kind}:${l.item_id}`])>0).map(l=>({kind:l.kind,item_id:l.item_id,quantity:Number(quantities[`${l.kind}:${l.item_id}`])}));
   if(kind==='discount'&&(!/^\d+(\.\d{1,2})?$/.test(amount)||Number(amount)<=0||Number(amount)>Number(invoice.adjusted_gross)))throw Error('أدخل خصمًا ثابتًا لا يتجاوز قيمة الفاتورة المتبقية.');
   if(kind==='return'&&(!lines.length||lines.some(l=>!Number.isSafeInteger(l.quantity)||l.quantity>invoice.lines.find(i=>i.kind===l.kind&&i.item_id===l.item_id).remaining_quantity)))throw Error('اختر كميات صحيحة لا تتجاوز المتبقي من المنتجات.');
   command={invoice_number:invoice.invoice_number,body:{request_id:requestId(),expected_session_scope:scope,invoice_id:invoice.invoice_id,kind,accepted:true,reference:reference.trim(),business_date:day,note,amount:kind==='discount'?amount:null,lines:kind==='return'?lines:[]}};
   localStorage.setItem(key,JSON.stringify(command));setPending(command);
  }
  const fresh=await api.context();if(fresh.session_scope!==scope)throw Error('تغير نطاق الجلسة؛ احتفظنا بالعملية في نطاقها الأصلي.');
  const result=await api.saveSupplierAdjustment(command.body);
  localStorage.removeItem(key);setPending(null);setInvoice(result);setNumber(result.invoice_number);setQuantities({});setAmount('');setReference('');setAccepted(false);setNote('');setMessage('تم تسجيل قبول المورد دون حركة بنك أو صندوق.');
 }catch(e){if(e?.response?.data?.detail?.not_applied===true){try{localStorage.removeItem(key);setPending(null);}catch{}}setError(e?.response?messageFor(e):e.message||messageFor(e));}finally{lock.current=false;setBusy(false);}}
 return <section className="op-card" dir="rtl"><h2>{kind==='discount'?'خصم من المورد':'إرجاع منتجات للمورد'}</h2><p>تخفيض الالتزام بقبول المورد الفعلي، دون صرف أو تحصيل نقدي.</p><Notice error={error} message={message}/>
 {!writable&&<p role="alert">لا تتوفر صلاحية إدخال الحركات.</p>}{busy&&<p role="status">جارٍ تنفيذ العملية…</p>}
 {pending?<div role="status"><p>عملية محفوظة بانتظار التأكيد: {pending.body.kind==='discount'?'خصم':'إرجاع'} · فاتورة {pending.invoice_number} · {pending.body.reference} · {pending.body.business_date}</p>{pending.body.amount&&<p>{pending.body.amount} ريال</p>}<p>إعادة المحاولة ترسل العملية المحفوظة نفسها دون تكرار.</p><button disabled={busy||!writable||!!recovery.error} onClick={save}>إعادة محاولة الحفظ</button></div>:<>
 <fieldset disabled={disabled}><Field label="رقم فاتورة المورد"><input value={number} onChange={e=>{setNumber(e.target.value);setInvoice(null);}}/></Field><button disabled={!number.trim()} onClick={lookup}>عرض الفاتورة</button></fieldset>
 {invoice&&<><p>{invoice.supplier_name} · {invoice.invoice_number} · الإجمالي بعد التخفيضات {invoice.adjusted_gross} ريال · المتبقي {invoice.outstanding} · رصيد لنا {invoice.credit}</p><fieldset disabled={disabled}>
 {kind==='discount'?<Field label="مبلغ الخصم الإجمالي الثابت"><input inputMode="decimal" value={amount} onChange={e=>setAmount(e.target.value)}/></Field>:invoice.lines.map(l=><article key={`${l.kind}:${l.item_id}`} className="op-card">{l.image_url&&<img src={l.image_url} width="64" height="64" alt={l.name||'منتج الفاتورة'}/>}<strong>{l.name||'منتج الفاتورة'}</strong><p>المتاح للإرجاع: {l.remaining_quantity} · قيمة المتبقي {l.remaining_gross} ريال</p><Field label={`كمية الإرجاع — ${l.name||'منتج الفاتورة'}`}><input type="number" min="0" max={l.remaining_quantity} step="1" value={quantities[`${l.kind}:${l.item_id}`]||''} onChange={e=>setQuantities(q=>({...q,[`${l.kind}:${l.item_id}`]:e.target.value}))}/></Field></article>)}
 <Field label="مرجع قبول المورد"><input value={reference} onChange={e=>setReference(e.target.value)}/></Field><Field label="تاريخ القبول"><input type="date" value={day} onChange={e=>setDay(e.target.value)}/></Field><Field label="ملاحظة اختيارية"><textarea value={note} onChange={e=>setNote(e.target.value)}/></Field><label><input type="checkbox" checked={accepted} onChange={e=>setAccepted(e.target.checked)}/> المورد وافق فعليًا على الخصم أو استلم المرتجع وقبله</label><button onClick={save}>حفظ قبول المورد</button></fieldset></>}
 </>}
 </section>;
}
