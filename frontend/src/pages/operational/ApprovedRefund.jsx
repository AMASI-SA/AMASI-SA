import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Amount, Icon, IconBox } from './ApprovedFinanceUI';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { useBalanceRead } from './simpleBalancesHooks';
import { minorUnits, permittedOperations } from './simpleBalancesModel';
import { RefundDetails, RefundRequestForm } from './SimpleOrderRefunds';

const PAGE = 8;
const normalize = value => value.trim().replace(/[٠-٩]/g, c => String('٠١٢٣٤٥٦٧٨٩'.indexOf(c))).replace(/[۰-۹]/g, c => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(c)));
const stateLabels = { pending: 'بانتظار الرد', refunded: 'تم ردّه', cancelled: 'ملغى' };
function dateLabel(value) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn', {month:'short',day:'numeric',timeZone:'Asia/Riyadh'}).format(date) : '';
}
function historyOK(result, number) {
  return result?.page_size === PAGE && Number.isInteger(result.page) && result.page > 0 && result.page <= result.total_pages
    && Array.isArray(result.items) && result.items.length <= PAGE && result.items.every(i => i.id && (!number || i.order_number === number));
}
function Problems({ read }) {
  if (read.error) return <div className="note warning" role="alert">{read.error} <button className="textbtn" type="button" onClick={read.reload}>إعادة المحاولة</button></div>;
  if (read.loading) return <p role="status">جارٍ تحميل البيانات…</p>;
  return null;
}
export default function ApprovedRefund({ context, identity, onBack, onSaved }) {
  const [params, setParams] = useSearchParams();
  const number = normalize(params.get('order') || ''), id = params.get('refund_id') || '';
  const page = Math.max(1, Number.parseInt(params.get('refund_page'), 10) || 1);
  const all = params.get('refund_history') === 'all';
  const [query, setQuery] = useState(number);
  const [saved, setSaved] = useState(null);
  useEffect(() => { setQuery(number); }, [number]);
  const granted = permittedOperations(context);
  const allowed = granted.has('customer.refund_request') || granted.has('customer.refund_execute');
  const scope = JSON.stringify([identity, context.session_scope, context.permission_version]);
  function go(next) {
    const updated = new URLSearchParams(params);
    ['refund_id','refund_page','refund_history','order'].forEach(k => updated.delete(k));
    Object.entries(next).forEach(([k,v]) => {if(v) updated.set(k, String(v));});
    setParams(updated);
  }
  const record = useBalanceRead(async config => {
    const r = await api.refundOrder(number, {...config,params:{include_history:true}});
    if (r.order_number !== number || !Array.isArray(r.methods) || !Array.isArray(r.banks) || !historyOK(r.history,number)
      || ['paid_amount','refunded_amount','reserved_amount','available_amount'].some(key => minorUnits(r[key]) === null)) throw new Error('invalid order');
    return r;
  }, JSON.stringify([scope,'order',number]), allowed && Boolean(number) && !id && !all);
  const history = useBalanceRead(async config => {
    const r = await api.refunds({page, page_size:PAGE, ...(!all && number ? {order_number:number} : {})},config);
    if (!historyOK(r,all ? '' : number)) throw new Error('invalid refund history');
    return r;
  }, JSON.stringify([scope,'history',number,page,all]), allowed && !id && (all || Boolean(record.data) && page > 1));
  const detail = useBalanceRead(async config => {
    const r = await api.refund(id,config);
    if (r.id !== id || (number && r.order_number !== number)) throw new Error('invalid refund detail');
    return r;
  }, JSON.stringify([scope,'refund',id,number]), allowed && Boolean(id));
  if (!allowed) return <p className="note warning" role="alert">عرض طلبات الإرجاع غير متاح ضمن صلاحياتك.</p>;
  const updated = item => {
    setSaved({item,scope}); setQuery(item.order_number); go({order:item.order_number}); record.reload();
    if(item.status === 'refunded') onSaved?.(item);
  };
  const refresh = () => {record.reload();history.reload();detail.reload();};
  const item = saved?.scope === scope && saved.item.order_number === number ? saved.item : null;
  if (id) return <div className="panel rf-block rf-wrap"><Problems read={detail}/>{detail.data && <RefundDetails key={scope + id + detail.data.status} item={detail.data} context={context} onBack={() => go(all ? {refund_history:'all'} : {order:number})} onSaved={updated} onRecoveryRetired={refresh}/>}</div>;
  const rows = all || page > 1 ? history.data : record.data?.history;
  return <><button type="button" className="textbtn back" onClick={onBack}><Icon name="chevron-left"/>الحركات المالية</button>
    <div className="heading"><div><h1>إرجاع مبلغ لعميل</h1><p className="subtitle">ابحث برقم الطلب لمعرفة كم دفع العميل وكم تقدر ترجع له.</p></div></div>
    <div className="rf-wrap"><section className="panel rf-block"><form className="rf-search" onSubmit={e => { e.preventDefault(); const input = normalize(query); if (input) {setSaved(null); go({order:input}); if(input === number)record.reload();} }}>
      <label className="field"><span>رقم الطلب في سلة</span><input required maxLength="160" inputMode="numeric" autoComplete="off" value={query} onChange={e => setQuery(e.target.value)} placeholder="اكتب رقم الطلب"/></label>
      <button className="button primary" type="submit"><Icon name="search"/>عرض الطلب</button></form>
      {!number && !all && <button className="textbtn" type="button" onClick={() => go({refund_history:'all'})}>عرض سجل الإرجاعات</button>}</section>
    <Problems read={record}/>
    {item && <section className="rf-status" role="status"><IconBox name="circle-check" tone="c-in"/><div><h2>{item.status === 'pending' ? 'حُفظ طلب الإرجاع' : stateLabels[item.status]}</h2><p><Amount value={item.amount} currency={item.currency}/> · {item.status === 'pending' ? item.method === 'bank' ? 'بانتظار توثيق التحويل البنكي' : 'بانتظار تأكيد ' + (item.source?.name || 'منصة الدفع') : stateLabels[item.status]}</p></div></section>}
    {record.data && <RefundRequestForm key={scope+number+record.data.available_amount+record.data.reserved_amount} approved showForm={!item} context={context} order={record.data} onSaved={updated} onRecoveryRetired={refresh}/>}
    {item && <section className="panel rf-block"><h2>الخطوة التالية</h2><p>{item.status === 'pending' ? item.method === 'bank' ? 'بعد التحويل الفعلي، افتح الطلب لتوثيق وقت التحويل.' : 'طابق تأكيد المنصة مع هذا الطلب عند وصوله.' : 'يمكنك مراجعة سجل إرجاعات الطلب.'}</p><button type="button" className="button" onClick={() => setSaved(null)}>العودة إلى الطلب</button></section>}
    {(record.data || all) && <section className="panel rf-block"><h2>{all ? 'سجل الإرجاعات' : 'سجل إرجاعات الطلب'}</h2><Problems read={history}/>
      {rows && (rows.items.length ? rows.items.map(row => <div className="rf-row" key={row.id}><div><button className="textbtn" type="button" onClick={() => go({order:row.order_number,refund_id:row.id,refund_history:all?'all':''})}>
        {all && <bdi>#{row.order_number} · </bdi>}<Amount value={row.amount} currency={row.currency}/></button><small>{row.source?.name} · {row.reason}</small></div><div><span className={'pill '+(row.status==='refunded'?'st-done':'st-pending')}>{stateLabels[row.status] || 'قيد المراجعة'}</span><small>{dateLabel(row.created_at)} · {row.recorded_by?.name}</small></div></div>) : <p className="empty">لا توجد إرجاعات مسجلة ضمن السجل المتاح لك.</p>)}
      {rows?.total_pages > 1 && <nav className="movement-pages" aria-label="صفحات الإرجاعات"><span>صفحة {rows.page} من {rows.total_pages}</span><div>{[['السابق',-1],['التالي',1]].map(([label,by]) => <button type="button" key={label} className="button" disabled={rows.page+by<1||rows.page+by>rows.total_pages} onClick={() => go({order:number,refund_page:rows.page+by,refund_history:all?'all':''})}>{label}</button>)}</div></nav>}</section>}
    </div></>;
}
