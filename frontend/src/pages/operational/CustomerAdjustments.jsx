import React from 'react';
import { Amount, Icon } from './ApprovedFinanceUI';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { useBalanceRead } from './simpleBalancesHooks';
import { minorUnits } from './simpleBalancesModel';

export const ADJUSTMENT_PAGE_SIZE = 8;
export const normalizeOrderNumber = value => String(value || '').trim()
  .replace(/[٠-٩]/g, c => String('٠١٢٣٤٥٦٧٨٩'.indexOf(c)))
  .replace(/[۰-۹]/g, c => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(c)));

export function validAdjustmentPage(result) {
  return result?.page_size === ADJUSTMENT_PAGE_SIZE && Number.isInteger(result.page) && result.page > 0
    && Number.isInteger(result.total) && result.total >= 0 && Number.isInteger(result.total_pages)
    && result.total_pages > 0 && result.page <= result.total_pages && Array.isArray(result.items)
    && result.items.length <= ADJUSTMENT_PAGE_SIZE;
}

export function AdjustmentReadState({ read }) {
  if (read.error) return <p className="note warning" role="alert">{read.error} <button className="textbtn" type="button" onClick={read.reload}>إعادة المحاولة</button></p>;
  return read.loading ? <p role="status">جارٍ تحميل البيانات…</p> : null;
}

export function AdjustmentPages({ result, onPage, label = 'صفحات السجل' }) {
  if (!result || result.total_pages <= 1) return null;
  return <nav className="movement-pages" aria-label={label}><span>صفحة {result.page} من {result.total_pages}</span><div>
    <button type="button" className="button" disabled={result.page <= 1} onClick={() => onPage(result.page - 1)}>السابق</button>
    <button type="button" className="button" disabled={result.page >= result.total_pages} onClick={() => onPage(result.page + 1)}>التالي</button>
  </div></nav>;
}

export function AdjustmentSummary({ summary, currency = 'SAR' }) {
  if (!summary) return null;
  return <section className="adjustment-summary" aria-label="مبالغ العملاء بانتظار التأكيد">
    <div className="adjustment-total refund"><span><Icon name="corner-down-left"/>استرجاع ينتظر التأكيد</span>
      <strong><Amount value={summary.refund_pending_amount} currency={currency}/></strong><small>{summary.refund_pending_count} طلب</small></div>
    <div className="adjustment-total collection"><span><Icon name="arrow-down-left"/>تحصيل ينتظر التأكيد</span>
      <strong><Amount value={summary.collection_pending_amount} currency={currency}/></strong><small>{summary.collection_pending_count} طلب</small></div>
    {summary.unknown_count > 0 && <p className="note warning">{summary.unknown_count} طلب يحتاج استكمال دليل الدفع لتحديد المبلغ.</p>}
  </section>;
}

export function CustomerAdjustmentBadges({ summary }) {
  if (!summary) return null;
  return <div className="adjustment-badges">
    {summary.refund_pending_count > 0 && <span className="adjustment-badge refund"><Icon name="corner-down-left"/>
      {summary.refund_pending_count} طلب · رد ينتظر التأكيد <Amount value={summary.refund_pending_amount} currency={summary.currency}/></span>}
    {summary.collection_pending_count > 0 && <span className="adjustment-badge collection"><Icon name="arrow-down-left"/>
      {summary.collection_pending_count} طلب · تحصيل ينتظر التأكيد <Amount value={summary.collection_pending_amount} currency={summary.currency}/></span>}
  </div>;
}

export default function CustomerAdjustments({ context, identity, direction, page = 1, onPage, onOrder }) {
  const read = useBalanceRead(async config => {
    const result = await api.customerAdjustments({ direction, page, page_size: ADJUSTMENT_PAGE_SIZE }, config);
    const summary = result?.summary;
    if (!validAdjustmentPage(result) || !summary
      || ['refund_pending_amount', 'collection_pending_amount'].some(key => minorUnits(summary[key]) === null)
      || ['refund_pending_count', 'collection_pending_count', 'unknown_count'].some(key => !Number.isInteger(summary[key]) || summary[key] < 0)
      || result.items.some(row => !row.order_number || row.status !== 'needs_confirmation'
        || minorUnits(row[`pending_${direction}_amount`]) === null)) throw new Error('invalid customer adjustments');
    return result;
  }, JSON.stringify([identity, context.session_scope, context.permission_version, 'customer-adjustments', direction, page]));
  return <><AdjustmentReadState read={read}/>{read.data && <>
    <AdjustmentSummary summary={read.data.summary}/>
    <section className="panel rf-block"><div className="section-head"><h2>{direction === 'refund' ? 'فروق رد المبالغ بانتظار التأكيد' : 'مبالغ متبقية على العملاء'}</h2>
      <button className="textbtn" type="button" onClick={read.reload}>تحديث</button></div>
      <p className="under">يبقى التحصيل المؤكد في حساب مصدره. يتغير الرصيد عند توثيق الرد أو التحصيل الفعلي.</p>
      {read.data.items.length ? read.data.items.map(row => <article className="rf-row adjustment-row" key={row.order_id || row.order_number}>
        <div><button className="textbtn" type="button" onClick={() => onOrder(row.order_number)}><bdi>طلب #{row.order_number}</bdi></button>
          <small>المدفوع المؤكد <Amount value={row.paid_amount} currency={row.currency}/> · إجمالي الطلب الحالي <Amount value={row.current_total} currency={row.currency}/></small>
          {row.reason && <small>{row.reason}</small>}</div>
        <div><span className={`adjustment-badge ${direction}`}>{direction === 'refund' ? 'رد ينتظر التأكيد' : 'تحصيل ينتظر التأكيد'}</span>
          <strong><Amount value={row[`pending_${direction}_amount`]} currency={row.currency}/></strong>
          <button className="textbtn" type="button" onClick={() => onOrder(row.order_number)}>مراجعة الطلب <Icon name="chevron-left"/></button></div>
      </article>) : <p className="empty">لا توجد مبالغ في هذه القائمة ضمن صلاحياتك.</p>}
      <AdjustmentPages result={read.data} onPage={onPage} label="صفحات المبالغ بانتظار التأكيد"/>
    </section>
  </>}</>;
}
