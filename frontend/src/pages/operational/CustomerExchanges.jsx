import React, {useEffect, useRef, useState} from 'react';
import {operationalApi as api, requestId, messageFor} from './api';
import {Field, Notice} from './OpeningBalances';
import './operational.css';

const localTime = () => {const d=new Date();return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);};
const emptyPayment = () => ({amount:'',bank_id:'',reference:'',paid_at:localTime(),existing_movement_id:null});
const price = value => {
  if(!/^\d+(\.\d{1,2})?$/.test(value))throw Error('أدخل مبالغ الفاتورة بمنزلتين عشريتين كحد أقصى.');
  const [whole,part='']=value.split('.');const cents=Number(whole)*100+Number(part.padEnd(2,'0'));
  if(!Number.isSafeInteger(cents))throw Error('مبلغ الفاتورة أكبر من الحد المسموح.');return cents;
};
function PaymentFields({value,onChange,banks,movements}){
  return <>
    <Field label="تسجيل المساهمة"><select value={value.existing_movement_id||''} onChange={e=>{
      const m=movements.find(r=>r.id===e.target.value);
      onChange(m?{...value,existing_movement_id:m.id,amount:m.amount,bank_id:m.bank_id,reference:m.reference}:{...emptyPayment()});
    }}><option value="">وارد جديد — لم يُسجل سابقًا</option>{movements.map(m=><option value={m.id} key={m.id}>ربط {m.reference} · {m.amount} · {m.bank_name}</option>)}</select></Field>
    <Field label="مبلغ مساهمة العميل"><input inputMode="decimal" value={value.amount} disabled={!!value.existing_movement_id} onChange={e=>onChange({...value,amount:e.target.value})}/></Field>
    <Field label="البنك المستلم"><select value={value.bank_id} disabled={!!value.existing_movement_id} onChange={e=>onChange({...value,bank_id:e.target.value})}><option value="">اختر البنك</option>{banks.map(b=><option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>
    <Field label="مرجع دفع العميل"><input value={value.reference} disabled={!!value.existing_movement_id} onChange={e=>onChange({...value,reference:e.target.value})}/></Field>
    <Field label="وقت دفع العميل"><input type="datetime-local" value={value.paid_at} onChange={e=>onChange({...value,paid_at:e.target.value})}/></Field>
  </>;
}

export default function CustomerExchanges(){
  const [context,setContext]=useState(null),[error,setError]=useState('');
  useEffect(()=>{let alive=true;api.context().then(c=>alive&&setContext(c)).catch(e=>alive&&setError(messageFor(e)));return()=>{alive=false;};},[]);
  if(error)return <main className="op-page" dir="rtl"><Notice error={error}/></main>;
  if(!context)return <main className="op-page" dir="rtl" role="status">جارٍ تحميل الاستبدالات…</main>;
  return <ExchangeForm key={context.session_scope} context={context}/>;
}

function ExchangeForm({context}){
  const scope=context.session_scope,key=`mezan.operational.exchanges.v1:${scope}`;
  const [restored]=useState(()=>{try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved&&(saved.body?.expected_session_scope!==scope||!saved.body?.request_id))throw Error();return {saved};}catch{return {error:'تعذر قراءة العملية المحفوظة. تحقق من التخزين قبل إنشاء عملية أخرى.'};}});
  const [pending,setPending]=useState(restored.saved||null),[error,setError]=useState(restored.error||''),[message,setMessage]=useState('');
  const [busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[order,setOrder]=useState(null),[number,setNumber]=useState('');
  const [cases,setCases]=useState([]),[accounts,setAccounts]=useState({bank:[],courier:[],supplier:[]}),[movements,setMovements]=useState([]);
  const [selected,setSelected]=useState({}),[carrier,setCarrier]=useState(''),[quote,setQuote]=useState(null),[shipment,setShipment]=useState('');
  const [paid,setPaid]=useState(false),[payment,setPayment]=useState(emptyPayment),[opened,setOpened]=useState(null),[mode,setMode]=useState('');
  const [invoice,setInvoice]=useState({supplier_id:'',invoice_number:'',invoice_date:localTime().slice(0,10),lines:[]});
  const lock=useRef(false),search=useRef(0);
  const writable=context.status==='active'&&context.permissions?.move;
  const disabled=!writable||busy||loading||!!pending||!!restored.error;
  const reload=async()=>{const [c,m]=await Promise.all([api.customerExchanges(),api.movements()]);setCases(c.items);setMovements(m.items);};
  useEffect(()=>{let alive=true;Promise.all([api.customerExchanges(),api.movements(),...['bank','courier','supplier'].map(k=>api.entities(k))]).then(([c,m,...a])=>{
    if(alive){setCases(c.items);setMovements(m.items);setAccounts(Object.fromEntries(['bank','courier','supplier'].map((k,i)=>[k,a[i].items])));}
  }).catch(e=>alive&&setError(messageFor(e))).finally(()=>alive&&setLoading(false));return()=>{alive=false;};},[]);
  useEffect(()=>{let alive=true;setQuote(null);if(!carrier||!order)return;api.returnShippingQuote('courier',carrier,order.order_number).then(q=>alive&&setQuote(q)).catch(e=>alive&&setError(messageFor(e)));return()=>{alive=false;};},[carrier,order]);
  const availableMovements=movements.filter(m=>m.bank_kind==='bank'&&m.party_type==='bank'&&m.kind==='collection'&&m.direction==='incoming'&&!m.order_number&&!m.automatic_order_bank&&!m.exchange_id&&m.reference&&!m.allocations?.length&&!cases.some(c=>c.contributions.some(p=>p.movement_id===m.id)));
  const paymentBody=()=>{if(!payment.bank_id||!payment.reference.trim()||price(payment.amount)<=0||!payment.paid_at)throw Error('أكمل بيانات المساهمة المدفوعة بالفعل.');return {...payment,paid_at:new Date(payment.paid_at).toISOString()};};
  async function findOrder(){
    if(disabled)return;const version=++search.current;setLoading(true);setError('');setOpened(null);setMode('');setOrder(null);setCarrier('');setSelected({});
    try{const result=await api.exchangeOrder(number.trim());if(search.current===version)setOrder(result);}catch(e){setError(messageFor(e));}finally{setLoading(false);}
  }
  function openCase(row,next){if(disabled)return;setNumber(row.order_number);setOrder(null);setOpened(row);setMode(next);setPayment(emptyPayment());setShipment(row.shipping.reference||'');setMessage('');setError('');setInvoice({supplier_id:'',invoice_number:'',invoice_date:localTime().slice(0,10),lines:row.items.filter(i=>i.remaining_to_buy>0).map(i=>({item_id:i.id,name:i.name,remaining:i.remaining_to_buy,quantity:0,net:'',tax:'0',gross:''}))});}
  async function save(){
    if(lock.current||!writable||restored.error)return;lock.current=true;setBusy(true);setError('');setMessage('');
    try{
      let command=pending;
      if(!command){
        let body,id=opened?.id||null;
        if(mode==='purchase'){
          const lines=invoice.lines.filter(l=>Number(l.quantity)>0).map(({item_id,quantity,net,tax,gross})=>({item_id,quantity:Number(quantity),net,tax,gross}));
          if(!lines.length||!invoice.supplier_id||!invoice.invoice_number.trim())throw Error('اختر المنتجات والمورد وأدخل رقم الفاتورة.');
          const totals=Object.fromEntries(['net','tax','gross'].map(k=>[k,(lines.reduce((sum,l)=>sum+price(l[k]),0)/100).toFixed(2)]));
          body={action:'purchase',supplier_id:invoice.supplier_id,invoice_number:invoice.invoice_number,invoice_date:invoice.invoice_date,lines,...totals};
        }else if(mode==='contribution')body={action:'contribution',contribution:paymentBody()};
        else if(mode==='shipping_completed'){if(!shipment.trim())throw Error('أدخل مرجع شحنة البدل المنفذة.');body={action:'shipping_completed',shipment_reference:shipment};}
        else{
          const items=Object.entries(selected).filter(([,q])=>Number(q)>0).map(([id,q])=>({id,quantity:Number(q)}));
          if(!order||!items.length||!carrier||!quote)throw Error('اختر منتجات الاستبدال وشركة الشحن وانتظر ظهور التكلفة.');
          body={order_number:order.order_number,items,shipping_id:carrier,shipping_quote_hash:quote.quote_hash,shipment_reference:shipment,contribution:paid?paymentBody():null};
        }
        command={id,body:{...body,expected_session_scope:scope,request_id:requestId()}};localStorage.setItem(key,JSON.stringify(command));setPending(command);
      }
      const fresh=await api.context();if(fresh.session_scope!==scope||!fresh.permissions?.move)throw Error('تغيرت الجلسة؛ احتفظنا بالعملية للتحقق.');
      await api.saveExchange(command.body,command.id);
      localStorage.removeItem(key);setPending(null);setOrder(null);setOpened(null);setMode('');setSelected({});setCarrier('');setShipment('');setPaid(false);setPayment(emptyPayment());setMessage('تم حفظ عملية الاستبدال');await reload();
    }catch(e){
      if(e.response?.data?.detail?.not_applied||[400,422].includes(e.response?.status)){localStorage.removeItem(key);setPending(null);}
      setError(e.response?messageFor(e):e.message||'تعذر تأكيد الحفظ؛ أعد المحاولة بالعملية نفسها.');
    }finally{lock.current=false;setBusy(false);}
  }
  return <main className="op-page op-returns" dir="rtl"><h1>الاستبدال التشغيلي</h1><p>رقم الطلب الأصلي فقط — لا يُنشأ طلب بيع جديد.</p><Notice error={error} message={message}/>
    {loading&&<p role="status">جارٍ تحميل البيانات…</p>}
    {!writable&&<p>التسجيل غير متاح في حالة النظام الحالية.</p>}
    {pending&&<section className="op-card"><p>عملية محفوظة بانتظار تأكيد النتيجة. إعادة المحاولة لا تكرر المبلغ.</p><button disabled={busy||!writable} onClick={save}>إعادة محاولة الحفظ</button></section>}
    <section className="op-card op-movement-cards"><Field label="رقم الطلب القديم"><input value={number} disabled={disabled} onChange={e=>{setNumber(e.target.value);setOrder(null);setOpened(null);setMode('');}}/></Field><button disabled={disabled||!number.trim()} onClick={findOrder}>عرض الطلب والاستبدالات</button>
      {order&&<><h2>منتجات الطلب {order.order_number}</h2><fieldset disabled={disabled}><button onClick={()=>setSelected(Object.fromEntries(order.items.filter(i=>i.remaining>0).map(i=>[i.id,i.remaining])))}>استبدال الطلب كاملًا — المتاح</button>
        {order.items.map(i=><Field key={i.id} label={`${i.name} · تقديري للوحدة: ${i.unit_estimate??'إعداد غير مكتمل'}`}><input aria-label={`كمية بدل ${i.name}`} type="number" min="0" max={i.remaining} value={selected[i.id]||0} onChange={e=>setSelected({...selected,[i.id]:e.target.value})}/></Field>)}
        <Field label="شركة شحن البدل"><select value={carrier} onChange={e=>setCarrier(e.target.value)}><option value="">اختر شركة الشحن</option>{accounts.courier.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
        {quote&&<p>تكلفة شحنة البدل: {quote.amount} ريال — تقديرية حتى التنفيذ.</p>}
        <Field label="مرجع الشحنة إن وجد"><input value={shipment} onChange={e=>setShipment(e.target.value)}/></Field>
        <Field label="هل دفع العميل بالفعل؟"><select value={paid?'yes':'no'} onChange={e=>setPaid(e.target.value==='yes')}><option value="no">لم يدفع — نتحمل التكلفة كاملة</option><option value="yes">دفع مساهمة في الاستبدال</option></select></Field>
        {paid&&<PaymentFields value={payment} onChange={setPayment} banks={accounts.bank} movements={availableMovements}/>}
      </fieldset><button className="op-primary" disabled={disabled} onClick={save}>حفظ الاستبدال</button></>}
      {opened&&<><h2>{mode==='purchase'?'شراء بدل':mode==='contribution'?'مساهمة العميل':'تنفيذ شحنة البدل'} — الطلب {opened.order_number}</h2><fieldset disabled={disabled}>
        {mode==='purchase'&&<><p>تسجيل فاتورة المورد الفعلية تشغيليًا. إجماليها يثبت مستحق المورد، ولا يعني سدادًا.</p>
          <Field label="المورد"><select value={invoice.supplier_id} onChange={e=>setInvoice({...invoice,supplier_id:e.target.value})}><option value="">اختر المورد</option>{accounts.supplier.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="رقم فاتورة المورد"><input value={invoice.invoice_number} onChange={e=>setInvoice({...invoice,invoice_number:e.target.value})}/></Field>
          <Field label="تاريخ فاتورة المورد"><input type="date" value={invoice.invoice_date} onChange={e=>setInvoice({...invoice,invoice_date:e.target.value})}/></Field>
          {invoice.lines.map((line,index)=><div className="op-entry-wide" key={line.item_id}><h3>{line.name} · المتبقي {line.remaining}</h3>{[['quantity','الكمية المشتراة'],['net','صافي البند'],['tax','ضريبة البند'],['gross','إجمالي البند شامل الضريبة']].map(([k,label])=><Field key={k} label={`${label} — ${line.name}`}><input type={k==='quantity'?'number':'text'} inputMode="decimal" min={k==='quantity'?0:undefined} max={k==='quantity'?line.remaining:undefined} value={line[k]} onChange={e=>setInvoice({...invoice,lines:invoice.lines.map((r,j)=>j===index?{...r,[k]:e.target.value}:r)})}/></Field>)}</div>)}
        </>}
        {mode==='contribution'&&<PaymentFields value={payment} onChange={setPayment} banks={accounts.bank} movements={availableMovements}/>}
        {mode==='shipping_completed'&&<><p>أكد فقط بعد تنفيذ الشحنة. يتحول التقديري إلى مستحق للشركة دون خصم البنك.</p><Field label="مرجع شحنة البدل المنفذة"><input value={shipment} onChange={e=>setShipment(e.target.value)}/></Field></>}
      </fieldset><button className="op-primary" disabled={disabled} onClick={save}>{busy?'جارٍ الحفظ…':'حفظ العملية'}</button></>}
    </section>
    <section><h2>الاستبدالات المسجلة</h2><button disabled={busy||loading} onClick={()=>reload().catch(e=>setError(messageFor(e)))}>تحديث القائمة</button>
      {cases.filter(c=>!number.trim()||c.order_number===number.trim()).map(c=><article className="op-card op-movement-cards" key={c.id}><h3>الطلب الأصلي {c.order_number}</h3><p>{c.purchase_status==='purchased'?'تم شراء جميع البدائل':c.purchase_status==='partial'?'شراء جزئي — يوجد متبقٍ':'بانتظار شراء البديل'}</p>
        <div className="op-summary">{[['expected_products','منتجات متوقعة'],['confirmed_products','فواتير مورد مؤكدة'],['shipping','تكلفة الشحن'],['customer_contribution','مساهمة العميل'],['net_cost','صافي التكلفة علينا']].map(([k,label])=><article key={k}>{label}<strong>{c.summary[k]} ريال</strong></article>)}</div>
        <p>{c.shipping.name} · {c.shipping.status==='completed'?'الشحنة منفذة':'بانتظار تنفيذ الشحنة'}</p>
        {c.items.map(i=><p key={i.id}>{i.name} · المطلوب {i.quantity} · المتبقي للشراء {i.remaining_to_buy}</p>)}
        {c.purchases.map(p=><p key={p.id}>فاتورة {p.invoice_number} · {p.supplier_name} · صافي {p.net} + ضريبة {p.tax} = {p.gross} ريال</p>)}
        {c.contributions.map(p=><p key={p.movement_id}>مساهمة {p.amount} ريال · {p.bank_name} · {p.reference}</p>)}
        <nav><button disabled={disabled||c.purchase_status==='purchased'} onClick={()=>openCase(c,'purchase')}>شراء بدل</button><button disabled={disabled} onClick={()=>openCase(c,'contribution')}>تسجيل مساهمة العميل</button><button disabled={disabled||c.shipping.status==='completed'} onClick={()=>openCase(c,'shipping_completed')}>تأكيد تنفيذ الشحنة</button></nav>
      </article>)}
    </section>
  </main>;
}
