import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { todaySA } from '../../lib/dates';
import { AdBalance, Amount, Heading, Icon, IconBox, MovementIcon, isAsset, issueLabel, styleOf } from './ApprovedFinanceUI';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { useBalanceRead } from './simpleBalancesHooks';
import { isStatement, minorUnits, movementDate, movementRecorder, movementTitle, resourceKey, signedResourceBalance } from './simpleBalancesModel';

const pageSize = 8;
function defaultPeriod(start) {
  const date_to = todaySA(), first = date_to.slice(0, 7) + '-01';
  return { date_from: start > first && start <= date_to ? start : first, date_to };
}

function Range({ value, onChange, start }) {
  const [draft, setDraft] = useState(value);
  return <form className="filters range screen-only" onSubmit={event => { event.preventDefault(); onChange(draft); }}>
    <label>من<input name="from" type="date" required min={start || undefined} max={draft.date_to || undefined}
      value={draft.date_from} onChange={e => setDraft({ ...draft, date_from: e.target.value })} /></label>
    <label>إلى<input name="to" type="date" required min={draft.date_from} value={draft.date_to}
      onChange={e => setDraft({ ...draft, date_to: e.target.value })} /></label>
    <button className="button" type="submit">عرض الفترة</button>
  </form>;
}

function Pagination({ result, onPage }) {
  if (!result?.total) return null;
  const { page, total, total_pages } = result;
  return <nav className="movement-pages screen-only" aria-label="صفحات جدول الحركات">
    <span>عرض {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} من {total}</span>
    <div><button type="button" className="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>السابق</button>
      <span aria-live="polite">صفحة {page} من {total_pages}</span>
      <button type="button" className="button" disabled={page >= total_pages} onClick={() => onPage(page + 1)}>التالي</button></div>
  </nav>;
}

function LedgerBalance({ value, resource }) {
  const units = minorUnits(value);
  if (units === null) return <span aria-label="الرصيد غير متاح">—</span>;
  const cash = ['bank', 'cash'].includes(resource.kind);
  const positiveIsOwed = resource.kind === 'employee_custody';
  const side = !cash && units !== 0n ? (units > 0n) === positiveIsOwed ? 'عليه للمؤسسة' : 'له عند المؤسسة' : '';
  return <><Amount value={cash ? value : String(value).replace(/^-/, '')} currency={resource.currency} />
    {side && <small className="under">{' '}{side}</small>}</>;
}

function Metrics({ period, resource }) {
  const cash = ['cash', 'bank'].includes(resource.kind);
  const visible = period.scope === 'all' && resource.access?.balance === true;
  const card = (title, content, tone) => <div className={'metric ' + tone}><small>{title}</small><strong>{content}</strong></div>;
  return <div className="metrics">
    {visible && card('رصيد بداية الفترة', <LedgerBalance value={period.opening_balance} resource={resource} />, '')}
    {card(cash ? 'دخل الحساب' : 'مبالغ له', <Amount value={period.totals[cash ? 'for_us' : 'for_party']} currency={resource.currency} />, 'income')}
    {card(cash ? 'خرج من الحساب' : 'مبالغ عليه', <Amount value={period.totals[cash ? 'for_party' : 'for_us']} currency={resource.currency} />, 'expense')}
    {visible && card('رصيد آخر الفترة', <LedgerBalance value={period.closing_balance} resource={resource} />, 'accent')}
  </div>;
}

function ActivityTable({ result, resource, onDetail, onHistory }) {
  if (!result.items.length) return <p className="empty">لا توجد حركات تطابق اختيارك في هذه الفترة.</p>;
  const cash = resource && ['bank', 'cash'].includes(resource.kind);
  const firstSide = cash ? 'for_us' : 'for_party';
  const balanceVisible = resource?.access?.balance === true && resource?.access?.movements === 'all';
  return <div className="scroll"><table className={'ledger ' + (resource ? 'statement' : 'global')}><thead>
    <tr>{(resource ? ['البيان', cash ? 'دخل' : 'له', cash ? 'خرج' : 'عليه', ...(balanceVisible ? ['الرصيد بعدها'] : [])]
      : ['الحركة ومن سجّلها', 'من وإلى', 'المبلغ', 'الحالة', 'المرجع']).map(t => <th key={t} scope="col">{t}</th>)}</tr>
  </thead><tbody>{result.items.map(row => {
    const credit = row.side === firstSide;
    return <tr key={row.id}><td><button type="button" className="tablelink" onClick={() => onDetail(row)}>
      <MovementIcon type={row.movement_type} /><span>{movementTitle(row)}<small>{movementDate(row)} · {movementRecorder(row)}</small>
        {row.informational && resource && <small>إثبات للمؤسسة · <Amount value={row.amount} currency={row.currency} /></small>}</span></button>
      {onHistory && row.automatic_history && <button type="button" className="textbtn" onClick={() => onHistory(row)}>
        {row.automatic_history === 'salary' ? 'سجل الرواتب' : 'سجل التوصيلات'}</button>}</td>
      {resource ? <><td className="income">{credit && !row.informational ? <Amount value={row.amount} currency={row.currency} /> : '—'}</td>
        <td className="expense">{!credit && !row.informational ? <Amount value={row.amount} currency={row.currency} /> : '—'}</td>
        {balanceVisible && <td><LedgerBalance value={row.balance_after} resource={resource} /></td>}</>
        : <><td>{row.from_name && row.to_name ? <>{row.from_name} ← {row.to_name}</> : row.party_name || '—'}</td>
          <td><Amount value={row.amount} currency={row.currency} /></td>
          <td><span className={'pill ' + (row.informational ? '' : 'st-done')}>{row.informational ? 'إثبات' : 'مسجّلة'}</span></td>
          <td><bdi>{row.reference || row.order_number || '—'}</bdi></td></>}
    </tr>;
  })}</tbody></table></div>;
}

function PeriodPrint({ resource, period, scope }) {
  const [request, setRequest] = useState(0);
  const [print, setPrint] = useState(null), [failure, setFailure] = useState('');
  // Recheck the current server permissions and load every page only on an
  // explicit print request. Navigation or auth changes cancel that request.
  useEffect(() => {
    if (!request) return undefined;
    const controller = new AbortController();
    const cancel = () => { if (document.visibilityState === 'hidden') { controller.abort(); setRequest(0); setPrint(null); } };
    document.addEventListener('visibilitychange', cancel);
    const query = { date_from: period.date_from, date_to: period.date_to, purpose: 'print' };
    Promise.all([api.statement(resource.kind, resource.id, query, { signal: controller.signal }),
      import('./SimpleBalanceStatement'), import('./simpleBalances.css')])
      .then(([value, { StatementDocument }]) => {
        if (controller.signal.aborted) return;
        if (!isStatement(value, resource, query) || value.can_print !== true) throw new Error('invalid print document');
        setPrint({ value, StatementDocument, scope });
      }).catch(error => { if (!controller.signal.aborted) { setFailure(simpleBalanceError(error)); setRequest(0); } });
    return () => { controller.abort(); document.removeEventListener('visibilitychange', cancel); };
  }, [request, scope, period.date_from, period.date_to, resource.kind, resource.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!print || scope !== print.scope) return;
    if (document.visibilityState !== 'hidden') {
      document.body.classList.add('sb-statement-printing');
      try { window.print(); } catch { setFailure('تعذر فتح الطباعة. أعد المحاولة.'); }
      finally { document.body.classList.remove('sb-statement-printing'); }
    }
    setPrint(null); setRequest(0);
  }, [print, scope]);
  return <><button type="button" className="button" disabled={Boolean(request)} onClick={() => { setFailure(''); setRequest(1); }}>
    <Icon name="printer" />{request ? 'جارٍ تجهيز الكشف' : 'طباعة الكشف'}</button>
    {failure && <small role="alert">{failure}</small>}
    {print?.scope === scope && createPortal(<div dir="rtl" className="sb-print-root"><print.StatementDocument statement={print.value} printing /></div>, document.body)}</>;
}

function LedgerPage({ context, identity, resource, onDetail, onAdd, onBack, onReviewOrders, onHistory, onExpected, onReviewCollections }) {
  const [period, setPeriod] = useState(() => defaultPeriod(context.ledger_start));
  const [query, setQuery] = useState(''), [draft, setDraft] = useState('');
  const [account, setAccount] = useState(''), [direction, setDirection] = useState(''), [status, setStatus] = useState('');
  const [page, setPage] = useState(1), [detail, setDetail] = useState(null);
  const dialog = useRef(null);
  useEffect(() => {
    if (draft.trim() === query) return undefined;
    const timer = setTimeout(() => { setQuery(draft.trim()); setPage(1); }, 450);
    return () => clearTimeout(timer);
  }, [draft, query]);
  const selected = !resource && context.resources.find(r => resourceKey(r) === account);
  const scope = JSON.stringify([identity, context.session_scope, context.permission_version]);
  const key = JSON.stringify([scope, resource?.kind, resource?.id, account, period.date_from, period.date_to, direction, status, query, page]);
  const readable = resource ? resource.access?.movements !== 'none' : context.resources.some(r => r.access?.movements !== 'none');
  const read = useBalanceRead(async config => {
    const params = { ...period, page, page_size: pageSize, ...(query && { q: query }), ...(direction && { direction }),
      ...(status && { status }), ...(selected && { kind: selected.kind, id: selected.id }) };
    const result = resource ? await api.ledger(resource.kind, resource.id, params, config) : await api.movements(params, config);
    if (!Array.isArray(result?.items) || result.items.length > pageSize || result.page_size !== pageSize ||
        !Number.isInteger(result.total) || !Number.isInteger(result.total_pages) || result.page !== Math.min(page, result.total_pages) ||
        (resource && (result.resource?.kind !== resource.kind || result.resource?.id !== resource.id))) throw new Error('invalid ledger');
    return result;
  }, key, readable);
  const result = read.data, summary = resource ? result?.period : null;
  const changePeriod = next => { setPeriod(next); setPage(1); };
  const search = <form className="searchbox directory-search" role="search" onSubmit={event => { event.preventDefault(); setQuery(draft.trim()); setPage(1); }}>
    <Icon name="search" /><input type="search" maxLength={160} value={draft} aria-label={resource ? 'بحث في حركات ' + resource.name : 'ابحث في الحركات'}
      placeholder={resource ? 'ابحث بالبيان أو المرجع أو اسم الموظف' : 'ابحث في الحركات'} onChange={e => setDraft(e.target.value)} /></form>;
  const show = row => { if (onDetail) onDetail(row); else { setDetail(row); } };
  useEffect(() => { if (detail && dialog.current && !dialog.current.open) { if (dialog.current.showModal) dialog.current.showModal(); else dialog.current.setAttribute('open', ''); } }, [detail]);
  const issueCount = resource?.order_issues_count;
  const changeStatus = value => {
    if (['pending', 'rejected'].includes(value) && onReviewCollections) { onReviewCollections(selected || null, value); return; }
    setStatus(value); setPage(1);
  };
  return <>
    {resource && onBack && <button type="button" className="textbtn back" onClick={() => onBack(resource)}><Icon name="chevron-left" />
      {isAsset(resource.kind) ? 'الأصول والحسابات' : 'الأشخاص والجهات'}</button>}
    <Heading title={resource?.name || 'سجل الحركات'} subtitle={resource ? styleOf(resource)[0] + ' · كشف الحساب' : 'ابحث باسم الحساب أو الموظف أو رقم الطلب أو المرجع.'}>
      {onAdd && <button type="button" className="button primary" onClick={() => onAdd(resource)}><Icon name="plus" />{resource?.kind === 'ad_account' ? 'شحن رصيد الإعلان' : resource ? 'سجّل حركة' : 'حركة مالية جديدة'}</button>}
      {summary?.can_print && <PeriodPrint key={JSON.stringify([scope, period, resourceKey(resource)])} resource={resource} scope={scope} period={period} />}
    </Heading>
    {resource && readable && <section className="panel sheet"><div className="section-head"><h2>كشف الفترة</h2><Icon name="calendar-days" /></div>
      <Range value={period} start={context.ledger_start} onChange={changePeriod} /></section>}
    {summary && <Metrics period={summary} resource={resource} />}
    {resource?.kind === 'ad_account' && resource.access?.balance && <div className="metric accent"><small>الرصيد الحالي</small><strong><AdBalance resource={resource} /></strong></div>}
    {resource?.kind !== 'ad_account' && resource?.access?.balance && (!resource.access.report || !readable) && <div className="metrics"><div className="metric accent">
      <small>الرصيد الحالي</small><strong><LedgerBalance value={signedResourceBalance(resource)} resource={resource} /></strong></div></div>}
    {resource?.access?.movements === 'own' && <p className="note">يعرض هذا الكشف الحركات التي سجّلتها أنت.</p>}
    {result?.balance_note && <p className="note">{result.balance_note}</p>}
    {(issueCount > 0 || issueCount === null) && onReviewOrders && <button type="button" className="issue-notice screen-only" onClick={() => onReviewOrders(resource)}>
      <IconBox name="clipboard-list" tone="c-wait" /><span><strong>{issueLabel(issueCount)}</strong><small>عرض طلبات المراجعة في آخر احتساب</small></span><b>عرض الطلبات <Icon name="chevron-left" /></b></button>}
    {resource && result?.pos_summary && <dl className="detail-list">{[['إيصالات الشبكة المعتمدة','approved'],['المحوّل إلى البنوك','bank_received'],['الرسوم الفعلية','fees']]
      .map(([label, field]) => <div key={field}><dt>{label}</dt><dd><Amount value={result.pos_summary[field]} currency={resource.currency} /></dd></div>)}</dl>}
    {resource && onExpected && result?.expected_collections?.total > 0 && <button type="button" className="textbtn" onClick={() => onExpected(resource, result.expected_collections)}>
      متوقع تحصيله · <Amount value={result.expected_collections.amount} currency={resource.currency} /> · {result.expected_collections.total} طلب</button>}
    <section className="panel sheet">
      {resource && <div className="section-head"><h2>حركات {resource.name}</h2>{result && <small>عدد حركات الفترة: {summary?.total ?? result.total}</small>}</div>}
      {readable ? <>
        <div className="filters screen-only">{search}{!resource && <>
          <select aria-label="الحساب" value={account} onChange={e => { setAccount(e.target.value); setPage(1); }}><option value="">كل الحسابات والجهات</option>
            {context.resources.filter(r => r.access?.movements !== 'none').map(r => <option value={resourceKey(r)} key={resourceKey(r)}>{r.name}</option>)}</select>
          <select aria-label="نوع الحركة" value={direction} onChange={e => { setDirection(e.target.value); setPage(1); }}><option value="">كل الأنواع</option>
            <option value="in">استلام</option><option value="out">دفع</option><option value="transfer">تحويل داخلي</option></select>
          <select aria-label="حالة الحركة" value={status} onChange={e => changeStatus(e.target.value)}>
            <option value="">كل الحالات المسجّلة</option><option value="done">مسجّلة</option><option value="info">إثبات</option>
            {onReviewCollections && (!selected || ['store_driver', 'bank'].includes(selected.kind)) && <><option value="pending">بانتظار المراجعة</option><option value="rejected">تحتاج تعديل</option></>}</select>
        </>}</div>
        {!resource && <Range value={period} onChange={changePeriod} start={context.ledger_start} />}
        {read.loading && <p role="status" className="empty">جارٍ تحميل الحركات…</p>}
        {read.error && <div className="empty" role="alert">{read.error} <button type="button" className="textbtn" onClick={read.reload}>إعادة المحاولة</button></div>}
        {result && <><ActivityTable result={result} resource={resource} onDetail={show} onHistory={onHistory} /><Pagination result={result} onPage={setPage} /></>}
      </> : <p className="empty">عرض الحركات غير متاح ضمن صلاحياتك.</p>}
    </section>
    {resource?.kind === 'store_driver' && resource.access?.movements === 'all' && onReviewCollections &&
      <p className="footnote"><button type="button" className="textbtn" onClick={() => onReviewCollections(resource, 'pending')}><Icon name="clock-3" />عرض التحصيلات التي تنتظر الاعتماد لهذا الحساب</button></p>}
    {detail && <dialog ref={dialog} className="panel" onClose={() => setDetail(null)}>
      <h2>تفاصيل الحركة</h2><dl className="detail-list">{[['البيان',detail.description],['المبلغ',<Amount value={detail.amount} currency={detail.currency} />],
        ['الحساب',detail.party_name],['من',detail.from_name],['إلى',detail.to_name],['التاريخ',movementDate(detail,true)],['سجّلها',movementRecorder(detail)],
        ['المرجع',detail.reference],['رقم الطلب',detail.order_number],['الملاحظة',detail.note]].filter(([,v])=>v).map(([t,v])=><div key={t}><dt>{t}</dt><dd>{v}</dd></div>)}</dl>
      <button type="button" className="button" onClick={() => setDetail(null)}>إغلاق</button></dialog>}
  </>;
}

export default function ApprovedLedger(props) {
  const { context, identity, resource } = props;
  return <LedgerPage key={JSON.stringify([identity, context.session_scope, context.permission_version, resource?.kind, resource?.id])} {...props} />;
}
