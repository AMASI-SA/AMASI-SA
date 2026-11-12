import React, {useEffect, useRef, useState} from 'react';
import {operationalApi as api, messageFor} from './api';
import PaginatedReportTable from './PaginatedReportTable';
import {visibleOperationalRead} from './visibleOperationalRead';

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

const correctionReasons = {
  native_settlement_not_proven_clear: 'لم يثبت سجل المورد أن الفاتورة خالية من السداد.',
  other_document_may_cover_receipt: 'يوجد مستند آخر قد يشمل القطعة نفسها.',
  invoice_evidence_incomplete: 'بيانات الفاتورة والقيود المرتبطة بها غير مكتملة.',
  supplier_opening_requires_allocation_review: 'يلزم التحقق مما يغطيه الرصيد الافتتاحي للمورد.',
  prior_movement_or_unallocated_payment: 'توجد حركة سابقة تتطلب مطابقة السداد وتخصيصه.',
  supplier_return_or_adjustment: 'يوجد مرتجع أو تعديل يحتاج إلى مطابقة.',
  native_purchase_link_requires_review: 'يوجد ربط بمستند شراء يحتاج إلى مراجعة.',
  saved_amount_or_currency_requires_review: 'يلزم التحقق من مبلغ القيد المحفوظ وعملته.',
  missing_finalized_receipt_or_unique_order_line: 'تعذر إثبات الاستلام وربط القطعة ببند طلب واحد.',
  unsupported_receipt_discrepancy: 'نوع الفرق يحتاج إلى مراجعة مستنداته.',
  no_proposed_change: 'لم يظهر فرق يستدعي تصحيحًا.',
};

function validProposal(p, invoiceId, asOf, revision, sessionScope) {
  if (!asOf || !sessionScope || p?.schema !== 'receiving_correction_preview_v1' || p.invoice_id !== invoiceId ||
      p.saved_balance_as_of !== asOf || !Number.isInteger(revision) || p.state_revision !== revision || p.session_scope !== sessionScope ||
      p.read_only !== true || p.posting_allowed !== false ||
      !Array.isArray(p.reasons) || !Array.isArray(p.legs)) return false;
  if (p.status === 'blocked') return p.legs.length === 0 && p.reasons.length > 0 && p.reasons.every(r => typeof r === 'string');
  return p.status === 'review_only' && p.reasons.length === 0 && p.legs.length > 0 && p.legs.every(leg =>
    typeof leg?.piece_id === 'string' && typeof leg.component === 'string' &&
    /^[A-Z]{3}$/.test(leg.currency) && /^\d+\.\d{2}$/.test(leg.before) && /^\d+\.\d{2}$/.test(leg.after));
}

function CorrectionPreview({invoiceId, asOf, revision, sessionScope}) {
  const [proposal, setProposal] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const cleanup=useRef(null),lane=useRef({pending:null});
  useEffect(()=>{setProposal(null);return()=>cleanup.current?.();},[invoiceId,asOf,revision,sessionScope]);
  function load() {
    if(busy)return;
    cleanup.current?.();
    cleanup.current=visibleOperationalRead(config=>api.receivingCorrectionPreview(invoiceId,asOf,revision,config),{
      onStart:()=>{setBusy(true);setProposal(null);setError('');},
      onData:response=>{if(!validProposal(response,invoiceId,asOf,revision,sessionScope))throw new Error();setProposal(response);},
      onError:()=>setError('تعذر التحقق من مقترح هذه اللقطة. أعد فحص فواتير الاستلام.'),
      onEnd:()=>setBusy(false),
    },lane.current);
  }
  return <>
    <button type="button" disabled={busy} onClick={load}>معاينة التصحيح التاريخي</button>
    {busy && <p role="status">جارٍ التحقق من أدلة الفاتورة…</p>}
    {error && <p role="alert">{error}</p>}
    {proposal && <section aria-label="مقترح التصحيح التاريخي">
      {proposal.status === 'blocked' ? <>
        <p>تعذرت معاينة تصحيح هذه الفاتورة.</p>
        <ul>{proposal.reasons.map(reason => <li key={reason}>{correctionReasons[reason] || 'يحتاج دليل الفاتورة إلى مراجعة.'}</li>)}</ul>
      </> : <PaginatedReportTable rows={proposal.legs} label="القيمة قبل التصحيح المقترح وبعده" resetKey={invoiceId}>{rows => <div className="ofp-table-scroll">
        <table><caption>القيمة الإجمالية لكل مكوّن قبل التصحيح المقترح وبعده، للمعاينة فقط</caption>
          <thead><tr><th>القطعة</th><th>المكوّن</th><th>العملة</th><th>المسجّل</th><th>المقترح</th></tr></thead>
          <tbody>{rows.map(leg => <tr key={JSON.stringify([leg.piece_id,leg.component,leg.currency])}>
            <td><bdi>{leg.piece_id}</bdi></td><td>{leg.component === 'product' ? 'منتج' : 'خدمة '+leg.component.replace(/^service:/,'')}</td>
            <td><bdi>{leg.currency}</bdi></td><td><bdi>{leg.before}</bdi></td><td><bdi>{leg.after}</bdi></td>
          </tr>)}</tbody>
        </table>
      </div>}</PaginatedReportTable>}
    </section>}
  </>;
}

export default function ReceivingInvoiceCoverage({sessionScope}) {
  const [data, setData] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [all, setAll] = useState(false), [page, setPage] = useState(0);
  const pending=useRef(false),cleanup=useRef(null),lane=useRef({pending:null});
  useEffect(()=>{setData(null);return()=>cleanup.current?.();},[sessionScope]);
  function load() {
    if (pending.current) return;
    cleanup.current?.();
    cleanup.current=visibleOperationalRead(config=>api.receivingInvoiceCoverage(config),{
      onStart:()=>{pending.current=true;setBusy(true);setError('');setData(null);setPage(0);setAll(false);},
      onData:r=>{
      if (r.schema !== 'receiving_component_coverage_v1' || r.session_scope !== sessionScope ||
          r.read_only !== true || r.posting_allowed !== false || !Array.isArray(r.rows))
        throw new Error();
      setData(r);},
      onError:e=>setError(e.response?messageFor(e):'تعذر التحقق من مطابقة فواتير الاستلام.'),
      onEnd:()=>{setBusy(false);pending.current=false;},
    },lane.current);
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
        {(r.source_issue || r.extra_saved_count > 0 || r.differences.length > 0) &&
          <CorrectionPreview key={data.saved_balance_as_of} invoiceId={r.invoice_id} asOf={data.saved_balance_as_of} revision={data.state_revision} sessionScope={sessionScope}/>}
      </details>)}
      {page>0 && <button type="button" onClick={()=>setPage(page-1)}>السابق</button>}
      {(page+1)*pageSize<rows.length && <button type="button" onClick={()=>setPage(page+1)}>التالي</button>}
    </div>}
  </div>;
}
