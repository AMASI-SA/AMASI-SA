import React,{useEffect,useRef,useState} from 'react';
import {operationalApi as api,requestId,messageFor} from './api';
import {Field,Notice} from './OpeningBalances';
import {visibleOperationalRead} from './visibleOperationalRead';
import './operational.css';

const localInput=value=>{const d=new Date(value);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);};
const blank=()=>({order_number:'',items:[],status:'pending',amount:'',refund_source_type:'bank',refund_source_id:'',refund_reference:'',refunded_at:'',shipping_kind:'none',shipping_id:null,shipment_reference:'',shipment_completed:false,shipping_quote_hash:null,note:''});
export default function CustomerReturns(){
 const [context,setContext]=useState(null),[error,setError]=useState('');
 const contextLane=useRef({pending:null});
 useEffect(()=>visibleOperationalRead(config=>api.context(config),{
  onData:setContext,onError:e=>setError(messageFor(e)),
 },contextLane.current),[]);
 if(error)return <main className="op-page" dir="rtl"><Notice error={error}/></main>;
 if(!context)return <main className="op-page" dir="rtl" role="status">جارٍ تحميل مرتجعات العملاء…</main>;
 return <ReturnsForm key={context.session_scope} context={context}/>;
}

function ReturnsForm({context}){
 const scope=context.session_scope,key=`mezan.operational.customer-returns.v1:${scope}`;
 const [restored]=useState(()=>{try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved&&(saved.body?.expected_session_scope!==scope||typeof saved.body?.request_id!=='string'))throw Error();return {saved};}catch{return {error:'تعذر قراءة العملية المحفوظة. لا تُدخل عملية أخرى قبل التحقق من التخزين.'};}});
 const [pending,setPending]=useState(restored.saved||null),[form,setForm]=useState(restored.saved?.body||blank),[caseId,setCaseId]=useState(restored.saved?.id||null);
 const [casesLoaded,setCasesLoaded]=useState(false);
 const [order,setOrder]=useState(null),[cases,setCases]=useState([]),[accounts,setAccounts]=useState({}),[quote,setQuote]=useState(null);
 const [busy,setBusy]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState(restored.error||''),[message,setMessage]=useState('');
 const lock=useRef(false),searchVersion=useRef(0);
 const readLane=useRef({pending:null}),quoteLane=useRef({pending:null}),orderLane=useRef({pending:null});
 const [revision,setRevision]=useState(0),[orderRead,setOrderRead]=useState(null);
 const writable=context.status==='active'&&context.permissions?.move;
 const disabled=busy||!!pending||!!restored.error||!writable;
 useEffect(()=>visibleOperationalRead(async config=>{
  const results=await Promise.allSettled([api.customerReturns(config),...['bank','provider','courier','store_driver'].map(k=>api.entities(k,config))]);
  const failure=results.find(r=>r.status==='rejected');if(failure)throw failure.reason;
  return results.map(r=>r.value);
 },{
  onStart:()=>setCasesLoaded(false),
  onData:([r,...choices])=>{setCases(r.items);setCasesLoaded(true);setAccounts(Object.fromEntries(['bank','provider','courier','store_driver'].map((k,i)=>[k,choices[i].items])));},
  onError:e=>setError(messageFor(e)),
 },readLane.current),[revision]);
 useEffect(()=>{if(caseId)return undefined;setQuote(null);if(form.shipping_kind==='none'||!form.shipping_id||pending)return undefined;
  return visibleOperationalRead(config=>api.returnShippingQuote(form.shipping_kind,form.shipping_id,form.order_number,config),{
   onData:q=>{setQuote(q);setForm(f=>({...f,shipping_quote_hash:q.quote_hash}));},onError:e=>setError(messageFor(e)),
  },quoteLane.current);
 },[form.shipping_kind,form.shipping_id,form.order_number,caseId,pending]);
 useEffect(()=>{if(!orderRead)return undefined;
  return visibleOperationalRead(config=>api.returnOrder(orderRead.number,config),{
   onStart:()=>setLoading(true),
   onData:o=>{if(orderRead.version!==searchVersion.current)return;setOrder(o);
    const row=orderRead.row;
    if(row){setCaseId(row.id);setQuote(row.shipping);setForm({...blank(),order_number:row.order_number,items:row.items,status:row.status,amount:row.amount||'',refund_source_type:row.refund_source_type||'bank',refund_source_id:row.refund_source_id||'',refund_reference:row.refund_reference||'',refunded_at:row.refunded_at?localInput(row.refunded_at):'',shipping_kind:row.shipping.kind,shipping_id:row.shipping.id||null,shipment_reference:row.shipping.reference||'',shipment_completed:row.shipping.status==='completed',note:row.note||''});}
    else {setForm({...blank(),order_number:o.order_number,amount:(Number(o.total)-Number(o.refunded)).toFixed(2)});setCaseId(null);}},
   onError:e=>{if(orderRead.version===searchVersion.current)setError(messageFor(e));},
   onEnd:()=>{if(orderRead.version===searchVersion.current)setLoading(false);},
  },orderLane.current);
 },[orderRead]);
 const change=patch=>{if(disabled)return;setForm(f=>({...f,...patch}));setError('');setMessage('');};
 function findOrder(){const version=++searchVersion.current;setLoading(true);setError('');setOrder(null);setOrderRead({number:form.order_number,version});}
 function openCase(row){if(disabled)return;const version=++searchVersion.current;setLoading(true);setError('');setOrderRead({number:row.order_number,version,row});}
 async function save(){
  if(lock.current||!writable||restored.error)return;
  lock.current=true;setBusy(true);setError('');
  try{
   let command=pending;
   if(!command){
    if(!order||!form.items.length)throw Error('اختر الطلب والمنتجات المسترجعة.');
    if(form.status==='refunded'&&(!form.amount||!form.refund_source_id||!form.refund_reference.trim()||!form.refunded_at))throw Error('أكمل مبلغ الرد وجهته ومرجعه وتاريخه.');
    if(form.shipping_kind!=='none'&&!caseId&&!quote)throw Error('انتظر قراءة تكلفة الاسترجاع المعتمدة.');
    command={id:caseId,body:{...form,amount:form.amount||null,refund_source_id:form.refund_source_id||null,refunded_at:form.refunded_at?new Date(form.refunded_at).toISOString():null,request_id:requestId(),expected_session_scope:scope}};
    localStorage.setItem(key,JSON.stringify(command));setPending(command);
   }
   const fresh=await api.context();if(fresh.session_scope!==scope||!fresh.permissions?.move)throw Error('تغيرت الجلسة أو الصلاحية؛ احتفظنا بالعملية للتحقق.');
   await api.saveCustomerReturn(command.body,command.id);
   localStorage.removeItem(key);setPending(null);setForm(blank());setOrder(null);setOrderRead(null);setCaseId(null);setMessage('تم حفظ المرتجع');
   setRevision(value=>value+1);
  }catch(e){
   if(e?.response?.data?.detail?.not_applied===true||[400,422].includes(e?.response?.status)){localStorage.removeItem(key);setPending(null);}
   setError(e?.response?messageFor(e):e.message||'تعذر تأكيد الحفظ؛ أعد المحاولة بالبيانات نفسها.');
  }finally{lock.current=false;setBusy(false);}
 }
 const existing=cases.find(c=>c.id===caseId),alreadyRefunded=existing?.status==='refunded';
 return <main className="op-page op-returns" dir="rtl"><h1>مرتجعات العملاء</h1><p>سجّل المنتجات المسترجعة وحالة رد المبلغ وشحنة الاسترجاع.</p><Notice error={error} message={message}/>
 {!writable&&<p>التسجيل غير متاح قبل بدء النظام أو دون صلاحية الدخول الحالية.</p>}
 {pending&&<p role="status">هناك عملية محفوظة بانتظار تأكيد النتيجة. إعادة المحاولة لا تنشئ خصمًا ثانيًا.</p>}
 <section className="op-card op-movement-cards"><Field label="رقم الطلب"><input disabled={disabled||loading} value={form.order_number} onChange={e=>{change({order_number:e.target.value,items:[]});setOrder(null);setCaseId(null);}}/></Field><button disabled={disabled||loading||!form.order_number} onClick={findOrder}>{loading?'جارٍ البحث…':'عرض الطلب'}</button>
 {order&&<><h2>طلب {order.order_number}</h2><p>إجمالي الطلب: {order.total} ريال · المردود: {order.refunded} ريال</p>
 <fieldset disabled={disabled||!!caseId}><button onClick={()=>change({items:order.items.filter(i=>i.remaining>0).map(i=>({id:i.id,quantity:i.remaining}))})}>الطلب كاملًا — الكميات المتاحة</button>
 {order.items.map(item=><Field key={item.id} label={item.name}><input aria-label={`كمية ${item.name}`} type="number" min="0" max={item.remaining} value={form.items.find(i=>i.id===item.id)?.quantity||0} onChange={e=>change({items:[...form.items.filter(i=>i.id!==item.id),...(Number(e.target.value)>0?[{id:item.id,quantity:Number(e.target.value)}]:[])]})}/></Field>)}</fieldset>
 <fieldset disabled={disabled||alreadyRefunded}><Field label="حالة رد المبلغ"><select value={form.status} onChange={e=>change({status:e.target.value})}><option value="pending">لم يُرد المبلغ بعد</option><option value="refunded">تم رد المبلغ بالفعل</option></select></Field>
 {form.status==='pending'?<p>لن يُخصم مبلغ من البنك أو المنصة حتى تأكيد رده.</p>:<><Field label="المبلغ المردود فعليًا"><input inputMode="decimal" value={form.amount} onChange={e=>change({amount:e.target.value})}/></Field><Field label="الرد من"><select value={form.refund_source_type} onChange={e=>change({refund_source_type:e.target.value,refund_source_id:''})}><option value="bank">بنك</option><option value="provider">منصة دفع</option></select></Field><Field label="البنك أو منصة الدفع"><select value={form.refund_source_id} onChange={e=>change({refund_source_id:e.target.value})}><option value="">اختر</option>{(accounts[form.refund_source_type]||[]).filter(a=>form.refund_source_type!=='provider'||a.id===order.provider).map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></Field><Field label="مرجع عملية رد المبلغ"><input value={form.refund_reference} onChange={e=>change({refund_reference:e.target.value})}/></Field><Field label="وقت رد المبلغ"><input type="datetime-local" value={form.refunded_at} onChange={e=>change({refunded_at:e.target.value})}/></Field><p>عمولة الدفع تبقى محسوبة للمنصة ولا تُخصم مرة ثانية من البنك.</p></>}
 </fieldset>
 <fieldset disabled={disabled}><Field label="شحنة الاسترجاع"><select disabled={!!caseId} value={form.shipping_kind} onChange={e=>change({shipping_kind:e.target.value,shipping_id:null,shipping_quote_hash:null,shipment_reference:'',shipment_completed:false})}><option value="none">لا توجد شحنة استرجاع</option><option value="courier">شركة شحن</option><option value="store_driver">مندوب المتجر — مجاني</option></select></Field>
 {form.shipping_kind!=='none'&&<><Field label="منفذ الاسترجاع"><select disabled={!!caseId} value={form.shipping_id||''} onChange={e=>change({shipping_id:e.target.value,shipping_quote_hash:null})}><option value="">اختر</option>{(accounts[form.shipping_kind]||[]).map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>{quote&&<p>تكلفة الاسترجاع: {quote.amount} ريال {form.shipping_kind==='store_driver'?'— لا تُحسب توصيلة للمندوب':''}</p>}<Field label="رقم البوليصة أو مرجع الاسترجاع"><input disabled={!!caseId} value={form.shipment_reference} onChange={e=>change({shipment_reference:e.target.value})}/></Field><label><input type="checkbox" checked={form.shipment_completed} disabled={existing?.shipping.status==='completed'} onChange={e=>change({shipment_completed:e.target.checked})}/> تم تنفيذ شحنة الاسترجاع</label></>}
 </fieldset><button className="op-primary" disabled={busy||!!restored.error||!writable||loading} onClick={save}>{busy?'جارٍ الحفظ…':pending?'إعادة محاولة الحفظ':caseId?'حفظ التأكيد':'حفظ المرتجع'}</button></>}
 {!order&&pending&&<button className="op-primary" disabled={busy} onClick={save}>إعادة محاولة الحفظ</button>}
 </section><section><h2>المرتجعات المسجلة</h2>{casesLoaded&&!cases.length&&<p>لا توجد مرتجعات مسجلة حتى الآن.</p>}{!casesLoaded&&!error&&<p role="status">جارٍ تحميل سجل المرتجعات…</p>}{cases.map(row=><article className="op-card" key={row.id}><strong>طلب {row.order_number}</strong><p>{row.status==='refunded'?`تم رد ${row.amount} ريال من ${row.refund_source_name}`:'بانتظار رد المبلغ'}</p><p>{row.shipping.kind==='none'?'بدون شحنة استرجاع':`${row.shipping.name} · ${row.shipping.amount} ريال · ${row.shipping.status==='completed'?'تم التنفيذ':'بانتظار التنفيذ'}`}</p><button disabled={disabled||loading} onClick={()=>openCase(row)}>عرض / تأكيد</button></article>)}</section></main>;
}
