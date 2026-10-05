import React, {useState} from 'react';
import {operationalApi as api, messageFor} from './api';

const feeLabels = {gross:'المبيعات',cancelled:'الإلغاءات',refunded:'الاستردادات',net:'الصافي',estimated_fees:'العمولة التقديرية',actual_fees:'العمولة الفعلية المسوّاة',expected_receivable:'المتوقع بعد التسوية',settled:'الوارد الفعلي من التسويات',outstanding:'المتبقي'};
const cents = value => {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(String(value ?? ''));
  return match ? (match[1] ? -1n : 1n) * (BigInt(match[2]) * 100n + BigInt((match[3] || '').padEnd(2, '0'))) : null;
};
const amount = value => value == null ? 'غير مكتمل' : `${value < 0n ? '-' : ''}${(value < 0n ? -value : value) / 100n}.${String((value < 0n ? -value : value) % 100n).padStart(2, '0')}`;
const sum = values => values.reduce((total,value) => total == null || cents(value) == null ? null : total + cents(value), 0n);
const unique = rows => [...new Map(rows.map(row => [row.id,row])).values()];

function ReceiptDocument({id,label}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const download=async()=>{
    if(busy)return;
    setBusy(true);setError('');
    try {const content=await api.receiptContent(id);const url=URL.createObjectURL(content);const link=document.createElement('a');link.href=url;link.download='receipt';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    catch(e){setError(messageFor(e));}finally{setBusy(false);}
  };
  return <span><button type="button" disabled={busy} onClick={download}>{label}</button>{error&&<span role="alert">{error}</span>}</span>;
}

export default function DomainDetails({report}) {
  const obligations = unique(report.obligations || []);
  const parties = report.parties || [];
  const facts = report.details?.facts || {};
  const named = id => {
    const matches = obligations.filter(o => o.id === id || o.ad_day_id === id);
    const obligation = matches[0];
    const sarAmount = matches.length > 1 ? (matches.every(o => o.sar_amount != null) ? amount(sum(matches.map(o => o.sar_amount))) : null) : obligation?.sar_amount;
    const party = parties.find(p => p.party_id === obligation?.party_id && p.party_type === obligation?.party_type && p.currency === obligation?.currency);
    return {name:party?.name || 'جهة غير مكتملة الإعداد',date:obligation?.business_date || '',currency:obligation?.currency || '',obligation,sarAmount};
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
    <details className="op-card"><summary>عهد الموظفين</summary>{(report.details?.employee_custody || []).map(p => <article key={`${p.party_id}:${p.currency}`}><h3>{p.name} · {p.currency}</h3><p>الممول: {p.custody_funded ?? 'غير مكتمل'} · المصروف: {p.custody_spent ?? 'غير مكتمل'} · المرتجع: {p.custody_returned ?? 'غير مكتمل'}</p><p>الرصيد المتبقي في العهدة: {p.custody_remaining ?? 'غير مكتمل'}</p>{(p.receipts || []).filter(r=>r.receipt_id).map((r,index)=><p key={r.id}><ReceiptDocument id={r.receipt_id} label={`إيصال ${index+1} · ${r.amount} ${p.currency}`}/>{r.occurred_at ? ` · ${new Date(r.occurred_at).toLocaleString('ar-SA')}` : ''}</p>)}</article>)}</details>
    <details className="op-card"><summary>المصروفات التشغيلية وسحوبات المالك/المدير</summary><p className="op-muted">حركات فعلية منذ بداية التشغيل؛ السحوبات منفصلة عن المصروفات التشغيلية.</p>{Object.entries(report.summaries || {}).map(([currency,values])=><article key={currency}><h3>{currency}</h3><p>إجمالي المصروفات التشغيلية: {values.operating_expenses_paid ?? 'غير مكتمل'}</p><p>إجمالي سحوبات المالك/المدير: {values.owner_withdrawals ?? 'غير مكتمل'}</p></article>)}{parties.filter(p=>['operating_expense','owner_withdrawal'].includes(p.party_type)).map(p=><p key={`${p.party_type}:${p.party_id}:${p.currency}`}>{p.name} · {p.party_type==='operating_expense'?'مصروف تشغيلي':'سحب مالك/مدير'} · {p.party_type==='operating_expense'?p.expense_paid:p.owner_withdrawals} {p.currency}</p>)}</details>
    <details className="op-card"><summary>المحافظ الإعلانية والالتزامات</summary>{parties.filter(p=>p.party_type==='ad_account').map(p=><article key={`${p.party_id}:${p.currency}`}><h3>{p.name} · {p.currency}</h3><p>رصيد المحفظة: {p.ad_wallet_balance ?? 'غير مكتمل'} · صرف المحفظة: {p.ad_wallet_spent ?? 'غير مكتمل'}</p><p>المستحق الآجل: {p.ad_payable ?? p.outstanding_payable ?? 'غير مكتمل'}</p></article>)}</details>
    <details className="op-card"><summary>استحقاقات الموظفين اليومية</summary>{Object.entries(report.details?.salary_days || {}).map(([id,row]) => {const p=named(id);return <p key={id}>{p.name} · {p.date} · {row.amount} {p.currency}</p>;})}</details>
    <details className="op-card"><summary>الصرف الإعلاني الشهري</summary>{[...adMonths.entries()].map(([id,row]) => <article key={id}><h3>{row.name} · {row.month} · {row.currency}</h3><p>الصرف النهائي للأيام المغلقة: {amount(sum(row.closed))} {row.currency}</p><p>آخر المشاهدات للأيام المفتوحة: {amount(sum(row.open))} {row.currency}</p></article>)}</details>
    <details className="op-card"><summary>الصرف الإعلاني اليومي</summary>{[...adDays.values()].map(p => <p key={p.id}>{p.name} · {p.date} · {p.row.amount} {p.currency} · {p.row.closed ? 'صرف نهائي' : 'مشاهدة تراكمية'}{p.sarAmount != null && p.currency !== 'SAR' ? ` · المعادل ${p.sarAmount} SAR` : ''}</p>)}</details>
  </div>;
}
