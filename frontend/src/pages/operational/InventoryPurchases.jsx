import React, {useEffect, useRef, useState} from 'react';
import {operationalApi as api, requestId, messageFor} from './api';
import {Field, Notice} from './OpeningBalances';
import {inventoryIdentity, inventoryVariant} from './inventoryIdentity';
import {inventoryPersonalizations, assignedQuantity} from './inventoryPersonalizations';
import PurchaseConfiguration, {purchaseConfigurationPayload, configurationDescription} from './PurchaseConfiguration';

const today = () => { const d=new Date(); return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10); };
const cents = value => { if(!/^\d+(\.\d{1,2})?$/.test(String(value))) throw Error('أدخل مبلغًا صحيحًا بمنزلتين عشريتين كحد أقصى.'); const [a,b='']=String(value).split('.'); const n=Number(a)*100+Number(b.padEnd(2,'0')); if(!Number.isSafeInteger(n))throw Error('المبلغ أكبر من الحد المسموح.'); return n; };
const money = n => (n/100).toFixed(2);
const unitLabel = unit => unit === 'piece' ? 'قطعة' : unit;
const fresh = () => ({supplier_id:'',invoice_number:'',invoice_date:today(),lines:[],note:''});

export default function InventoryPurchases({context,source='mezan2',mode='purchase',supplierId='',fixedSource=null}) {
  const scope=context.session_scope, storageKey=`mezan.operational.inventory.v1:${scope}`;
  const [recovery]=useState(()=>{try{const command=JSON.parse(localStorage.getItem(storageKey)||'null'); if(command&&(!['purchase','payment'].includes(command.type)||command.body?.expected_session_scope!==scope||!command.body?.request_id))throw Error();return {command};}catch{return {error:'تعذر قراءة العملية المحفوظة؛ تحقق من التخزين قبل المتابعة.'};}});
  const [pending,setPending]=useState(recovery.command),[error,setError]=useState(recovery.error||''),[message,setMessage]=useState('');
  const [form,setForm]=useState(fresh),[catalog,setCatalog]=useState([]),[suppliers,setSuppliers]=useState([]),[accounts,setAccounts]=useState([]);
  const [rows,setRows]=useState([]),[stock,setStock]=useState([]),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[loadVersion,setLoadVersion]=useState(0),[loadFailed,setLoadFailed]=useState(false);
  const [kind,setKind]=useState('product'),[search,setSearch]=useState('');
  const [locations,setLocations]=useState([]),[reportSearch,setReportSearch]=useState(''),[reportDate,setReportDate]=useState('');
  const [payment,setPayment]=useState(null),[lookup,setLookup]=useState({supplier_id:supplierId,invoice_number:''}), lock=useRef(false);
  const writable=context.status==='active'&&!!context.permissions?.move, readable=mode!=='purchase'&&!!context.permissions?.reports;
  const entry=mode==='purchase'&&writable, paying=mode==='payment'&&writable;
  const disabled=!writable||loading||loadFailed||busy||!!pending||!!recovery.error;
  const refresh=async()=>{if(readable){const result=await api.inventoryPurchases();setRows(result.items);setStock(result.stock);}};
  useEffect(()=>{let alive=true;const load=async()=>{setLoading(true);setLoadFailed(false);setError(recovery.error||'');try{
    const [result,products,entities,...sources]=await Promise.all([readable?api.inventoryPurchases():Promise.resolve({items:[],stock:[]}),entry?api.inventoryCatalog():Promise.resolve({items:[]}),(entry||paying)?api.entities('supplier'):Promise.resolve({items:[]}),... (paying?['bank','cash','employee_custody'].map(kind=>api.entities(kind).then(r=>r.items.map(a=>({...a,kind})))):[])]);
    if(alive){setRows(result.items);setStock(result.stock);setCatalog(products.products?[...products.products,...products.items.filter(i=>i.kind==='component').map(i=>({...i,options:[]}))]:products.items);setLocations(products.locations||[]);setSuppliers(entities.items);setAccounts(sources.flat().filter(a=>!fixedSource||`${a.kind}:${a.id}`===fixedSource));}
  }catch(e){if(alive){setError(messageFor(e));setLoadFailed(true);}}finally{if(alive)setLoading(false);}};load();return()=>{alive=false;};},[readable,writable,entry,paying,loadVersion,recovery.error,fixedSource]);
  useEffect(()=>{const refreshOnFocus=()=>setLoadVersion(v=>v+1);window.addEventListener('focus',refreshOnFocus);return()=>window.removeEventListener('focus',refreshOnFocus);},[]);
  const totals=()=>form.lines.reduce((sum,line)=>{const q=Number(line.quantity);if(!Number.isSafeInteger(q)||q<=0)throw Error('الكمية يجب أن تكون عددًا صحيحًا موجبًا.'); const n=q*cents(line.unit_price),t=cents(line.tax);if(!Number.isSafeInteger(n+t+sum.gross))throw Error('الإجمالي أكبر من الحد المسموح.');return {net:sum.net+n,tax:sum.tax+t,gross:sum.gross+n+t};},{net:0,tax:0,gross:0});
  let preview=null;try{preview=totals();}catch{}
  const update=(index,key,value)=>setForm(f=>({...f,lines:f.lines.map((l,i)=>i===index?{...l,[key]:value}:l)}));
  const updateName=(index,rowIndex,key,value)=>update(index,'personalizations',(form.lines[index].personalizations||[]).map((row,i)=>i===rowIndex?{...row,[key]:value}:row));
  async function save(type){
    if(lock.current||!writable||recovery.error||mode==='reports'||(pending&&pending.type!==(paying?'payment':'purchase')))return;lock.current=true;setBusy(true);setError('');setMessage('');
    try{
      let command=pending;
      if(!command){let body;
        if(type==='purchase'){
          if(!form.supplier_id||!form.invoice_number.trim()||!form.invoice_date||!form.lines.length)throw Error('اختر المورد والمنتجات وأكمل بيانات الفاتورة.');totals();
          body={...form,invoice_number:form.invoice_number.trim(),lines:form.lines.map(line=>({item_id:line.item_id,kind:line.kind,...inventoryVariant(line),...inventoryPersonalizations(line),...purchaseConfigurationPayload(line),quantity:Number(line.quantity),unit_price:line.unit_price,tax:line.tax}))};
        }else{
          const account=accounts.find(a=>`${a.kind}:${a.id}`===payment?.account);if(!payment||!account||cents(payment.amount)<=0||cents(payment.amount)>cents(payment.row.outstanding))throw Error('اختر مصدر الدفع ومبلغًا لا يتجاوز المتبقي.');
          body={party_type:'supplier',party_id:payment.row.supplier_id,kind:'payment',direction:'outgoing',currency:'SAR',source_account_type:account.kind,bank_id:account.id,amount:payment.amount,allocations:[{obligation_id:payment.row.obligation_id,amount:payment.amount}],reference:payment.reference||'',source,receipt_id:null,business_date:today()};
        }
        command={type,...(type==='payment'?{lookup:{supplier_id:payment.row.supplier_id,invoice_number:payment.row.invoice_number}}:{}),body:{...body,request_id:requestId(),expected_session_scope:scope}};localStorage.setItem(storageKey,JSON.stringify(command));setPending(command);
      }
      const current=await api.context();if(current.session_scope!==scope||!current.permissions?.move||current.status!=='active')throw Error('تغيرت الجلسة أو الصلاحية؛ احتفظنا بالعملية للتحقق.');
      const result=command.type==='purchase'?await api.saveInventoryPurchase(command.body):await api.movement(command.body);
      localStorage.removeItem(storageKey);setPending(null);setForm(fresh());setPayment(null);setMessage('تم حفظ العملية التشغيلية');
      // The write response may show the author's invoice without granting report access.
      if(!readable&&command.type==='purchase'&&result?.id){setRows([result]);setMessage(`تم حفظ الفاتورة ${result.invoice_number} · الإجمالي ${result.gross} ريال`);}
      if(!readable&&command.type==='payment'&&command.lookup){const row=await api.inventoryPurchaseEntry(command.lookup.supplier_id,command.lookup.invoice_number);setRows([row]);}
      await refresh();
    }catch(e){if(e.response?.data?.detail?.not_applied||[400,422].includes(e.response?.status)){localStorage.removeItem(storageKey);setPending(null);}setError(e.response?messageFor(e):e.message);}
    finally{lock.current=false;setBusy(false);}
  }
  return <section dir="rtl" className="op-inventory"><h2>{mode==='reports'?'فواتير وكميات المشتريات':mode==='payment'?'سداد فاتورة مورد':'شراء المخزون'}</h2>{entry&&<p>اختر الأصناف المستلمة وكمياتها، ثم احفظ الشراء.</p>}<Notice error={error} message={message}/>
    {loading&&<p role="status">جارٍ تحميل المشتريات…</p>}{loadFailed&&<button disabled={loading||busy} onClick={()=>setLoadVersion(v=>v+1)}>إعادة تحميل بيانات المشتريات</button>}
    {pending&&<div className="op-card"><p>عملية محفوظة بانتظار تأكيد النتيجة؛ أعد المحاولة بالبيانات نفسها.</p>{((entry&&pending.type==='purchase')||(paying&&pending.type==='payment'))?<button disabled={busy||!writable} onClick={()=>save(pending.type)}>إعادة محاولة الحفظ</button>:<p>{pending.type==='purchase'?'تابع العملية من صفحة شراء المخزون.':'تابع العملية من الموردين ← سداد فاتورة مخزون.'}</p>}</div>}
    {entry&&<><fieldset disabled={disabled} className="op-card"><legend>فاتورة شراء مخزون مستلم</legend>
      <Field label="المورد"><select value={form.supplier_id} onChange={e=>setForm({...form,supplier_id:e.target.value})}><option value="">اختر المورد</option>{suppliers.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
      <div className="op-segment" aria-label="نوع المخزون">{[['product','منتج'],['component','مكوّن']].map(([value,label])=><button type="button" key={value} aria-pressed={kind===value} onClick={()=>setKind(value)}>{label}</button>)}</div>
      <Field label="بحث بالاسم أو الرمز"><input value={search} onChange={e=>setSearch(e.target.value)}/></Field>
      <div className="op-tile-grid">{catalog.filter(item=>item.kind===kind&&`${item.name} ${item.sku||''}`.toLowerCase().includes(search.trim().toLowerCase())).map(item=><button type="button" className="op-tile" key={inventoryIdentity(item)} disabled={!item.options&&form.lines.some(l=>inventoryIdentity(l)===inventoryIdentity(item))} onClick={()=>setForm({...form,lines:[...form.lines,{...item,item_id:item.id,quantity:'1',unit_price:'',tax:'0',...(item.options?{draft_key:requestId(),purchase_configuration:{state:'raw',selections:[],inputs:[]}}:{})}]})}>{item.image_url&&<img src={item.image_url} alt={item.name} width="64" height="64"/>}<strong>{item.name}</strong><small>{item.kind==='component'?'مكوّن':'منتج'} · {unitLabel(item.unit)}</small></button>)}</div>
      {!loading&&!catalog.length&&<p>لا توجد منتجات أو مكونات مؤهلة في ميزان 2.</p>}
      {form.lines.map((line,index)=><article className="op-card" key={line.draft_key||inventoryIdentity(line)}><h3>{line.name}</h3>{line.image_url&&<img src={line.image_url} alt={line.name} width="80" height="80"/>}<p>{unitLabel(line.unit)}</p><PurchaseConfiguration line={line} index={index} locations={locations} onChange={(key,value)=>update(index,key,value)}/>
        <Field label={`الكمية — ${line.name}`}><input type="number" min="1" step="1" value={line.quantity} onChange={e=>update(index,'quantity',e.target.value)}/></Field>
        <Field label={`سعر الوحدة قبل الضريبة — ${line.name}`}><input inputMode="decimal" value={line.unit_price} onChange={e=>update(index,'unit_price',e.target.value)}/></Field>
        <Field label={`ضريبة البند — ${line.name}`}><input inputMode="decimal" value={line.tax} onChange={e=>update(index,'tax',e.target.value)}/></Field><button onClick={()=>setForm({...form,lines:form.lines.filter((_,i)=>i!==index)})}>إزالة {line.name}</button>
        {!!line.customization_issues?.length&&<p role="alert">خيارات المنتج غير مكتملة في ميزان 2؛ يلزم استكمال تعريف الخيارات قبل تخصيصها.</p>}
        {!line.purchase_configuration&&line.kind==='product'&&!!line.customization_fields?.length&&!line.customization_issues?.length&&<section aria-label={`تخصيص خيارات — ${line.name}`}>
          <h4>توزيع الكمية حسب خيارات المنتج</h4><p>الحقول التالية من تعريف المنتج، وتتبع اللون والمقاس المحددين في هذا البند.</p><p>الكمية المشتراة {line.quantity} · المخصص {assignedQuantity(line)} · غير المخصص {Math.max(0,Number(line.quantity)-assignedQuantity(line))}</p>
          {assignedQuantity(line)>Number(line.quantity)&&<p role="alert">توزيع التخصيصات يتجاوز كمية هذا الخيار.</p>}
          {(line.personalizations||[]).map((row,rowIndex)=><div key={rowIndex} className="op-card">
            {line.customization_fields.map(field=>{const Control=field.type==='textarea'?'textarea':'input';return <Field key={field.id} label={`${field.name}${field.required?' *':''} ${rowIndex+1} — ${line.name}`}><Control maxLength={field.type==='textarea'?1000:100} value={row.values.find(v=>v.option_id===field.id)?.value||''} onChange={e=>updateName(index,rowIndex,'values',[...row.values.filter(v=>v.option_id!==field.id),{option_id:field.id,value:e.target.value}])}/></Field>;})}
            <Field label={`كمية التخصيص ${rowIndex+1} — ${line.name}`}><input type="number" min="1" step="1" value={row.quantity} onChange={e=>updateName(index,rowIndex,'quantity',e.target.value)}/></Field>
            <button type="button" onClick={()=>update(index,'personalizations',line.personalizations.filter((_,i)=>i!==rowIndex))}>إزالة التخصيص {rowIndex+1} — {line.name}</button>
          </div>)}
          <button type="button" disabled={(line.personalizations||[]).length>=100} onClick={()=>update(index,'personalizations',[...(line.personalizations||[]),{values:[],quantity:'1'}])}>إضافة تخصيص — {line.name}</button>
        </section>}
      </article>)}
      <Field label="رقم فاتورة المورد"><input value={form.invoice_number} onChange={e=>setForm({...form,invoice_number:e.target.value})}/></Field>
      <Field label="تاريخ الفاتورة"><input type="date" value={form.invoice_date} onChange={e=>setForm({...form,invoice_date:e.target.value})}/></Field>
      <Field label="ملاحظات اختيارية"><textarea value={form.note} onChange={e=>setForm({...form,note:e.target.value})}/></Field>
      {preview&&<p role="status">الصافي {money(preview.net)} · الضريبة {money(preview.tax)} · الإجمالي {money(preview.gross)} ريال</p>}
      <button className="op-primary" onClick={()=>save('purchase')}>{busy?'جارٍ الحفظ…':'حفظ الشراء'}</button>
    </fieldset></>}
    {paying&&<fieldset disabled={disabled} className="op-card"><legend>سداد فاتورة معروفة</legend><Field label="مورد الفاتورة للسداد"><select disabled={!!supplierId} value={lookup.supplier_id} onChange={e=>{setLookup({...lookup,supplier_id:e.target.value});setRows([]);setPayment(null);}}><option value="">اختر المورد</option>{suppliers.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></Field><Field label="رقم الفاتورة للسداد"><input value={lookup.invoice_number} onChange={e=>{setLookup({...lookup,invoice_number:e.target.value});setRows([]);setPayment(null);}}/></Field><button disabled={!lookup.supplier_id||!lookup.invoice_number.trim()} onClick={async()=>{setLoading(true);setError('');setRows([]);setPayment(null);try{setRows([await api.inventoryPurchaseEntry(lookup.supplier_id,lookup.invoice_number.trim())]);}catch(e){setError(messageFor(e));}finally{setLoading(false);}}}>عرض الفاتورة للسداد</button></fieldset>}
    {mode==='reports'&&<><p>هذه كميات مشتراة تشغيليًا. المخزون الفعلي غير مثبت بهذه الفواتير؛ يُعرض مستقلاً من مصادر المستودع.</p><Field label="بحث في فواتير المشتريات"><input value={reportSearch} onChange={e=>setReportSearch(e.target.value)}/></Field><Field label="تاريخ المشتريات"><input type="date" value={reportDate} onChange={e=>setReportDate(e.target.value)}/></Field></>}{(readable||(paying&&rows.length>0))&&<><h3>{readable?'المشتريات المسجلة':'الفاتورة المحددة للسداد'}</h3>{rows.filter(row=>(!supplierId||row.supplier_id===supplierId)&&(!reportDate||row.invoice_date===reportDate)&&(!reportSearch||[row.supplier_name,row.invoice_number,...row.lines.map(l=>l.name+' '+configurationDescription(l))].join(' ').toLowerCase().includes(reportSearch.trim().toLowerCase()))).map(row=><article className="op-card" key={row.id}><h4>{row.supplier_name} · {row.invoice_number}</h4><p>{row.invoice_date} · الصافي {row.net} · الضريبة {row.tax} · الإجمالي {row.gross}</p>{row.lines.map(l=><div key={inventoryIdentity(l)}><p>{l.image_url&&<img src={l.image_url} alt={l.name} width="48" height="48"/>}{l.name} · المشتراة {l.quantity} {unitLabel(l.unit)} · المرتجعة {l.returned_quantity??0} · المتبقية {l.remaining_quantity??l.quantity}</p><p>{configurationDescription(l)} {l.location_name?' · الموقع '+l.location_name:''}</p><p>تكلفة الوحدة {l.unit_price??'غير مثبت'} · الصافي {l.net??'غير مثبت'} · الضريبة {l.tax??'غير مثبت'} · الإجمالي {l.gross??'غير مثبت'}</p>{!!l.personalizations?.length&&<div><strong>توزيع الكمية المشتراة الأصلي حسب الخيارات</strong>{l.personalizations.map((p,i)=><p key={i}>{p.values?.length?p.values.map(v=>`${v.option_name}: ${v.value}`).join(' · '):p.name} · {p.quantity} {unitLabel(l.unit)}</p>)}<p>بدون تخصيص · {l.unallocated_quantity??(Number(l.quantity)-assignedQuantity(l))} {unitLabel(l.unit)}</p></div>}</div>)}<p>المسدد {row.settled} · المتبقي {row.outstanding}</p>{paying&&Number(row.outstanding)>0&&<button disabled={disabled} onClick={()=>setPayment({row,amount:row.outstanding,account:fixedSource?(accounts.some(a=>`${a.kind}:${a.id}`===fixedSource)?fixedSource:''):accounts.some(a=>a.kind==='bank'&&a.id===context.operational_banks?.default_bank_id)?`bank:${context.operational_banks.default_bank_id}`:'',reference:''})}>سداد الفاتورة {row.invoice_number}</button>}</article>)}
      {mode==='reports'&&<h3>إجمالي الكميات المشتراة</h3>}{(mode==='reports'?stock:[]).map(l=><p key={inventoryIdentity(l)}>{l.name} · المشتراة {l.quantity} {unitLabel(l.unit)} · المرتجعة {l.returned_quantity??0} · المتبقية {l.remaining_quantity??l.quantity}</p>)}
    </>}
    {payment&&paying&&<fieldset className="op-card" disabled={disabled}><legend>سداد فاتورة {payment.row.invoice_number}</legend><Field label="مصدر السداد"><select disabled={!!fixedSource} value={payment.account} onChange={e=>setPayment({...payment,account:e.target.value})}><option value="">اختر بنكًا أو صندوقًا أو عهدة</option>{accounts.map(a=><option key={`${a.kind}:${a.id}`} value={`${a.kind}:${a.id}`}>{a.name}</option>)}</select></Field><Field label="مبلغ السداد"><input inputMode="decimal" value={payment.amount} onChange={e=>setPayment({...payment,amount:e.target.value})}/></Field><Field label="مرجع السداد الاختياري"><input value={payment.reference} onChange={e=>setPayment({...payment,reference:e.target.value})}/></Field><button onClick={()=>save('payment')}>حفظ السداد</button></fieldset>}
    {!writable&&!readable&&<p role="alert">لا تتوفر صلاحية للمشتريات التشغيلية.</p>}
  </section>;
}
