import React from 'react';

const feeLabels = {gross:'المبيعات',cancelled:'الإلغاءات',refunded:'الاستردادات',net:'الصافي',estimated_fees:'العمولة التقديرية',actual_fees:'العمولة الفعلية المسوّاة',expected_receivable:'المتوقع بعد التسوية',settled:'الوارد الفعلي من التسويات',outstanding:'المتبقي'};
const cents = value => {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(String(value ?? ''));
  return match ? (match[1] ? -1n : 1n) * (BigInt(match[2]) * 100n + BigInt((match[3] || '').padEnd(2, '0'))) : null;
};
const amount = value => value == null ? 'غير مكتمل' : `${value < 0n ? '-' : ''}${(value < 0n ? -value : value) / 100n}.${String((value < 0n ? -value : value) % 100n).padStart(2, '0')}`;
const sum = values => values.reduce((total,value) => total == null || cents(value) == null ? null : total + cents(value), 0n);
const unique = rows => [...new Map(rows.map(row => [row.id,row])).values()];

export default function DomainDetails({report}) {
  const obligations = unique(report.obligations || []);
  const parties = report.parties || [];
  const facts = report.details?.facts || {};
  const named = id => {
    const obligation = obligations.find(o => o.id === id);
    const party = parties.find(p => p.party_id === obligation?.party_id && p.party_type === obligation?.party_type && p.currency === obligation?.currency);
    return {name:party?.name || 'جهة غير مكتملة الإعداد',date:obligation?.business_date || '',currency:obligation?.currency || '',obligation};
  };
  // A provider may expose multiple observations; choose its latest account/day
  // observation before aggregating monthly amounts. Never sum intraday snapshots.
  const adDays = new Map();
  Object.entries(report.details?.ad_days || {}).forEach(([id,row]) => {
    const party = named(id);
    if (!party.obligation || !party.date || !party.currency) return;
    const key = JSON.stringify([party.obligation.party_id,party.currency,party.date]);
    const prior = adDays.get(key);
    if (!prior || Date.parse(row.observed_at) >= Date.parse(prior.row.observed_at)) adDays.set(key,{id,row,...party});
  });
  const adMonths = new Map();
  [...adDays.values()].forEach(day => {
    const month = day.date.slice(0,7);
    const key = JSON.stringify([day.obligation.party_id,day.currency,month]);
    if (!adMonths.has(key)) adMonths.set(key,{name:day.name,currency:day.currency,month,closed:[],open:[]});
    adMonths.get(key)[day.row.closed ? 'closed' : 'open'].push(day.row.amount);
  });
  const carriers = parties.filter(p => ['courier','store_driver'].includes(p.party_type)).map(p => {
    const rows = obligations.filter(o => o.kind === 'shipping' && o.party_id === p.party_id && o.party_type === p.party_type && o.currency === p.currency);
    const delivered = rows.filter(o => Boolean(facts[o.id]?.delivered_at) || (cents(o.confirmed) || 0n) > 0n);
    return {...p,orderCount:rows.length,deliveredCount:delivered.length,shippingConfirmed:sum(rows.map(o => o.confirmed))};
  });
  const suppliers = parties.filter(p => p.party_type === 'supplier').map(p => {
    const rows = obligations.filter(o => o.kind === 'supplier' && !o.derived_credit && o.party_id === p.party_id && o.currency === p.currency);
    const evidence = new Set(rows.flatMap(o => o.evidence_ids || []).filter(id => id.startsWith('receipt:')));
    const covered = [...evidence].map(id => facts[id]?.amount);
    const acceptedReturns = Object.entries(facts).filter(([key,value]) => key.startsWith('return:') && evidence.has(value.receipt_id)).map(([,value]) => value.amount);
    return {...p,covered:sum(covered),acceptedReturns:sum(acceptedReturns)};
  });
  return <div className="op-domain-details">
    <details className="op-card"><summary>تفاصيل منصات الدفع</summary>{Object.entries(report.details?.provider_reports || {}).map(([id,row]) => {
      const p = named(id);
      return <article key={id}><h3>{p.name} · {p.currency}</h3><dl>{Object.entries(feeLabels).map(([key,label]) => <div key={key}>{label}: {row[key] ?? p.obligation?.[key] ?? 'غير مكتمل'}</div>)}</dl></article>;
    })}</details>
    <details className="op-card"><summary>الشحن ومناديب المتجر</summary>{carriers.map(p => <article key={`${p.party_type}:${p.party_id}:${p.currency}`}><h3>{p.name} · {p.currency}</h3><p>عدد الطلبات: {p.orderCount} · التوصيلات المؤكدة: {p.deliveredCount}</p><p>أجرة التوصيل المؤكدة: {amount(p.shippingConfirmed)} · المسدد: {p.settled ?? 'غير مكتمل'} · المتبقي: {p.outstanding_payable ?? 'غير مكتمل'}</p><p>التحصيل المستحق لنا: {p.outstanding_receivable ?? 'غير مكتمل'}</p></article>)}</details>
    <details className="op-card"><summary>تغطية الموردين والمرتجعات والمدفوعات</summary>{suppliers.map(p => <article key={`${p.party_id}:${p.currency}`}><h3>{p.name} · {p.currency}</h3><p>قيمة الفواتير المغطاة: {amount(p.covered)} · المرتجعات المقبولة: {amount(p.acceptedReturns)}</p><p>المدفوع المخصص: {p.settled ?? 'غير مكتمل'} · المتبقي علينا: {p.outstanding_payable ?? 'غير مكتمل'} · المستحق لنا: {p.outstanding_receivable ?? 'غير مكتمل'}</p></article>)}</details>
    <details className="op-card"><summary>استحقاقات الموظفين اليومية</summary>{Object.entries(report.details?.salary_days || {}).map(([id,row]) => {const p=named(id);return <p key={id}>{p.name} · {p.date} · {row.amount} {p.currency}</p>;})}</details>
    <details className="op-card"><summary>الصرف الإعلاني الشهري</summary>{[...adMonths.entries()].map(([id,row]) => <article key={id}><h3>{row.name} · {row.month} · {row.currency}</h3><p>الصرف النهائي للأيام المغلقة: {amount(sum(row.closed))} {row.currency}</p><p>آخر المشاهدات للأيام المفتوحة: {amount(sum(row.open))} {row.currency}</p></article>)}</details>
    <details className="op-card"><summary>الصرف الإعلاني اليومي</summary>{[...adDays.values()].map(p => <p key={p.id}>{p.name} · {p.date} · {p.row.amount} {p.currency} · {p.row.closed ? 'صرف نهائي' : 'مشاهدة تراكمية'}{p.obligation?.sar_amount != null && p.currency !== 'SAR' ? ` · المعادل ${p.obligation.sar_amount} SAR` : ''}</p>)}</details>
  </div>;
}
