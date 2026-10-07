import React, { useCallback, useEffect, useState } from 'react';
import { operationalApi as api, entityKinds, messageFor, feeIncomplete } from './api';
import { OpeningBalances, Notice } from './OpeningBalances';
import DailyMovements from './DailyMovements';
import SupplierReturn from './SupplierReturn';
import MovementHistory from './MovementHistory';
import DomainDetails from './DomainDetails';
import './operational.css';
const labels = {
  actual_liquidity: 'السيولة الفعلية',
  receivable: 'المتبقي لنا',
  payable: 'المتبقي علينا',
  expected_receivable: 'التقديري لنا',
  expected_payable: 'التقديري علينا',
  confirmed: 'إجمالي المؤكد',
  settled: 'المسدد',
  outstanding: 'المتبقي'
};
function issueMessage(code = '') {
  if (/salary|employee/.test(code)) return 'عقد راتب موظف غير مكتمل؛ يلزم التحقق من الراتب وتاريخ السريان في ميزان 2.';
  if (/fee|tax|refund_fee|cancellation_fee/.test(code)) return 'إعداد العمولة غير مكتمل';
  if (/fx/.test(code)) return 'سعر الصرف المعتمد غير متاح؛ يظهر الصرف بعملته الأصلية فقط.';
  if (/ad_start/.test(code)) return 'صرف يوم البداية يحتاج إثباتًا يفصل الصرف السابق عن الصرف بعد بداية النظام.';
  if (/advertising_closed|ad_correction/.test(code)) return 'تغير صرف يوم إعلاني مغلق؛ يلزم تصحيح موثق قبل اعتماد الفرق.';
  if (/ad_|advertising/.test(code)) return 'بيانات الصرف أو إثبات إغلاق اليوم الإعلاني غير مكتملة.';
  if (/supplier|invoice/.test(code)) return 'دليل فاتورة المورد غير مكتمل أو متعارض؛ يلزم مراجعة الفاتورة وبنودها.';
  return 'إعداد غير مكتمل؛ يلزم استكمال بيانات المصدر.';
}
function Reports() {
  const [report, setReport] = useState(null),
    [audit, setAudit] = useState([]),
    [error, setError] = useState(''),
    [kind, setKind] = useState('');
  useEffect(() => {
    let alive = true;
    let running = false;
    const refresh = async () => {
      if (running) return;
      running = true;
      try {
        const [r, a] = await Promise.all([api.reports(), api.audit()]);
        if (alive) { setReport(r); setAudit(a.items); setError(''); }
      } catch (e) { if (alive) setError(messageFor(e)); }
      finally { running = false; }
    };
    refresh();
    const timer = setInterval(refresh, 30000);
    return () => { alive = false; clearInterval(timer); };

  }, []);
  if (error) return <Notice error={error} />;
  if (!report) return <p role="status">جاري تحميل الأرصدة…</p>;
  return <section><h2>الأرصدة والتقارير التشغيلية</h2><p className="op-muted">آخر تحديث: {report.as_of ? new Date(report.as_of).toLocaleString('ar-SA') : 'غير متاح'}</p>{(report.issues || []).map((issue, i) => <p role="alert" className="op-error" key={i}>{feeIncomplete(issue) ? 'إعداد العمولة غير مكتمل' : (typeof issue === 'string' ? issueMessage(issue) : issue.message || issueMessage(issue.code))}</p>)}<div className="op-summary">{Object.entries(labels).map(([key, label]) => <article key={key}><span>{label}</span><strong>{Object.entries(report.summaries || {SAR: report.summary || {}}).map(([currency, summary]) => `${summary[key] ?? 'غير مكتمل'} ${currency}`).join(' / ')}</strong></article>)}</div><label className="op-field">نوع الجهة<select value={kind} onChange={e => setKind(e.target.value)}><option value="">جميع الجهات</option>{entityKinds.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label><div className="op-table"><table><thead><tr>{['الجهة', 'العملة', 'الابتدائي', 'التقديري لنا', 'التقديري علينا', 'المؤكد لنا', 'المؤكد علينا', 'المسدد', 'الفعلي', 'المتبقي لنا', 'المتبقي علينا'].map(l => <th key={l}>{l}</th>)}</tr></thead><tbody>{report.parties.filter(p => !['operating_expense','owner_withdrawal'].includes(p.party_type) && (!kind || p.party_type === kind)).map(p => <tr key={`${p.party_type}:${p.party_id}:${p.currency}`}>{['name', 'currency', 'opening', 'expected_receivable', 'expected_payable', 'confirmed_receivable', 'confirmed_payable', 'settled', 'actual', 'outstanding_receivable', 'outstanding_payable'].map(key => <td key={key}>{p[key] ?? 'غير مكتمل'}</td>)}</tr>)}</tbody></table>{!report.parties.length && <p>لا توجد أرصدة مسجلة بعد.</p>}</div><DomainDetails report={report}/><details className="op-card"><summary>سجل التدقيق</summary>{audit.length ? audit.map((a, i) => <p key={i}>{a.message || a.action_label || 'تم تسجيل عملية موثقة'} · {(a.at || a.created_at) ? new Date(a.at || a.created_at).toLocaleString('ar-SA') : ''} {a.reason || ''}</p>) : <p>لا توجد عمليات مسجلة.</p>}</details></section>;
}
export default function OperationalBalances({
  source = 'mezan2'
}) {
  const [context, setContext] = useState(null),
    [error, setError] = useState(''),
    [tab, setTab] = useState(source === 'employee_app' ? 'movements' : 'openings'),
    [version, setVersion] = useState(0);
  const load = useCallback(() => {
    setError('');
    api.context().then(c => {
      setContext(c);
      setTab(t => t === 'openings' && (c.status !== 'draft' || !c.permissions?.manage) ? (c.permissions?.reports ? 'reports' : 'movements') : t);
    }).catch(e => setError(messageFor(e)));
  }, []);
  useEffect(() => { load(); const timer = setInterval(load, 30000); return () => clearInterval(timer); }, [load]);
  if (error) return <main className="op-page" dir="rtl"><Notice error={error} /><button onClick={load}>إعادة المحاولة</button></main>;
  if (!context) return <main className="op-page" dir="rtl" role="status">جاري تحميل النظام التشغيلي…</main>;
  const permissions = context.permissions || {};
  const active = context.status === 'active';
  return <main className="op-page" dir="rtl"><header><span className="op-eyebrow">ميزان · التشغيل اليومي</span><h1>الأرصدة التشغيلية</h1><p>{context.status === 'draft' ? 'ابدأ بإدخال الأرصدة الافتتاحية' : active ? 'بدأ النظام التشغيلي؛ الطلبات الجديدة تُحتسب تلقائيًا.' : 'النظام للقراءة والتدقيق فقط'}</p></header><nav aria-label="صفحات الأرصدة التشغيلية">{context.status === 'draft' && permissions.manage && <button aria-current={tab === 'openings' ? 'page' : undefined} onClick={() => setTab('openings')}>الأرصدة الافتتاحية</button>}{permissions.move && <button aria-current={tab === 'movements' ? 'page' : undefined} onClick={() => setTab('movements')}>الحركات المالية اليومية</button>}{permissions.reports && <button aria-current={tab === 'reports' ? 'page' : undefined} onClick={() => setTab('reports')}>الأرصدة والتقارير التشغيلية</button>}</nav>{tab === 'openings' && context.status === 'draft' && permissions.manage && <OpeningBalances context={context} onFinished={load} />} {tab === 'movements' && permissions.move && (active ? <><DailyMovements cards canCreateCash={context.can_create_cash === true} key={context.session_scope} storageScope={context.session_scope} onScopeChanged={load} canManage={permissions.manage} source={source} onSaved={() => setVersion(v => v + 1)} /><MovementHistory key={`${context.session_scope}:${version}`} />{permissions.manage && <SupplierReturn />}</> : <p>تسجيل الحركات متاح بعد إنهاء الأرصدة الافتتاحية وأثناء تشغيل النظام.</p>)}{tab === 'reports' && permissions.reports && <Reports key={version} />}</main>;
}
