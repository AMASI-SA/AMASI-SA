import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useOptionalAuth } from '../../context/AuthContext';
import { operationalApi as api, entityKinds, messageFor, feeIncomplete } from './api';
import { OpeningBalances, Notice } from './OpeningBalances';
import DailyMovements from './DailyMovements';
import SupplierReturn from './SupplierReturn';
import InventoryPurchases from './InventoryPurchases';
import InventoryAvailability from './InventoryAvailability';
import OpeningInventory from './OpeningInventory';
import SalaryStart from './SalaryStart';
import DomainDetails from './DomainDetails';
import CustomerPaymentTotals from './CustomerPaymentTotals';
import CustomerWalletBalance from './CustomerWalletBalance';
import { formatRiyadhDateTime } from '../../lib/dates';
import OperationalCostExposure from './OperationalCostExposure';
import PaginatedReportTable from './PaginatedReportTable';
import './operational.css';
import { visibleOperationalRead, refreshVisibleSnapshot } from './visibleOperationalRead';
import PendingOrdersDialog from './PendingOrdersDialog';
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
  if (code === 'canonical_salla_source_missing') return 'طلبات سلة تفتقد بيانات المصدر الأساسية؛ يلزم تدقيق المصدر قبل اعتبارها حركات جديدة.';
  if (/salary|employee/.test(code)) return 'عقد راتب موظف غير مكتمل؛ يلزم التحقق من الراتب وتاريخ السريان في ميزان 2.';
  if (/fee|tax|refund_fee|cancellation_fee/.test(code)) return 'إعداد العمولة غير مكتمل';
  if (/fx/.test(code)) return 'سعر الصرف المعتمد غير متاح؛ يظهر الصرف بعملته الأصلية فقط.';
  if (/ad_start/.test(code)) return 'صرف يوم البداية يحتاج إثباتًا يفصل الصرف السابق عن الصرف بعد بداية النظام.';
  if (/advertising_closed|ad_correction/.test(code)) return 'تغير صرف يوم إعلاني مغلق؛ يلزم تصحيح موثق قبل اعتماد الفرق.';
  if (/ad_|advertising/.test(code)) return 'بيانات الصرف أو إثبات إغلاق اليوم الإعلاني غير مكتملة.';
  if (/supplier|invoice/.test(code)) return 'دليل فاتورة المورد غير مكتمل أو متعارض؛ يلزم مراجعة الفاتورة وبنودها.';
  return 'إعداد غير مكتمل؛ يلزم استكمال بيانات المصدر.';
}
function OperationalIssueAlerts({items}) {
  // Group for display only. The API and financial reconciliation retain each original issue.
  const groups = React.useMemo(() => {
    const result = new Map();
    for (const issue of Array.isArray(items) ? items : []) {
      const original = typeof issue === 'string' ? issue : issue?.code;
      const code = typeof original === 'string' && original.trim() ? original.trim() : 'source_issue_unknown';
      if (!result.has(code)) result.set(code, {code, count: 0, sources: [], messages: []});
      const group = result.get(code);
      group.count += 1;
      const sourceId = issue && typeof issue === 'object' ? issue.source_id : null;
      if ((typeof sourceId === 'string' || typeof sourceId === 'number') &&
          group.sources.length < 5 && !group.sources.includes(String(sourceId))) {
        group.sources.push(String(sourceId));
      }
      const message = issue && typeof issue === 'object' && typeof issue.message === 'string' ? issue.message.trim() : '';
      if (message && group.messages.length < 3 && !group.messages.includes(message)) group.messages.push(message);
    }
    return [...result.values()].sort((a, b) => b.count - a.count || a.code.localeCompare(b.code));
  }, [items]);
  if (!groups.length) return null;
  const total = groups.reduce((sum, group) => sum + group.count, 0);
  const count = value => value.toLocaleString('en-US');
  return <section className="op-issue-summary" aria-label="تنبيهات مصادر الأرصدة">
    <h3>تنبيهات مصادر الأرصدة: {count(total)} تنبيه في {count(groups.length)} أنواع</h3>
    <p className="op-muted">جُمعت التنبيهات المتشابهة للعرض فقط، ولم تُحذف أو تُعتبر مكتملة. افتح كل نوع للاطلاع على السبب وعينات المراجع.</p>
    {groups.map(group => <details className="op-error op-issue-group" key={group.code}>
      <summary><span>{feeIncomplete({code: group.code}) ? 'إعداد العمولة غير مكتمل' : issueMessage(group.code)}</span>
        <strong>{count(group.count)} {group.count === 1 ? 'تنبيه' : 'تنبيهًا'}</strong></summary>
      <div className="op-issue-detail">
        {!feeIncomplete({code: group.code}) && <p>رمز التنبيه: <code dir="ltr">{group.code}</code></p>}
        {group.messages.map(message => <p key={message}>{message}</p>)}
        {group.sources.length > 0 && <p>عينات المراجع: <bdi>{group.sources.join('، ')}</bdi></p>}
        {group.sources.length > 0 && group.count > group.sources.length && <p>تُعرض عينات محدودة فقط هنا. تبقى جميع التنبيهات الأصلية ضمن استجابة التقارير للتدقيق.</p>}
      </div>
    </details>)}
  </section>;
}
function useOperationalRead({read, onData, onError, onStart, onEnd, enabled = true, refreshKey = ''}) {
  const callbacks = useRef(null);
  const lane = useRef({});
  callbacks.current = {read, onData, onError, onStart, onEnd};
  useEffect(() => {
    if (!enabled) return;
    return visibleOperationalRead(config => callbacks.current.read(config), {
      onStart: () => callbacks.current.onStart?.(),
      onData: result => callbacks.current.onData?.(result),
      onError: failure => callbacks.current.onError?.(failure),
      onEnd: () => callbacks.current.onEnd?.(),
    }, lane.current);
  }, [enabled, refreshKey]);
}

function AuditHistory({refreshKey}) {
  const [opened, setOpened] = useState(false);
  const [items, setItems] = useState(null), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [revision, setRevision] = useState(0);
  useOperationalRead({
    read: config => api.audit(config), enabled: opened,
    refreshKey: JSON.stringify([refreshKey, revision]),
    onStart: () => { setBusy(true); setItems(null); setError(''); },
    onData: result => setItems(result.items),
    onError: failure => setError(messageFor(failure)),
    onEnd: () => setBusy(false),
  });
  return <details className="op-card" open={opened} onToggle={event => setOpened(event.currentTarget.open)}>
    <summary>سجل التدقيق</summary>
    {opened && <div>
      <button type="button" disabled={busy} onClick={() => setRevision(value => value + 1)}>تحديث سجل التدقيق</button>
      {busy && <p role="status">جاري قراءة سجل التدقيق…</p>}
      {error && <Notice error={error}/>}
      {!busy && !error && items !== null && (items.length ? items.map((item, index) => <p key={index}>
        {item.message || item.action_label || 'تم تسجيل عملية موثقة'} · {(item.at || item.created_at) ? new Date(item.at || item.created_at).toLocaleString('ar-SA') : ''} {item.reason || ''}
      </p>) : <p>لا توجد عمليات مسجلة.</p>)}
    </div>}
  </details>;
}

function Reports({context,source,refreshVersion,visible,onRefresh,contextBusy}) {
  const [report, setReport] = useState(null),
    [error, setError] = useState(''),
    [kind, setKind] = useState(''),
    [revision, setRevision] = useState(0),
    [busy, setBusy] = useState(false),
    [completedKey, setCompletedKey] = useState(null);
  const refreshKey = JSON.stringify([refreshVersion, revision, context.status, context.started_at]);
  useOperationalRead({
    read: async config => { const update = context.status === 'active' ? await refreshVisibleSnapshot(api, config) : null;
      return {...await api.reports(config), ...(update?.refresh_warning ? {refresh_warning: update.refresh_warning} : {})}; }, enabled: visible && !contextBusy, refreshKey,
    onStart: () => setBusy(true),
    onData: result => { setReport(result); setError(''); setCompletedKey(refreshKey); },
    onError: failure => { setError(messageFor(failure)); setCompletedKey(refreshKey); },
    onEnd: () => setBusy(false),
  });
  if (!visible) return null;
  const refreshControl = <button type="button" disabled={contextBusy || busy || completedKey !== refreshKey}
    onClick={onRefresh}>تحديث الأرصدة</button>;
  if (error) return <section><Notice error={error}/>{refreshControl}</section>;
  if (!report) return <p role="status">جاري تحميل الأرصدة…</p>;
  const summaryEntries = Object.entries(report.known_summaries || report.summaries || {});
  if (!summaryEntries.length && Object.keys(report.summary || {}).length) summaryEntries.push(['SAR', report.summary]);
  return <section><h2>الأرصدة والتقارير التشغيلية</h2>{refreshControl}{(contextBusy || busy) && <p role="status">جاري تحديث الأرصدة…</p>}<p className="op-muted">آخر تحديث: {report.as_of ? new Date(report.as_of).toLocaleString('ar-SA') : context.status === 'draft' ? 'لم يبدأ الاحتساب بعد؛ أنهِ الأرصدة الافتتاحية أولًا.' : 'لم تكتمل قراءة التقرير بعد'}</p><CustomerPaymentTotals report={report} /><OperationalIssueAlerts items={report.issues} /><div className="op-summary">{Object.entries(labels).map(([key, label]) => <article key={key}><span>{label}</span><strong>{summaryEntries.map(([currency, summary]) => `${summary[key] ?? 'غير مكتمل'} ${currency}`).join(' / ') || (context.status === 'draft' ? 'لم يبدأ الاحتساب' : 'لا توجد بيانات محسوبة')}</strong></article>)}</div><label className="op-field">نوع الجهة<select value={kind} onChange={e => setKind(e.target.value)}><option value="">جميع الجهات</option>{entityKinds.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
    <PaginatedReportTable label="أرصدة الجهات" resetKey={kind} rows={report.parties.filter(p => !['operating_expense','owner_withdrawal'].includes(p.party_type) && (!kind || p.party_type === kind))}>{rows =>
      <div className="op-table"><table>
        <thead><tr>{['الجهة', 'العملة', 'الابتدائي', 'التقديري لنا', 'التقديري علينا', 'المؤكد لنا', 'المؤكد علينا', 'المسدد', 'الفعلي', 'المتبقي المحتسب لنا', 'المتبقي المحتسب علينا', 'طلبات مدفوعة / تواصيل محتسبة', 'طلبات غير مؤكدة'].map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{rows.map(party => <tr key={`${party.party_type}:${party.party_id}:${party.currency}`}>
          {['name', 'currency', 'opening', 'expected_receivable', 'expected_payable', 'confirmed_receivable', 'confirmed_payable', 'settled', 'actual', 'outstanding_receivable', 'outstanding_payable'].map(key => <td key={key}>{(Object.prototype.hasOwnProperty.call(party, `known_${key}`) ? party[`known_${key}`] : party[key]) ?? 'غير مكتمل'}</td>)}
          <td>{(party.party_type === 'provider' ? party.paid_orders : party.confirmed_orders) ?? '—'}</td>
          <td>{party.pending_orders == null ? '—' : <PendingOrdersDialog count={party.pending_orders} details={party.pending_order_details} name={party.name} />}</td>
        </tr>)}</tbody>
      </table>{!report.parties.length && <p>لا توجد أرصدة مسجلة بعد.</p>}</div>
    }</PaginatedReportTable>
    {report.refresh_warning && <p role="alert" className="op-error">{report.refresh_warning}</p>}
    {report.issues?.length > 0 && <p className="op-muted">المجاميع تعرض الجزء المحتسب من الأرصدة. الطلبات غير المؤكدة لا تلغي هذا الجزء؛ اضغط عددها لمراجعة رقم الطلب والسبب.</p>}
    {Object.entries(report.unassigned_order_details || {}).flatMap(([currency, kinds]) => Object.entries(kinds).filter(([, orders]) => orders.length).map(([kind, orders]) =>
      <p key={`${currency}:${kind}`}><PendingOrdersDialog count={orders.length} details={orders} name={`طلبات لم تثبت جهة توصيلها · ${currency}`} /></p>))}
    {Object.entries(report.details?.customer_wallet_balances || {}).map(([currency, balance]) => <p key={currency}>
      رصيد محافظ العملاء المستحق علينا {balance.remaining ?? 'غير متاح'} {currency} · وفق كشف سلة حتى {formatRiyadhDateTime(balance.as_of)}
    </p>)}
    {context.permissions?.manage && context.status === 'active' && source !== 'employee_app' && <CustomerWalletBalance key={context.session_scope} sessionScope={context.session_scope} onSaved={() => setRevision(value => value + 1)} />}
    <OperationalCostExposure scopeKey={context.session_scope} enabled={context.status !== 'draft'} /><DomainDetails report={report}/><InventoryPurchases mode="reports" context={context} source={source}/><AuditHistory refreshKey={JSON.stringify([revision, context.status, context.started_at])}/></section>;
}
function OperationalBalancesContent({
  source = 'mezan2'
}) {
  const [context, setContext] = useState(null),
    [error, setError] = useState(''),
    [tab, setTab] = useState(source === 'employee_app' ? 'movements' : 'openings'),
    [version, setVersion] = useState(0),
    [contextRevision, setContextRevision] = useState(0),
    [contextBusy, setContextBusy] = useState(true);
  const load = useCallback(() => { setContextBusy(true); setContextRevision(value => value + 1); }, []);
  useOperationalRead({
    read: config => api.context(config), refreshKey: contextRevision,
    onStart: () => setContextBusy(true),
    onData: result => {
      setError(''); setContext(result);
      setVersion(value => value + 1);
      setTab(tab => tab === 'openings' && (result.status !== 'draft' || !result.permissions?.manage) ? (result.permissions?.reports ? 'reports' : 'movements') : tab);
    },
    onError: failure => setError(messageFor(failure)),
    onEnd: () => setContextBusy(false),
  });
  if (error) return <main className="op-page" dir="rtl"><Notice error={error} /><button onClick={load}>إعادة المحاولة</button></main>;
  if (!context) return <main className="op-page" dir="rtl" role="status">جاري تحميل النظام التشغيلي…</main>;
  const permissions = context.permissions || {};
  const active = context.status === 'active';
  return <main className="op-page" dir="rtl"><header><span className="op-eyebrow">ميزان · التشغيل اليومي</span><h1>الأرصدة التشغيلية</h1><p>{context.status === 'draft' ? 'ابدأ بإدخال الأرصدة الافتتاحية' : active ? 'تُحمّل الأرصدة وتُحتسب أثناء فتح التقرير فقط، ويمكنك تحديثها عند الحاجة.' : 'النظام للقراءة والتدقيق فقط'}</p></header><nav aria-label="صفحات الأرصدة التشغيلية">{permissions.reports && <a href="/operational-balances/daily-movements">سجل الحركات المالية اليومية</a>}{source !== 'employee_app' && <a href="/inventory-v2">صفحة المخزون المستقلة</a>}{context.status === 'draft' && permissions.manage && <button aria-current={tab === 'openings' ? 'page' : undefined} onClick={() => setTab('openings')}>الأرصدة الافتتاحية</button>}{context.can_manage_inventory_opening === true && <button aria-current={tab === 'opening-stock' ? 'page' : undefined} onClick={() => setTab('opening-stock')}>المخزون الافتتاحي</button>}{context.can_manage_salary_start === true && source !== 'employee_app' && <button aria-current={tab === 'salary-start' ? 'page' : undefined} onClick={() => setTab('salary-start')}>بداية الرواتب</button>}{permissions.move && <button aria-current={tab === 'movements' ? 'page' : undefined} onClick={() => setTab('movements')}>الحركات المالية اليومية</button>}{permissions.reports && <button aria-current={tab === 'reports' ? 'page' : undefined} onClick={() => setTab('reports')}>الأرصدة والتقارير التشغيلية</button>}{permissions.move&&<button aria-current={tab==='inventory'?'page':undefined} onClick={()=>setTab('inventory')}>شراء المخزون</button>}{permissions.reports&&<button aria-current={tab==='stock'?'page':undefined} onClick={()=>setTab('stock')}>المخزون المتاح</button>}</nav>{tab === 'openings' && context.status === 'draft' && permissions.manage && <OpeningBalances context={context} onFinished={load} />} {tab === 'movements' && permissions.move && (active ? <><DailyMovements context={context} cards canCreateCash={context.can_create_cash === true} key={context.session_scope} storageScope={context.session_scope} onScopeChanged={load} canManage={permissions.manage} source={source} onSaved={() => setVersion(v => v + 1)} /><a className="op-button" href="/operational-balances/daily-movements">عرض سجل الحركات المالية اليومية</a>{permissions.manage && <SupplierReturn />}</> : <p>تسجيل الحركات متاح بعد إنهاء الأرصدة الافتتاحية وأثناء تشغيل النظام.</p>)}{permissions.reports && <Reports key={`reports:${context.session_scope}`} context={context} source={source} refreshVersion={version} visible={tab === 'reports'} onRefresh={load} contextBusy={contextBusy}/>}{tab === 'salary-start' && context.can_manage_salary_start === true && source !== 'employee_app' && <SalaryStart key={`${context.session_scope}:${context.started_at}:${context.status}`} context={context}/>}{tab==='opening-stock'&&context.can_manage_inventory_opening === true&&<OpeningInventory key={context.session_scope} context={context}/>} {tab==='stock'&&permissions.reports&&<InventoryAvailability key={context.session_scope} context={context} onOpeningInventory={()=>setTab('opening-stock')}/ >}{tab==='inventory'&&permissions.move&&<InventoryPurchases key={context.session_scope} context={context} source={source}/>}</main>;
}


export default function OperationalBalances({source = 'mezan2'}) {
  const auth = useOptionalAuth();
  const user = auth?.user;
  if (auth?.loading) return <main className="op-page" dir="rtl" role="status">جاري تحميل النظام التشغيلي…</main>;
  if (auth && !user) return <main className="op-page" dir="rtl"><Notice error="يلزم تسجيل الدخول لعرض الأرصدة التشغيلية."/></main>;
  const scope = auth ? JSON.stringify([source, user?.id, user?.created_by, user?.owner_id, user?.merchant_id,
    user?.tenant_id, user?.role, user?.is_owner, auth.authStatus]) : source;
  return <OperationalBalancesContent key={scope} source={source}/>;
}
