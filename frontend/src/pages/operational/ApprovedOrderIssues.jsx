import React, { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import http from '../../lib/api';
import { Heading, Icon, IconBox, kindStyles } from './ApprovedFinanceUI';
import { useBalanceRead } from './simpleBalancesHooks';

const pageSize = 8;
const subject = { provider: 'مستحقات منصة الدفع', courier: 'الشحن', store_driver: 'التوصيل والتحصيل', unassigned: 'جهة التوصيل' };
const category = code => {
  if (code.includes('refund')) return 'استرجاع العميل';
  if (code.includes('fee') && code.startsWith('provider_')) return 'عمولة الدفع';
  if (code.includes('settlement') || code.includes('settled_capture')) return 'تسوية المنصة';
  if (code.includes('fee') || code.includes('tariff') || code.includes('rate')) return 'أجرة الشحن';
  if (code.includes('collection')) return 'تحصيل العميل';
  if (code.includes('delivery')) return 'إثبات التوصيل';
  return '';
};
const guidance = {
  'عمولة الدفع': ['تعتمد قيمة صافي المستحق على مطابقة العمولة.', 'طابق رقم الطلب ومرجع العملية مع سطر العمولة في كشف المنصة.'],
  'تسوية المنصة': ['تحتاج حصة هذا الطلب من التسوية إلى مطابقة.', 'راجع مرجع الدفعة والمبلغ في كشف التسوية.'],
  'استرجاع العميل': ['مبلغ الاسترجاع المرتبط بهذه العملية يحتاج مطابقة.', 'راجع مرجع الاسترجاع في الطلب وفي كشف المنصة.'],
  'أجرة الشحن': ['أجرة هذا الطلب تحتاج مراجعة الدليل المذكور.', 'قارن البوليصة وأجرة الشحنة في كشف الشركة بسبب المراجعة الظاهر.'],
  'تحصيل العميل': ['مبلغ التحصيل محل المراجعة يحتاج إثباتًا.', 'راجع إيصال التحصيل أو كشف الشركة الخاص بهذا الطلب.'],
  'إثبات التوصيل': ['تاريخ التوصيل أو دليله يؤثر في تحديد فترة احتساب الطلب.', 'راجع حالة الطلب وتاريخها في سجل التوصيل والبوليصة.'],
};
const sourceLabel = order => order.order_number ? '#' + order.order_number : 'معرّف الطلب ' + order.order_id;
function snapshotDate(value) {
  const date = new Date(value || '');
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn', {
    dateStyle: 'medium', timeZone: 'Asia/Riyadh',
  }).format(date) : '';
}
function reasonTitle(group) {
  return [...new Set(group.reasons.map(r => category(r.code)).filter(Boolean))].join('، ') || subject[group.kind];
}
function valid(result) {
  return Array.isArray(result?.items) && Number.isInteger(result.total) && Number.isInteger(result.page)
    && result.page_size === pageSize && Array.isArray(result.unidentified) && result.items.every(row =>
      typeof row.order_id === 'string' && Array.isArray(row.groups) && row.groups.length > 0 && row.groups.every(group =>
        typeof group.name === 'string' && Array.isArray(group.reasons) && group.reasons.every(reason =>
          typeof reason.code === 'string' && typeof reason.message === 'string')));
}
function Back({ children, onClick }) {
  return <button className="textbtn back" type="button" onClick={onClick}><Icon name="chevron-left"/>{children}</button>;
}
function Detail({ row, name, onBack }) {
  const names = row.groups.map(group => group.name);
  return <><Back onClick={onBack}>طلبات تحتاج مراجعة · {name}</Back>
    <Heading title={row.order_number ? 'الطلب #' + row.order_number : sourceLabel(row)} subtitle={names.join('، ')}/>
    {row.groups.map(group => <section className="issue-detail panel" key={group.kind + ':' + group.id}>
      <div className="issue-detail-head"><div><IconBox name={kindStyles[group.kind]?.[1] || 'truck'} tone={kindStyles[group.kind]?.[2] || 'party'}/>
        <h2>{names.length === 1 ? reasonTitle(group) : group.name}</h2></div>
        <span className="issue-badge"><Icon name="clock-3"/>تحتاج مراجعة</span></div>
      {group.known && <div className="issue-known"><Icon name="circle-check"/><div><strong>المؤكد في الطلب</strong><p>{group.known}</p></div></div>}
      {group.reasons.map(reason => {
        const advice = guidance[category(reason.code)];
        return <div key={reason.code + reason.message}><div className="issue-reason"><h3>سبب المراجعة</h3><p>{reason.message}</p></div>
          <dl className="issue-facts"><div><dt>أثره على الرصيد</dt><dd>{advice?.[0] || 'لا تحدد اللقطة مبلغًا منفصلًا لهذا السبب.'}</dd></div>
            <div><dt>مصدر المعلومة</dt><dd>لقطة الأرصدة التشغيلية · {group.name}</dd></div></dl>
          <div className="issue-next"><Icon name="list-checks"/><div><strong>وش أراجع؟</strong><p>{advice?.[1] || 'راجع تفاصيل السبب ودليل الطلب في المصدر.'}</p></div></div></div>;
      })}
      {!group.reasons.length && <p className="empty">سبب المراجعة غير متوفر في اللقطة المحفوظة.</p>}
    </section>)}</>;
}

export default function ApprovedOrderIssues({ context, identity, resource, onBack, onReviewCollections }) {
  const [params, setParams] = useSearchParams();
  const page = Math.max(1, parseInt(params.get('issue_page'), 10) || 1);
  const query = (params.get('issue_q') || '').slice(0, 160);
  const orderId = params.get('issue_order') || '';
  const [draft, setDraft] = useState(query);
  const searchTimer = useRef(null);
  useEffect(() => { setDraft(query); return () => clearTimeout(searchTimer.current); }, [query, resource?.kind, resource?.id]);
  const scope = resource ? context.resources.find(r => r.kind === resource.kind && r.id === resource.id) : null;
  const permitted = item => Boolean(subject[item?.kind] && item.access?.movements === 'all');
  const allowed = resource ? permitted(scope) : context.actor.is_owner || context.resources.some(permitted);
  const name = scope?.name || 'كل الجهات';
  const key = JSON.stringify([identity, context.session_scope, context.permission_version, resource?.kind, resource?.id, query, page]);
  const read = useBalanceRead(async config => {
    const { data } = await http.get('/operational-balances/workspace/order-issues', {
      ...config, timeout: 30000, params: { ...(scope && { kind: scope.kind, id: scope.id }), page, ...(query && { q: query }) },
    });
    if (!valid(data)) throw new Error('invalid workspace order review');
    return data;
  }, key, Boolean(allowed));
  function go(patch, options) {
    const next = new URLSearchParams(params);
    Object.entries(patch).forEach(([k, v]) => { if (v) next.set(k, String(v)); else next.delete(k); });
    setParams(next, options);
  }
  const result = read.data;
  const detail = orderId ? result?.items.find(item => item.order_id === orderId) : null;
  const canReceipts = scope?.kind === 'store_driver' && permitted(scope) && onReviewCollections &&
    context.operations.some(item => (typeof item === 'string' ? item : item.key) === 'courier.review');
  if (detail) return <Detail row={detail} name={name} onBack={() => go({ issue_order: null }, { replace: true })}/>;
  const end = snapshotDate(result?.as_of);
  const subtitle = end ? name + ' · كل الطلبات غير المؤكدة حتى ' + end : name;
  return <><Back onClick={onBack}>{scope ? 'كشف ' + name : 'تحتاج مراجعة'}</Back>
    <Heading title="طلبات تحتاج مراجعة" subtitle={subtitle}/>
    {!allowed ? <p role="alert">طلبات هذه الجهة غير متاحة ضمن صلاحياتك.</p> : <section className="panel sheet">
      <div className="section-head"><h2>{scope ? 'طلبات ' + name : 'طلبات الجهات'}</h2>
        {result && <span className="under">{result.available} طلبًا برقم أو معرّف</span>}</div>
      <form className="filters" onSubmit={event => { event.preventDefault(); clearTimeout(searchTimer.current); go({ issue_q: draft.trim(), issue_page: null, issue_order: null }, { replace: true }); }}>
        <label className="searchbox directory-search"><Icon name="search"/><input type="search" maxLength="160" aria-label="بحث في الطلبات"
          value={draft} onChange={e => {
            const value = e.target.value; setDraft(value); clearTimeout(searchTimer.current);
            searchTimer.current = setTimeout(() => go({issue_q:value.trim(), issue_page:null, issue_order:null}, {replace:true}), 400);
          }} placeholder="ابحث برقم الطلب أو سبب المراجعة"/></label>
      </form>
      {read.error && <div className="empty" role="alert">{read.error} <button className="textbtn" type="button" onClick={read.reload}>إعادة المحاولة</button></div>}
      {read.loading && <p className="empty" role="status">جارٍ تحميل طلبات المراجعة…</p>}
      {result && <>{orderId && !detail && <p className="empty" role="status">لم يظهر هذا الطلب ضمن صفحة النتائج الحالية.</p>}
        {result.unidentified.map(item => <p className="note warning" key={item.name}>{item.name} · {item.count} طلبًا يحتاج مراجعة ولم تربطه اللقطة بمعرّف طلب.</p>)}
        {result.items.length ? <div className="scroll"><table className="ledger issue-table"><thead><tr><th>رقم الطلب</th><th>{scope ? 'الجزء المتأثر' : 'الجهة'}</th><th>سبب المراجعة</th><th><span className="visually-hidden">التفاصيل</span></th></tr></thead>
          <tbody>{result.items.map(row => <tr key={row.order_id}><td><button className="textbtn order-id" onClick={() => go({ issue_order: row.order_id })}><bdi>{sourceLabel(row)}</bdi></button></td>
            <td>{[...new Set(row.groups.map(g => scope ? reasonTitle(g) : g.name))].join('، ')}</td>
            <td>{[...new Set(row.groups.flatMap(g => g.reasons.map(reason => reason.message)))].join('، ') || 'لم توضّح اللقطة السبب'}</td>
            <td><button className="textbtn" onClick={() => go({ issue_order: row.order_id })}><Icon name="chevron-left"/>عرض السبب</button></td></tr>)}</tbody></table></div> :
          <p className="empty" role="status">{query ? 'لا توجد طلبات تطابق البحث.' : 'لا توجد طلبات مرتبطة بمعرّف متاح للمراجعة.'}</p>}
        {result.total > 0 && <nav className="movement-pages screen-only" aria-label="صفحات جدول الطلبات"><span>عرض {(result.page-1)*pageSize+1}–{Math.min(result.page*pageSize,result.total)} من {result.total}</span><div>
          <button className="button" disabled={result.page <= 1} onClick={() => go({issue_page:result.page-1,issue_order:null})}>السابق</button>
          <span>صفحة {result.page} من {result.total_pages}</span><button className="button" disabled={result.page >= result.total_pages} onClick={() => go({issue_page:result.page+1,issue_order:null})}>التالي</button>
        </div></nav>}</>}
    </section>}
    {canReceipts && <p className="footnote"><button className="textbtn" type="button" onClick={() => onReviewCollections(scope,'pending')}><Icon name="clock-3"/>مراجعة إيصالات التحصيل لهذا الموصّل</button></p>}
  </>;
}
