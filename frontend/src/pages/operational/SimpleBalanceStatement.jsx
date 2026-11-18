import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { todaySA } from '../../lib/dates';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { canReadStatement, isMoneyAccount, isStatement, minorUnits, money, movementDate, movementRecorder, movementTitle,
  resourceKey, resourceLabels } from './simpleBalancesModel';

export function SignedAmount({ value, hidden = false }) {
  const units = hidden ? null : minorUnits(value);
  return <span className="sb-running" data-sign={units === null ? undefined : units > 0n ? 'positive' : units < 0n ? 'negative' : 'zero'}>
    {units === null ? '—' : <bdi>{money(value)}</bdi>}
  </span>;
}

const statementTypes = {
  bank: 'بنك', cash: 'صندوق', employee: 'الموظف', store_driver: 'الموصل',
  supplier: 'المورد', external_person: 'الجهة الخارجية', courier: 'شركة الشحن',
  provider: 'مزود الدفع', pos_receivable: 'شبكة', employee_custody: 'عهدة الموظف',
  ad_account: 'حساب الإعلانات', operating_expense: 'المصروفات', owner_withdrawal: 'المالك',
};

function statementTitle(resource) {
  const name = String(resource.name || '').trim(), type = statementTypes[resource.kind] || '';
  const namedType = name === type || name.startsWith(`${type} `) || (resource.kind === 'bank' && /^البنك\s/.test(name));
  return `كشف حساب ${[!namedType && type, name].filter(Boolean).join(' ')}`;
}

function periodDate(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? `${value.slice(8)}/${value.slice(5, 7)}/${value.slice(0, 4)}` : 'غير محدد';
}

function balanceSide(value, resource) {
  const units = minorUnits(value);
  if (units === null) return '';
  if (units === 0n) return 'متعادل';
  const account = isMoneyAccount(resource.kind) || resource.kind === 'employee_custody';
  return (units > 0n) === account ? 'عليه' : 'له';
}

const savedNote = row => typeof row.note === 'string' && row.note.trim() ? row.note : '';

export function StatementDocument({ statement, printing = false }) {
  const resource = statement.resource;
  const balances = statement.scope === 'all' && resource.access?.balance === true
    && (statement.opening_balance !== null || statement.closing_balance !== null || statement.items.some(row => row.balance_after != null));
  const notes = statement.items.some(row => Boolean(savedNote(row)));
  return <section className={printing ? 'sb-print-document' : 'sb-statement-document'} aria-label="كشف حساب الفترة">
    <h2>{statementTitle(resource)}</h2>
    <p className="sb-small-note sb-statement-period">الفترة من <bdi>{periodDate(statement.date_from)}</bdi> إلى <bdi>{periodDate(statement.date_to)}</bdi> · {statement.currency}</p>
    {statement.scope === 'own' && <p className="sb-limited-report">كشف نشاط محدود: الحركات التي سجّلتها أنت فقط. لا يعرض الرصيد الافتتاحي أو الختامي أو الرصيد بعد الحركة.</p>}
    {typeof statement.balance_note === 'string' && statement.balance_note && <p className="sb-limited-report">{statement.balance_note}</p>}
    {!balances && statement.scope !== 'own' && !statement.balance_note && <p className="sb-small-note">الأرصدة غير متاحة في هذا الكشف.</p>}
    <div className="sb-table-wrap"><table className={`sb-ledger sb-statement-table${balances ? ' sb-with-running' : ''}${notes ? ' sb-with-notes' : ''}`}>
      <thead><tr><th scope="col">البيان</th><th scope="col" data-side="for_party">له</th><th scope="col" data-side="for_us">عليه</th>{balances && <th scope="col" className="sb-statement-balance">الرصيد بعد الحركة</th>}{notes && <th scope="col" className="sb-statement-note">الملاحظة</th>}</tr></thead>
      <tbody>{statement.items.map((row, index) => <tr key={`${row.id}:${index}`}>
        <td><strong>{movementTitle(row)}</strong><small title={movementDate(row, true)}><bdi>{movementDate(row)}</bdi></small>{row.order_number && row.customer_name && <small>طلب {row.order_number}</small>}{row.informational && <small>إثبات للمؤسسة · {money(row.amount)} {row.currency || statement.currency}</small>}<small>سجّلها: {movementRecorder(row)}</small></td>
        <td data-side="for_party">{!row.informational && row.side === 'for_party' ? money(row.amount) : '—'}</td>
        <td data-side="for_us">{!row.informational && row.side === 'for_us' ? money(row.amount) : '—'}</td>
        {balances && <td className="sb-statement-balance"><SignedAmount value={row.balance_after} /></td>}
        {notes && <td className="sb-statement-note">{savedNote(row) || '—'}</td>}
      </tr>)}</tbody>
    </table></div>
    {!statement.items.length && <p className="sb-empty">لا توجد حركات خلال هذه الفترة.</p>}
    <section className="sb-statement-summary" aria-label="ملخص الكشف">
      <h3>ملخص الفترة</h3>
      <dl className="sb-statement-totals">
        {balances && <div data-statement-total="opening"><dt>الرصيد الافتتاحي</dt><dd><SignedAmount value={statement.opening_balance} /> <span className="sb-balance-side">{balanceSide(statement.opening_balance, resource)}</span></dd></div>}
        <div><dt>إجمالي له</dt><dd><bdi>{money(statement.totals.for_party)}</bdi></dd></div>
        <div><dt>إجمالي عليه</dt><dd><bdi>{money(statement.totals.for_us)}</bdi></dd></div>
        {balances && <div data-statement-total="closing"><dt>الصافي بنهاية الفترة</dt><dd><SignedAmount value={statement.closing_balance} /> <span className="sb-balance-side">{balanceSide(statement.closing_balance, resource)}</span></dd></div>}
      </dl>
      {balances && <p className="sb-small-note">الصافي بنهاية الفترة يشمل الرصيد الافتتاحي وحركات الفترة.</p>}
    </section>
    <p className="sb-statement-prepared">أعدّه: {statement.prepared_by?.name || 'غير موثق'} · {movementDate({ recorded_at: statement.generated_at }, true)}</p>
  </section>;
}

export default function SimpleBalanceStatement({ context, identity, resource: initialResource, onFiltersChange, initialFilters = {} }) {
  const available = context.resources.filter(resource => canReadStatement(context, resource));
  const today = todaySA();
  const [form, setForm] = useState(() => ({ kind: initialResource?.kind || initialFilters.kind || '',
    account: initialResource ? resourceKey(initialResource) : initialFilters.account || '',
    date_from: initialFilters.date_from || `${today.slice(0, 7)}-01`, date_to: initialFilters.date_to || today }));
  const [result, setResult] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState('');
  const [printDocument, setPrintDocument] = useState(null);
  const request = useRef({ sequence: 0, controller: null });
  const selected = available.find(resource => resourceKey(resource) === form.account);
  const scope = JSON.stringify([identity, context.session_scope, context.permission_version]);
  const latestScope = useRef(scope); latestScope.current = scope;
  function cancel() { request.current.sequence += 1; request.current.controller?.abort(); request.current.controller = null; }
  useEffect(() => {
    setResult(null); setPrintDocument(null); setBusy(''); setError('');
    const hide = () => { if (document.visibilityState === 'hidden') { cancel(); setBusy(''); } };
    document.addEventListener('visibilitychange', hide);
    return () => { cancel(); document.removeEventListener('visibilitychange', hide); };
  }, [scope]);
  useEffect(() => {
    if (!printDocument || printDocument.scope !== latestScope.current) return;
    if (document.visibilityState === 'hidden') { setPrintDocument(null); return; }
    document.body.classList.add('sb-statement-printing');
    try { window.print(); } catch { setError('تعذر فتح الطباعة. أعد المحاولة.'); }
    finally { document.body.classList.remove('sb-statement-printing'); setPrintDocument(null); }
    return () => document.body.classList.remove('sb-statement-printing');
  }, [printDocument]);
  function change(patch) {
    cancel(); setBusy(''); setError(''); setResult(null); setPrintDocument(null);
    const next = { ...form, ...patch };
    setForm(next); onFiltersChange?.(next);
  }
  async function load(purpose = 'view') {
    if (!selected || !canReadStatement(context, selected)) { setError('اختر حسابًا متاحًا لكشف الفترة.'); return; }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(form.date_from) || !/^\d{4}-\d{2}-\d{2}$/.test(form.date_to) || form.date_from > form.date_to) {
      setError('اختر بداية ونهاية صحيحتين للفترة.'); return;
    }
    if (purpose === 'print' && !(context.actor.is_owner || selected.access?.print === true) || busy) return;
    cancel(); const sequence = request.current.sequence, controller = new AbortController();
    request.current.controller = controller; setBusy(purpose); setError('');
    const query = { date_from: form.date_from, date_to: form.date_to, purpose };
    const requestedScope = scope;
    try {
      const value = await api.statement(selected.kind, selected.id, query, { signal: controller.signal });
      if (controller.signal.aborted || sequence !== request.current.sequence || requestedScope !== latestScope.current) return;
      if (!isStatement(value, selected, query)) throw { localMessage: 'تعذر التحقق من كشف الفترة. أعد طلبه.' };
      if (purpose === 'print' && value.can_print !== true) throw { response: { status: 403 } };
      setResult(value);
      if (purpose === 'print') setPrintDocument({ value, scope: requestedScope });
    } catch (failure) {
      if (controller.signal.aborted || sequence !== request.current.sequence || requestedScope !== latestScope.current) return;
      setResult(null); setPrintDocument(null); setError(simpleBalanceError(failure));
    } finally { if (sequence === request.current.sequence && requestedScope === latestScope.current) setBusy(''); }
  }
  const canPrint = result?.can_print === true && (context.actor.is_owner || selected?.access?.print === true);
  return <section className="sb-statement-panel" aria-label="التقارير المالية">
    {!available.length ? <p role="alert">لا توجد حسابات بصلاحية عرض التقارير.</p> : <>
      <form className="sb-report-filters" onSubmit={event => { event.preventDefault(); load('view'); }}>
        {!initialResource && <div className="sb-form-two"><label className="sb-field"><span>نوع الحساب</span><select value={form.kind} onChange={event => change({ kind: event.target.value, account: '' })}><option value="">الجميع</option>{[...new Set(available.map(resource => resource.kind))].map(kind => <option key={kind} value={kind}>{resourceLabels[kind] || kind}</option>)}</select></label><label className="sb-field"><span>الحساب</span><select value={form.account} onChange={event => change({ account: event.target.value })}><option value="">اختر الحساب</option>{available.filter(resource => !form.kind || resource.kind === form.kind).map(resource => <option key={resourceKey(resource)} value={resourceKey(resource)}>{resource.name}</option>)}</select></label></div>}
        {initialResource && <p className="sb-small-note">{initialResource.name}</p>}
        <div className="sb-form-two"><label className="sb-field"><span>من تاريخ</span><input type="date" value={form.date_from} onChange={event => change({ date_from: event.target.value })} /></label><label className="sb-field"><span>إلى تاريخ</span><input type="date" value={form.date_to} onChange={event => change({ date_to: event.target.value })} /></label></div>
        <button className="sb-primary" type="submit" disabled={Boolean(busy) || !selected}>{busy === 'view' ? 'جارٍ إعداد الكشف…' : 'عرض الكشف'}</button>
      </form>
      {error && <div className="sb-notice sb-error" role="alert">{error}</div>}
      {result && <><StatementDocument statement={result} />{canPrint && <button className="sb-secondary sb-print-button" type="button" disabled={Boolean(busy)} onClick={() => load('print')}>{busy === 'print' ? 'جارٍ تجهيز الطباعة…' : 'طباعة / حفظ PDF'}</button>}</>}
      {printDocument && createPortal(<div className="sb-print-root" dir="rtl"><StatementDocument statement={printDocument.value} printing /></div>, document.body)}
    </>}
  </section>;
}
