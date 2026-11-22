import React, {useEffect, useRef, useState} from 'react';
import {operationalApi as api, requestId, messageFor} from './api';
import {Field, Notice} from './OpeningBalances';
import {configurationDescription} from './PurchaseConfiguration';
import {purchaseToday} from './inventoryPurchaseWorkflow';
import {visibleOperationalRead} from './visibleOperationalRead';
import './inventoryPurchaseDraft.css';

export const receiptLabels = {not_received:'بانتظار الاستلام',partial:'مستلمة جزئيًا',received:'مستلمة بالكامل',closed:'لا توجد كمية متبقية للاستلام',unproven:'الاستلام غير مثبت'};
export function PurchaseReceiptStatus({invoice}) {
  const status = invoice.receipt_status || 'unproven';
  return <span className={'mz-receipt-status '+(['not_received','partial'].includes(status)?'is-waiting':'')}>{receiptLabels[status] || receiptLabels.unproven}</span>;
}
export function PurchaseReceiptHistory({invoice}) {
  if (!invoice.receipt_history?.length) return null;
  return <details className="mz-receipt-history"><summary>سجل الاستلام · {invoice.receipt_history.length} دفعات</summary>{invoice.receipt_history.map(receipt=><section key={receipt.id}><p><strong>{receipt.receipt_date}</strong>{receipt.note?' · '+receipt.note:''}</p><ul>{receipt.lines.map(line=><li key={line.line_id}>{line.name} · {line.quantity} {line.unit==='piece'?'قطعة':line.unit} · {line.location_name}</li>)}</ul></section>)}</details>;
}

export default function InventoryPurchaseReceipts({context,initialInvoiceId=''}) {
  const scope = context.session_scope, key = 'mezan.inventory.purchase.receiving.v1:'+scope;
  const [recovery] = useState(()=>{try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved&&(!saved.invoice_id||!saved.body?.request_id||saved.body.expected_session_scope!==scope||!saved.body.preview_hash))throw Error();return {saved};}catch{return {error:'تعذر قراءة عملية الاستلام المحفوظة. تحقق من التخزين قبل المتابعة.'};}});
  const [pending,setPending] = useState(recovery.saved), [rows,setRows] = useState([]), [selected,setSelected] = useState(recovery.saved?.invoice_id || initialInvoiceId);
  const [detail,setDetail] = useState(null), [quantities,setQuantities] = useState({}), [locations,setLocations] = useState({});
  const [date,setDate] = useState(purchaseToday), [note,setNote] = useState(''), [filter,setFilter] = useState('waiting'), [search,setSearch] = useState('');
  const [loading,setLoading] = useState(true), [detailLoading,setDetailLoading] = useState(false), [busy,setBusy] = useState(false), [revision,setRevision] = useState(0);
  const [error,setError] = useState(recovery.error || ''), [message,setMessage] = useState(''), [readError,setReadError] = useState('');
  const lock = useRef(false), listLane = useRef({}), detailLane = useRef({}), mounted=useRef(true);
  const owner = context.can_manage_inventory_opening === true;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{
    if(!owner)return;
    return visibleOperationalRead(config=>api.inventoryPurchases(config),{
      onStart:()=>{setLoading(true);setReadError('');},onData:result=>setRows(result.items || []),
      onError:reason=>setReadError(messageFor(reason)),onEnd:()=>setLoading(false),
    },listLane.current);
  },[owner,scope,revision]);
  useEffect(()=>{
    if(!owner||!selected){setDetail(null);return;}
    return visibleOperationalRead(config=>api.inventoryPurchaseReceivingContext(selected,config),{
      onStart:()=>{setDetailLoading(true);setDetail(null);setError(recovery.error || '');},
      onData:result=>{
        if(!result?.invoice || !Array.isArray(result.locations) || typeof result.writer_disabled!=='boolean')throw Error('تعذر التحقق من بيانات الاستلام.');
        setDetail(result);setQuantities({});setLocations(Object.fromEntries(result.invoice.lines.map(line=>[line.line_id,line.location_id || ''])));setNote('');
      },onError:reason=>setError(messageFor(reason)),onEnd:()=>setDetailLoading(false),
    },detailLane.current);
  },[owner,scope,selected,revision,recovery.error]);
  async function save(){
    if(lock.current||!owner||!detail||detail.writer_disabled||!detail.operational_active||recovery.error)return;
    lock.current=true;setBusy(true);setError('');setMessage('');
    let command=pending;
    try{
      if(!command){
        const lines=detail.invoice.lines.map(line=>({line_id:line.line_id,quantity:Number(quantities[line.line_id] || 0),location_id:locations[line.line_id] || ''}));
        if(lines.some((line,index)=>!Number.isSafeInteger(line.quantity)||line.quantity<0||line.quantity>detail.invoice.lines[index].pending_receipt_quantity))throw Error('أدخل كمية صحيحة لا تتجاوز المتبقي للاستلام.');
        const chosen=lines.filter(line=>line.quantity>0);
        if(!chosen.length||chosen.some(line=>!line.location_id))throw Error('حدد الكميات التي وصلت وموقع تخزين كل كمية.');
        if(!date||date<detail.invoice.invoice_date||date>purchaseToday())throw Error('راجع تاريخ الاستلام.');
        const body={request_id:requestId(),expected_session_scope:scope,receipt_date:date,lines:chosen,note};
        const preview=await api.previewInventoryPurchaseReceipt(selected,body);
        if(!preview?.preview_hash||preview.writer_disabled||preview.fulfillment_eligible!==false)throw Error('الاستلام متوقف أو تعذر التحقق من الكميات.');
        command={invoice_id:selected,body:{...body,preview_hash:preview.preview_hash}};
        localStorage.setItem(key,JSON.stringify(command));setPending(command);
      }
      const result=await api.saveInventoryPurchaseReceipt(command.invoice_id,command.body);
      if(result?.status!=='approved')throw Error('لم يصل تأكيد الاستلام. أعد المحاولة بنفس العملية.');
      localStorage.removeItem(key);
      if(mounted.current){setPending(null);setMessage('تم تسجيل الاستلام وإضافة الكميات المحددة إلى المخزون. بقي رصيد المورد والسداد دون تغيير.');setRevision(value=>value+1);}
    }catch(reason){
      const code=reason.response?.data?.detail?.code;
      if(reason.response?.data?.detail?.not_applied===true||['purchase_receipt_preview_changed','opening_inventory_preview_changed','purchase_receipt_quantity_exceeded'].includes(code)||reason.response?.status===422){localStorage.removeItem(key);if(mounted.current)setPending(null);}
      if(mounted.current)setError(reason.response?messageFor(reason):reason.message);
    }finally{lock.current=false;if(mounted.current)setBusy(false);}
  }
  if(!owner)return <p role="alert">تسجيل استلام مشتريات المخزون متاح للمالك فقط.</p>;
  const filtered=rows.filter(row=>(filter==='all'||filter==='waiting'&&['not_received','partial'].includes(row.receipt_status)||row.receipt_status===filter)&&(!search||[row.supplier_name,row.invoice_number,...row.lines.map(line=>line.name)].join(' ').toLowerCase().includes(search.trim().toLowerCase())));
  return <section className="mz-purchase-receiving" dir="rtl" aria-label="استلام مشتريات المخزون">
    <p>افتح الفاتورة عند وصول البضاعة وسجّل الكميات المستلمة فقط. يمكن الاستلام على دفعات مع بقاء المتبقي ظاهرًا.</p>
    <Notice error={error || readError} message={message}/>
    <button type="button" className="mz-inventory-button" disabled={loading||busy} onClick={()=>setRevision(value=>value+1)}>تحديث فواتير الاستلام</button>
    {pending&&<p role="alert">يوجد استلام بانتظار تأكيد النتيجة. أعد المحاولة بالمرجع نفسه لمنع تكرار الكمية.<button type="button" disabled={busy||!detail||detail.writer_disabled} onClick={save}>إعادة محاولة الاستلام</button></p>}
    {detailLoading&&<p role="status">جارٍ فتح بيانات الاستلام…</p>}
    {detail&&<fieldset className="mz-receipt-form" disabled={busy||!!pending||detail.writer_disabled||!detail.operational_active||!!recovery.error}>
      <legend>تسجيل استلام · {detail.invoice.invoice_number}</legend><p>{detail.invoice.supplier_name} · <PurchaseReceiptStatus invoice={detail.invoice}/></p>
      {detail.writer_disabled&&<p role="alert">إدخال المخزون المستقل متوقف. يمكن للمالك تفعيله من «إضافة المخزون».</p>}
      {!detail.operational_active&&<p role="alert">النظام التشغيلي متوقف عن الكتابة.</p>}
      <Field label="تاريخ الاستلام"><input type="date" min={detail.invoice.invoice_date} max={purchaseToday()} value={date} onChange={event=>setDate(event.target.value)}/></Field>
      {detail.invoice.lines.map(line=><div className="mz-receipt-item" key={line.line_id}><strong>{line.name}</strong><p>{configurationDescription(line)}</p><p>المشتراة {line.quantity} · المستلمة {line.received_quantity}{line.returned_quantity>0?' · المرتجعة للمورد '+line.returned_quantity:''} · المتبقية للاستلام {line.pending_receipt_quantity}</p>{line.pending_receipt_quantity>0&&<div className="mz-invoice-meta"><Field label={'المستلم الآن — '+line.name}><input type="number" min="0" max={line.pending_receipt_quantity} step="1" value={quantities[line.line_id] || ''} onChange={event=>setQuantities({...quantities,[line.line_id]:event.target.value})}/></Field><Field label={'موقع الاستلام — '+line.name}><select value={locations[line.line_id] || ''} onChange={event=>setLocations({...locations,[line.line_id]:event.target.value})}><option value="">اختر الموقع</option>{detail.locations.map(location=><option key={location.id} value={location.id}>{location.name}{location.warehouse_name?' · '+location.warehouse_name:''}</option>)}</select></Field></div>}</div>)}
      <Field label="ملاحظة الاستلام الاختيارية"><textarea rows="2" maxLength="1000" value={note} onChange={event=>setNote(event.target.value)}/></Field>
      <div className="mz-invoice-actions"><button type="button" className="op-primary" disabled={!detail.invoice.can_receive} onClick={save}>حفظ الاستلام وإضافة الكمية</button><button type="button" onClick={()=>setSelected('')}>إغلاق الفاتورة</button></div>
      <PurchaseReceiptHistory invoice={detail.invoice}/>
    </fieldset>}
    <div className="mz-receipt-filter">{[['waiting','بانتظار الاستلام'],['received','المستلمة'],['all','جميع الفواتير']].map(([value,label])=><button type="button" key={value} aria-pressed={filter===value} onClick={()=>setFilter(value)}>{label}</button>)}</div>
    <Field label="بحث في فواتير الاستلام"><input value={search} onChange={event=>setSearch(event.target.value)}/></Field>
    {loading&&<p role="status">جارٍ تحميل الفواتير…</p>}
    {!loading&&!readError&&!filtered.length&&<p>لا توجد فواتير ضمن هذه القائمة.</p>}
    <div className="mz-receipt-list">{filtered.map(row=><article key={row.id}><header><h3>{row.invoice_number} · {row.supplier_name}</h3><PurchaseReceiptStatus invoice={row}/></header><p>{row.invoice_date} · الإجمالي {row.gross} ر.س · المسدد {row.settled} · المتبقي للمورد {row.outstanding}</p>{row.lines.map((line,index)=><p key={line.line_id || index}>{line.name} · المشتراة {line.quantity}{row.receipt_tracking_version===1?' · المستلمة '+line.received_quantity+(line.returned_quantity>0?' · المرتجعة للمورد '+line.returned_quantity:'')+' · المتبقية للاستلام '+line.pending_receipt_quantity:''}</p>)}{row.receipt_tracking_version===1?<button type="button" disabled={busy||!!pending} onClick={()=>{setSelected(row.id);setMessage('');}}>{row.can_receive?'تسجيل استلام':'عرض سجل الاستلام'}</button>:<p>فاتورة قديمة دون سجل استلام مرتبط. يلزم مطابقة المخزون قبل إضافة كمية جديدة.</p>}<PurchaseReceiptHistory invoice={row}/></article>)}</div>
  </section>;
}
