import React, { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Amount, Icon } from './ApprovedFinanceUI';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { useBalanceRead } from './simpleBalancesHooks';
import { usePendingBalanceCommand } from './simpleBalancesPending';
import { decimalAmount, minorUnits, permittedOperations } from './simpleBalancesModel';
import CustomerAdjustments, { ADJUSTMENT_PAGE_SIZE, AdjustmentPages, AdjustmentReadState, normalizeOrderNumber, validAdjustmentPage } from './CustomerAdjustments';
import { CustomerReceipt, CustomerReceiptUpload } from './CustomerAdjustmentReceipt';

const localTime = value => {
  const date = new Date(value || Date.now());
  return Number.isNaN(date.getTime()) ? '' : new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
const dateLabel = value => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'غير موثق' : new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn', {dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Riyadh'}).format(date);
};
const sourceKey = source => JSON.stringify([source.kind, source.id]);
const sallaMethods = [['mada','مدى'],['visa','Visa'],['mastercard','Mastercard'],['credit_card','بطاقة ائتمانية'],
  ['debit_card','بطاقة بنكية'],['apple_pay','Apple Pay'],['google_pay','Google Pay'],['stc_pay','STC Pay']];
const validHistory = (result,number='') => validAdjustmentPage(result) && result.items.every(item => Boolean(item.id)
  && item.status === 'confirmed' && (!number || item.order_number === number) && minorUnits(item.amount) !== null
  && ['bank','provider'].includes(item.source?.kind) && Boolean(item.source?.id));

export function CustomerCollectionForm({ context, order, onSaved, onRecoveryRetired, readOnly = false }) {
  const allowed = !readOnly && context.status === 'active' && order.can_record === true && permittedOperations(context).has('customer.collection');
  const [form, setForm] = useState({amount:order.suggested_collection_amount || order.pending_collection_amount,source:'',payment_method:'',fee_mode:'',
    original_reference:'',reference:'',executed_at:localTime(),reason:'',receipt_id:'',confirmed:false});
  const [quote, setQuote] = useState(null), [quoting, setQuoting] = useState(false), [quoteError, setQuoteError] = useState(''), [uploading, setUploading] = useState(false);
  const mounted = useRef(false), quoteLock = useRef(false), draftVersion = useRef(0);
  useEffect(() => {mounted.current = true;return () => {mounted.current = false;};}, []);
  const mutation = usePendingBalanceCommand({context,
    storageIdentity:JSON.stringify(['customer.collection',context.actor.id,context.session_scope,order.id || order.order_id || order.order_number]),
    validate:value => value.action === 'customer.collection' && value.target_id === null && value.command.order_number === order.order_number,
    execute:value => api.recordCustomerCollection(value.command),
    validateSaved:(result,pending) => result.item?.status === 'confirmed' && Boolean(result.item.id)
      && result.item.order_number === pending.command.order_number && minorUnits(result.item.amount) === minorUnits(pending.command.amount)
      && result.item.source?.kind === pending.command.source_type && result.item.source.id === pending.command.source_id,
    onSaved:result => onSaved(result.item),onRetired:onRecoveryRetired});
  const pending = mutation.pending?.command;
  useEffect(() => {
    if (!pending) return;
    setForm({amount:pending.amount,source:JSON.stringify([pending.source_type,pending.source_id]),payment_method:pending.payment_method || '',
      fee_mode:pending.fee_mode,original_reference:pending.original_reference || '',reference:pending.reference,
      executed_at:localTime(pending.executed_at),reason:pending.reason,receipt_id:pending.receipt_id || '',confirmed:pending.confirmed === true});
  }, [pending]);
  const disabled = !allowed || mutation.busy || mutation.blocked || mutation.recovering || Boolean(pending);
  const source = order.sources.find(item => sourceKey(item) === form.source);
  const version = JSON.stringify([form.amount,form.source,form.payment_method,form.fee_mode,form.original_reference,form.reference,form.executed_at]);
  const visibleQuote = quote?.version === version ? quote : null;
  const remaining = minorUnits(order.pending_collection_amount);
  const canCollect = remaining !== null && remaining > 0n;
  function choose(patch) {
    if (disabled) return;
    draftVersion.current += 1; setForm(value => ({...value,...patch,confirmed:false})); setQuote(null); setQuoteError('');
  }
  function quoteBody() {
    const amount = decimalAmount(form.amount);
    if (!amount || remaining === null || minorUnits(amount) > remaining) throw {localMessage:'أدخل المبلغ المقبوض فعليًا ضمن المتبقي على العميل.'};
    if (!source) throw {localMessage:'اختر الحساب الذي استقبل المبلغ.'};
    if (source.kind === 'provider' && !['new_transaction','same_transaction'].includes(form.fee_mode)) throw {localMessage:'حدد هل التحصيل عملية دفع جديدة أم إضافة للعملية الأصلية.'};
    if (source.kind === 'provider' && source.id === 'salla' && !sallaMethods.some(([key]) => key === form.payment_method)) throw {localMessage:'اختر طريقة الدفع في سلة لحساب الرسوم.'};
    if (source.kind === 'provider' && form.fee_mode === 'same_transaction' && !form.original_reference.trim()) throw {localMessage:'اكتب مرجع عملية الدفع الأصلية.'};
    if (!form.reference.trim()) throw {localMessage:'اكتب مرجع التحصيل الفعلي.'};
    const executed = new Date(form.executed_at);
    if (!form.executed_at || Number.isNaN(executed.getTime()) || executed.getTime() > Date.now() + 60000) throw {localMessage:'اختر وقت التحصيل الفعلي، دون تاريخ مستقبلي.'};
    return {order_number:order.order_number,amount,source_type:source.kind,source_id:source.id,
      payment_method:source.kind === 'bank' ? '' : source.id === 'salla' ? form.payment_method : source.id,
      fee_mode:source.kind === 'bank' ? 'new_transaction' : form.fee_mode,
      ...(source.kind === 'provider' && form.fee_mode === 'same_transaction' ? {original_reference:form.original_reference.trim()} : {}),
      executed_at:executed.toISOString(),reference:form.reference.trim()};
  }
  async function review() {
    if (disabled || uploading || quoteLock.current) return;
    quoteLock.current = true; setQuoting(true); setQuoteError('');
    const current = draftVersion.current;
    try {
      const body = quoteBody(), result = await api.quoteCustomerCollection(body);
      if (!/^[a-f0-9]{64}$/.test(result?.quote_hash || '') || result.expected_basis_hash !== order.basis_hash
        || !['estimated','unavailable','none'].includes(result.fee_status)
        || result.fee_status !== 'unavailable' && (minorUnits(result.fee_amount) === null || minorUnits(result.net_amount) === null)) throw new Error('invalid collection quote');
      if (mounted.current && current === draftVersion.current) setQuote({version,body,result});
    } catch (failure) {
      if (mounted.current && current === draftVersion.current) setQuoteError(simpleBalanceError(failure));
    } finally {
      quoteLock.current = false;
      if (mounted.current) setQuoting(false);
    }
  }
  async function submit(event) {
    event.preventDefault();
    if (!allowed || uploading || quoting) return;
    await mutation.submit('customer.collection',null,() => {
      if (!visibleQuote) throw {localMessage:'راجع مبلغ التحصيل ورسومه قبل التأكيد.'};
      if (form.reason.trim().length < 3) throw {localMessage:'اكتب سبب التحصيل بوضوح.'};
      if (!form.confirmed) throw {localMessage:'أكد أن المبلغ وصل فعليًا إلى الحساب المختار.'};
      return {...visibleQuote.body,expected_basis_hash:visibleQuote.result.expected_basis_hash,expected_quote_hash:visibleQuote.result.quote_hash,
        reason:form.reason.trim(),confirmed:true,...(form.receipt_id ? {receipt_id:form.receipt_id} : {})};
    });
  }
  return <><section className="panel rf-block"><h2>طلب <bdi>#{order.order_number}</bdi></h2>
    <div className="rf-metrics">{[['المدفوع المؤكد',order.paid_amount],['منه تحصيل إضافي موثّق',order.confirmed_collected_amount],
      ['الإجمالي الحالي',order.current_total],['المتبقي على العميل',order.pending_collection_amount]].map(([label,value]) =>
      <div className="metric" key={label}><small>{label}</small><strong><Amount value={value} currency={order.currency}/></strong></div>)}</div>
  </section>
  {mutation.error && <p className="note warning" role="alert">{mutation.error}</p>}
  {mutation.recovering && <p role="status">جارٍ استعادة العملية السابقة من الخادم…</p>}
  {mutation.blocked && <button className="button" type="button" disabled={mutation.busy || mutation.recovering} onClick={mutation.recover}>استعادة العملية السابقة</button>}
  {pending && <p className="note" role="status">هناك تحصيل بانتظار تأكيد النتيجة. إعادة المحاولة تستخدم العملية نفسها.</p>}
  {allowed && (canCollect || pending) ? <form className="panel rf-block" onSubmit={submit} noValidate><h2>تسجيل تحصيل منفّذ</h2><div className="fields">
    <label className="field"><span>المبلغ المقبوض فعليًا</span><input required inputMode="decimal" value={form.amount} disabled={disabled} onChange={e => choose({amount:e.target.value})}/></label>
    <label className="field"><span>الحساب الذي استقبل المبلغ</span><select required value={form.source} disabled={disabled} onChange={e => choose({source:e.target.value,payment_method:'',fee_mode:'',original_reference:''})}>
      <option value="">اختر البنك أو منصة الدفع</option>{order.sources.map(item => <option key={sourceKey(item)} value={sourceKey(item)}>{item.name} · {item.kind === 'bank' ? 'بنك' : 'منصة دفع'}</option>)}</select></label>
    {source?.kind === 'provider' && <><label className="field"><span>نوع عملية التحصيل</span><select required value={form.fee_mode} disabled={disabled} onChange={e => choose({fee_mode:e.target.value,original_reference:''})}>
      <option value="">اختر نوع العملية</option><option value="new_transaction">عملية دفع جديدة</option><option value="same_transaction">إضافة إلى عملية الدفع الأصلية</option></select></label>
      {form.fee_mode === 'same_transaction' && <label className="field"><span>مرجع عملية الدفع الأصلية</span><input required maxLength="160" value={form.original_reference} disabled={disabled} onChange={e => choose({original_reference:e.target.value})}/><small>يُستخدم للتحقق من العملية الأصلية ومنع تكرار الرسم الثابت.</small></label>}
      {source.id === 'salla' && <label className="field"><span>طريقة الدفع في سلة</span><select required value={form.payment_method} disabled={disabled} onChange={e => choose({payment_method:e.target.value})}>
        <option value="">اختر طريقة الدفع</option>{sallaMethods.map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label>}</>}
    <label className="field"><span>مرجع التحصيل الفعلي</span><input required maxLength="160" value={form.reference} disabled={disabled} onChange={e => choose({reference:e.target.value})}/></label>
    <label className="field"><span>وقت التحصيل الفعلي (بتوقيت جهازك)</span><input required type="datetime-local" value={form.executed_at} disabled={disabled} onChange={e => choose({executed_at:e.target.value})}/></label>
    <label className="field"><span>سبب التحصيل</span><input required minLength="3" maxLength="1000" value={form.reason} disabled={disabled} onChange={e => choose({reason:e.target.value})}/></label>
    <CustomerReceiptUpload context={context} orderNumber={order.order_number} value={form.receipt_id} disabled={disabled} operation="customer.collection" onChange={receipt_id => choose({receipt_id})} onBusyChange={setUploading}/>
    </div>
    {quoteError && <p className="note warning" role="alert">{quoteError}</p>}
    {!pending && <button className="button" type="button" disabled={disabled || quoting || uploading} onClick={review}>{quoting ? 'جارٍ حساب الرسوم…' : 'مراجعة التحصيل والرسوم'}</button>}
    {visibleQuote && <section className="adjustment-quote" aria-label="مراجعة مبلغ التحصيل"><h3>راجع التحصيل</h3><dl>
      <div><dt>المبلغ المقبوض</dt><dd><Amount value={visibleQuote.body.amount} currency={order.currency}/></dd></div>
      <div><dt>{visibleQuote.result.fee_status === 'estimated' ? 'رسوم محسوبة تقديريًا' : 'الرسوم'}</dt><dd>{visibleQuote.result.fee_status === 'unavailable' ? 'بانتظار تحديد الرسوم' : <Amount value={visibleQuote.result.fee_amount} currency={order.currency}/>}</dd></div>
      <div><dt>الصافي</dt><dd>{visibleQuote.result.fee_status === 'unavailable' ? 'بانتظار تحديد الرسوم' : <Amount value={visibleQuote.result.net_amount} currency={order.currency}/>}</dd></div>
    </dl>{visibleQuote.result.fee_status === 'unavailable' && <p className="note">يُثبت المبلغ المقبوض كاملًا، وتبقى الرسوم والصافي للمراجعة.</p>}
      <label className="sb-check"><input type="checkbox" checked={form.confirmed} disabled={disabled} onChange={e => setForm(value => ({...value,confirmed:e.target.checked}))}/><span>وصل هذا المبلغ فعليًا إلى الحساب المختار</span></label>
    </section>}
    {(visibleQuote || pending) && <button className="button primary" type="submit" disabled={mutation.busy || mutation.recovering || mutation.blocked || uploading || quoting}>{mutation.busy ? 'جارٍ التسجيل…' : pending ? 'إعادة محاولة العملية نفسها' : 'تأكيد وتسجيل التحصيل'}</button>}
  </form> : !canCollect && <p className="note">لا يوجد مبلغ متبقٍ للتحصيل في هذا الطلب.</p>}</>;
}

function CollectionHistory({ rows, identity, onPage }) {
  return <section className="panel rf-block"><h2>التحصيلات المؤكدة</h2>
    {rows.items.length ? rows.items.map(item => <article className="rf-row collection-record" key={item.id}><div><strong><bdi>طلب #{item.order_number}</bdi> · <Amount value={item.amount} currency={item.currency}/></strong>
      <small>{item.source?.name} · {item.reference}</small><small>{dateLabel(item.executed_at)} · {item.recorded_by?.name || 'غير موثق'}</small>
      {item.match_status === 'awaiting_source_match' && <small>بانتظار مطابقة التحصيل مع سجل المصدر.</small>}
      {item.match_status === 'needs_review' && <small className="issue-badge">التحصيل موثّق، ومطابقته مع سجل المصدر تحتاج مراجعة.</small>}
      <CustomerReceipt id={item.receipt_id} identity={identity}/></div><div><span className="pill st-done">تحصيل مؤكد</span>
      <small>{item.fee_status === 'unavailable' ? <span className="issue-badge">رسوم التحصيل تحتاج مراجعة</span> : <>{item.fee_status === 'estimated' ? 'رسوم تقديرية ' : 'الرسوم '}<Amount value={item.fee_amount} currency={item.currency}/></>}</small></div></article>)
      : <p className="empty">لا توجد تحصيلات مؤكدة ضمن السجل المتاح لك.</p>}
    <AdjustmentPages result={rows} onPage={onPage} label="صفحات التحصيلات المؤكدة"/>
  </section>;
}

export default function ApprovedCustomerCollections({ context, identity, onBack, onSaved, readOnly = false }) {
  const [params,setParams] = useSearchParams();
  const number = normalizeOrderNumber(params.get('order')), all = params.get('collection_history') === 'all';
  const page = Math.max(1,Number.parseInt(params.get('collection_page'),10) || 1);
  const [query,setQuery] = useState(number), [saved,setSaved] = useState(null);
  useEffect(() => {setQuery(number);}, [number]);
  const allowed = permittedOperations(context).has('customer.collection');
  const scope = JSON.stringify([identity,context.actor.id,context.session_scope,context.permission_version]);
  const go = next => {
    const updated = new URLSearchParams(params);
    ['order','collection_page','collection_history'].forEach(key => updated.delete(key));
    Object.entries(next).forEach(([key,value]) => {if(value)updated.set(key,String(value));});setParams(updated);
  };
  const record = useBalanceRead(async config => {
    const result = await api.customerCollectionOrder(number,{...config,params:{include_history:true}});
    if (result?.order_number !== number || !Array.isArray(result.sources) || result.sources.some(source => !['bank','provider'].includes(source.kind) || !source.id || !source.name)
      || !validHistory(result.history,number) || !/^[a-f0-9]{64}$/.test(result.basis_hash || '')
      || ['paid_amount','current_total','confirmed_collected_amount','pending_collection_amount'].some(key => minorUnits(result[key]) === null)) throw new Error('invalid collection order');
    return result;
  },JSON.stringify([scope,'collection-order',number]),allowed && Boolean(number) && !all);
  const history = useBalanceRead(async config => {
    const result = await api.customerCollections({page,page_size:ADJUSTMENT_PAGE_SIZE,...(!all && number ? {order_number:number} : {})},config);
    if (!validHistory(result,all ? '' : number)) throw new Error('invalid collection history');
    return result;
  },JSON.stringify([scope,'collection-history',number,page,all]),allowed && (all || Boolean(record.data) && page > 1));
  if (!allowed) return <p className="note warning" role="alert">تحصيل مبالغ العملاء غير متاح ضمن صلاحياتك.</p>;
  const refresh = () => {record.reload();history.reload();};
  const confirmed = item => {setSaved({scope,item});go({order:item.order_number});refresh();onSaved?.(item);};
  const rows = all || page > 1 ? history.data : record.data?.history;
  return <><button className="textbtn back" type="button" onClick={onBack}><Icon name="chevron-left"/>الحركات المالية</button>
    <div className="heading"><div><h1>تحصيل متبقي طلب</h1><p className="subtitle">راجع المبلغ المتبقي على العميل، ثم وثّق ما وصل فعليًا إلى البنك أو منصة الدفع.</p></div></div>
    <div className="rf-wrap"><div className="tabset adjustment-tabs" role="group" aria-label="سجل تحصيل مبالغ العملاء">
      <button className={!all && !number?'on':''} aria-pressed={!all && !number} type="button" onClick={() => {setSaved(null);go({});}}>بانتظار التحصيل</button>
      <button className={all?'on':''} aria-pressed={all} type="button" onClick={() => {setSaved(null);go({collection_history:'all'});}}>تحصيلات مؤكدة</button></div>
      <section className="panel rf-block"><form className="rf-search" onSubmit={event => {event.preventDefault();const input=normalizeOrderNumber(query);if(input){setSaved(null);go({order:input});if(input===number)record.reload();}}}>
        <label className="field"><span>رقم الطلب في سلة</span><input required maxLength="160" inputMode="numeric" value={query} onChange={e => setQuery(e.target.value)} placeholder="اكتب رقم الطلب"/></label>
        <button className="button primary" type="submit"><Icon name="search"/>عرض الطلب</button></form></section>
      {!number && !all && <CustomerAdjustments context={context} identity={identity} direction="collection" page={page} onPage={next => go({collection_page:next})} onOrder={order => go({order})}/>}
      <AdjustmentReadState read={record}/>
      {saved?.scope===scope && saved.item.order_number===number && <p className="note" role="status">تم تسجيل تحصيل <Amount value={saved.item.amount} currency={saved.item.currency}/> في {saved.item.source.name}.</p>}
      {record.data && <CustomerCollectionForm key={scope+number+record.data.basis_hash} context={context} order={record.data} readOnly={readOnly} onSaved={confirmed} onRecoveryRetired={refresh}/>}
      <AdjustmentReadState read={history}/>
      {rows && <CollectionHistory rows={rows} identity={scope} onPage={next => go({order:number,collection_page:next,collection_history:all?'all':''})}/>}
    </div></>;
}
