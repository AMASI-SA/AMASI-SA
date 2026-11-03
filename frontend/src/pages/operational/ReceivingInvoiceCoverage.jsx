import React, {useEffect, useRef, useState} from 'react';
import {operationalApi as api, messageFor} from './api';

const reasons = {
  missing: 'لا يوجد قيد مرتبط بدليل هذه القطعة',
  wrong_currency: 'القيد محفوظ بعملة تختلف عن عملة الفاتورة',
  amount_mismatch: 'قيمة القيد تختلف عن قيمة المكوّن',
  missing_provenance: 'الدليل موجود ولم يُحفظ رابط الفاتورة في القيد',
  fact_only: 'دليل الاستلام موجود دون قيد مطابق',
  ambiguous_evidence: 'أكثر من قيد يشير إلى الدليل نفسه',
  conflicting_evidence: 'القيد ودليل الاستلام غير متطابقين',
};
const pageSize = 20;

export default function ReceivingInvoiceCoverage({sessionScope}) {
  const [data, setData] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [all, setAll] = useState(false), [page, setPage] = useState(0);
  const active = useRef(false), pending = useRef(false);
  useEffect(() => {active.current = true; return () => {active.current = false;};}, []);
  async function load() {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(''); setData(null); setPage(0); setAll(false);
    try {
      const r = await api.receivingInvoiceCoverage();
      if (r.schema !== 'receiving_component_coverage_v1' || r.session_scope !== sessionScope ||
          r.read_only !== true || r.posting_allowed !== false || !Array.isArray(r.rows))
        throw new Error();
      if (active.current) setData(r);
    } catch(e) { if (active.current) setError(e.response ? messageFor(e) : 'تعذر التحقق من مطابقة فواتير الاستلام.'); }
    finally {if (active.current) setBusy(false); pending.current = false;}
  }
  const rows = (data?.rows || []).filter(r=>all || r.source_issue || r.extra_saved_count || r.differences.length);
  return <div aria-label="فحص فواتير استلام المورد">
    <button type="button" className="ofp-button" disabled={busy} onClick={load}>فحص فواتير استلام المورد</button>
    {busy && <p role="status">جارٍ مقارنة فواتير الاستلام بقيود القطع…</p>}
    {error && <p role="alert">{error}</p>}
    {data && <div>
      <p>فواتير الاستلام المعتمدة حتى لقطة <bdi>{data.saved_balance_as_of}</bdi>: {data.invoice_count}، تحتاج مراجعة: {data.review_count}.</p>
      <p>هذه مقارنة للقيمة الإجمالية قبل المرتجعات والسداد، وليست مطالبة بدفع الفرق. لا تغيّر الأرصدة.</p>
      {data.after_snapshot_count > 0 && <p>{data.after_snapshot_count} فاتورة أحدث من اللقطة؛ لم تدخل المقارنة.</p>}
      <button type="button" onClick={()=>{setAll(!all);setPage(0);}}>{all?'عرض الفروق فقط':'عرض جميع الفواتير'}</button>
      {rows.slice(page*pageSize,(page+1)*pageSize).map(r=><details key={r.invoice_id}>
        <summary><bdi>{r.invoice_number || r.invoice_id}</bdi> · {r.invoice_gross_sar == null ? 'قيمة غير مثبتة' : r.invoice_gross_sar+' SAR'} ·
          {r.component_count == null ? 'بيانات غير مكتملة' : ' مكوّنات مطابقة: '+r.matched_components+' / '+r.component_count}</summary>
        {r.source_issue && <p>بيانات الفاتورة أو القيود المرتبطة بها تحتاج مراجعة.</p>}
        {r.extra_saved_count > 0 && <p>قيود مرتبطة برقم الفاتورة بلا مكوّن مطابق في الأصل: {r.extra_saved_count}</p>}
        <ul>{r.differences.map((d,i)=><li key={i}>القطعة <bdi>{d.piece_id}</bdi> · {d.component==='product'?'منتج':'خدمة '+d.component.replace(/^service:/,'')} ·
          {reasons[d.status] || 'تعارض في الدليل'} · الأصل <bdi>{d.source_amount_sar} SAR</bdi>
          {d.saved_gross != null && <> · المسجل <bdi>{d.saved_gross} {d.saved_currency}</bdi></>}</li>)}</ul>
      </details>)}
      {page>0 && <button type="button" onClick={()=>setPage(page-1)}>السابق</button>}
      {(page+1)*pageSize<rows.length && <button type="button" onClick={()=>setPage(page+1)}>التالي</button>}
    </div>}
  </div>;
}
