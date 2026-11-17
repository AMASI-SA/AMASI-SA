import React, { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import { todaySA } from '../../lib/dates';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { authIdentity, useBalanceRead } from './simpleBalancesHooks';
import SimpleOrderRefunds from './SimpleOrderRefunds';
import {
  BALANCE_PAGE_SIZE, afterBalance, balanceText, decimalAmount, entryChoices, isMoneyAccount,
  isPage, minorUnits, money, permittedOperations, resourceKey, resourceLabels, validContext,
} from './simpleBalancesModel';
import './simpleBalances.css';

const routeBase = '/operational-balances/daily-movements';
const directReasons = new Set(['salary_settlement', 'charity', 'household', 'personal']);
const reasonLabels = { accrual: 'مستحق', payment: 'دفع', collection: 'تحصيل', adjustment: 'خصم موثق',
  receipt: 'تسوية مديونية', salary_settlement: 'صرف راتب موظف', charity_company: 'صدقات على المؤسسة',
  charity_owner: 'صدقات على المالك', household: 'مصروفات منزلية', personal: 'مصروفات شخصية',
  transfer: 'تحويل بين الحسابات', settlement: 'تسوية', correction: 'تصحيح موثق', refund: 'رد مبلغ' };
const operationGroups = [
  { title: 'مرتجعات الأصناف', text: 'إرجاع المنتجات ومراجعة شحنة الاسترجاع.', to: '/operational-balances/customer-returns', owner: true },
  { title: 'الاستبدال', text: 'مراجعة الأصناف وفرق الاستبدال.', to: '/operational-balances/customer-exchanges', owner: true },
  { title: 'المشتريات والمخزون', text: 'الفواتير والأصناف والاستلام من المورد.', to: '/inventory-v2', owner: true },
  { title: 'مطابقة كشوف مزودي الدفع', text: 'مراجعة الكشوف والتسويات المعتمدة.', to: '/operational-balances/provider-statements', keys: ['provider.settle'] },
  { title: 'المركز المالي', text: 'الأرصدة والتفاصيل المالية الكاملة.', to: '/operational-balances/financial-position', owner: true },
  { title: 'الإعدادات والعمليات المتخصصة', text: 'الأرصدة الافتتاحية ومسارات المراجعة الحالية.', to: '/operational-balances', owner: true },
];

function dateLabel(value) {
  if (!value) return 'تاريخ غير موثق';
  const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00Z` : value);
  if (Number.isNaN(date.getTime())) return 'تاريخ غير موثق';
  return new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'Asia/Riyadh' }).format(date);
}

function Notice({ error, children }) {
  if (!error && !children) return null;
  return <div className={`sb-notice${error ? ' sb-error' : ''}`} role={error ? 'alert' : 'status'}>{error || children}</div>;
}

function Loading({ loading, error, retry }) {
  if (error) return <><Notice error={error} /><button className="sb-secondary" type="button" onClick={retry}>إعادة التحميل</button></>;
  return loading ? <p className="sb-status" role="status">جارٍ تحميل البيانات…</p> : null;
}

function Field({ label, children, hint }) {
  return <label className="sb-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

function BalanceCard({ resource, label = 'الرصيد الحالي' }) {
  const balance = resource.balance;
  const visible = resource.access?.balance === true && balance && balance.visible !== false;
  const account = isMoneyAccount(resource.kind);
  return <section className="sb-balance" aria-label={label}>
    <div><span>{label}</span>{visible && <span className="sb-side" data-side={balance.side === 'balanced' ? undefined : balance.side}>{account ? 'ريال' : balance.side === 'for_party' ? 'له' : balance.side === 'for_us' ? 'عليه' : 'متوازن'}</span>}</div>
    {visible ? <><strong className="sb-balance-value"><bdi>{account && balance.side === 'for_party' && minorUnits(balance.amount) > 0n ? '−' : ''}{money(balance.amount)}</bdi><small>{resource.currency && resource.currency !== 'SAR' ? resource.currency : 'ريال'}</small></strong><p>الرصيد الكامل للحساب</p></>
      : <p>عرض رصيد هذا الحساب غير متاح ضمن صلاحياتك.</p>}
  </section>;
}

function Pages({ page, total, totalPages, onPage, busy = false, noun = 'حركة' }) {
  if (!total) return null;
  const pages = totalPages || Math.ceil(total / BALANCE_PAGE_SIZE);
  const choices = [...new Set(pages <= 3 ? Array.from({ length: pages }, (_, index) => index + 1) : [1, page === 1 ? 2 : page === pages ? pages - 1 : page, pages])].sort((a, b) => a - b);
  return <div className="sb-pagination-wrap"><nav className="sb-pagination" aria-label="صفحات السجل">
    <button type="button" disabled={busy || page <= 1} onClick={() => onPage(page - 1)}>السابق</button>
    <div>{choices.map((number, index) => <React.Fragment key={number}>{index > 0 && number > choices[index - 1] + 1 && <span>…</span>}<button type="button" disabled={busy} aria-current={number === page ? 'page' : undefined} aria-label={`الصفحة ${number}`} onClick={() => onPage(number)}>{number}</button></React.Fragment>)}</div>
    <button type="button" disabled={busy || page >= pages} onClick={() => onPage(page + 1)}>التالي</button>
  </nav><p>{(page - 1) * BALANCE_PAGE_SIZE + 1}–{Math.min(page * BALANCE_PAGE_SIZE, total)} من {total} {noun}</p></div>;
}

function MovementDetails({ row, onBack }) {
  const details = [
    ['البيان', row.description], ['المبلغ', `${money(row.amount)} ${row.currency && row.currency !== 'SAR' ? row.currency : 'ريال'}`],
    ['التاريخ', dateLabel(row.business_date)], ['سجّلها', row.recorded_by?.name || 'غير موثق'],
    ['الحساب', row.party_name], ['المصدر', row.source_label], ['رقم الطلب', row.order_number],
    ['سبب المبلغ', row.reason_label || reasonLabels[row.reason]],
  ].filter(([, value]) => value);
  return <section className="sb-details"><button type="button" className="sb-text" onClick={onBack}>العودة إلى الحركات</button><h2>تفاصيل الحركة</h2><dl>{details.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></section>;
}

function Ledger({ result, resource, focusId, onPage }) {
  const [detail, setDetail] = useState(null);
  useEffect(() => setDetail(null), [result]);
  if (detail) return <MovementDetails row={detail} onBack={() => setDetail(null)} />;
  if (!result?.items?.length) return <div className="sb-empty"><h2>لا توجد حركات في هذا السجل</h2><p>ستظهر هنا الحركات المتاحة لك.</p></div>;
  const account = resource && isMoneyAccount(resource.kind);
  return <section className="sb-ledger-section"><div className="sb-section-title"><h2>الحركات المالية</h2><span>{resource ? `المبالغ ${resource.currency && resource.currency !== 'SAR' ? resource.currency : 'بالريال'}` : 'العملة موضحة لكل حركة'}</span></div>
    <div className="sb-table-wrap"><table className="sb-ledger"><thead><tr><th scope="col">البيان</th>{resource ? <><th scope="col" data-side={account ? 'for_us' : 'for_party'}>{account ? 'وارد' : 'له'}</th><th scope="col" data-side={account ? 'for_party' : 'for_us'}>{account ? 'صادر' : 'عليه'}</th></> : <><th scope="col">المبلغ</th><th scope="col">الحساب</th></>}</tr></thead>
      <tbody>{result.items.map(row => {
        const firstSide = account ? 'for_us' : 'for_party';
        return <tr key={row.id} className={row.id === focusId ? 'sb-new-row' : undefined} data-testid="simple-movement-row"><td><button type="button" className="sb-row-button" onClick={() => setDetail(row)}><strong>{row.description || 'حركة مالية'}{row.id === focusId && <span className="sb-new-label">جديدة</span>}</strong>{row.order_number && <span>طلب {row.order_number}</span>}<span>{dateLabel(row.business_date)}</span><small>سجّلها: {row.recorded_by?.name || 'غير موثق'}</small></button></td>
          {resource ? <><td data-side={row.side === firstSide ? row.side : undefined}>{row.side === firstSide ? money(row.amount) : '—'}</td><td data-side={row.side !== firstSide && ['for_party', 'for_us'].includes(row.side) ? row.side : undefined}>{row.side !== firstSide && ['for_party', 'for_us'].includes(row.side) ? money(row.amount) : '—'}</td></> : <><td data-side={row.side}>{money(row.amount)}<small className="sb-currency">{row.currency || 'SAR'}</small></td><td>{row.party_name || row.source_label || '—'}</td></>}
        </tr>;
      })}</tbody></table></div>
    <Pages page={result.page} total={result.total} totalPages={result.total_pages} onPage={onPage} />
  </section>;
}

function ResourceList({ context, tab, identity, onOpen, initialKind = '' }) {
  const [kind, setKind] = useState(initialKind);
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const account = tab === 'accounts';
  const kinds = [...new Set(context.resources.filter(resource => isMoneyAccount(resource.kind) === account).map(resource => resource.kind))];
  const key = JSON.stringify([identity, context.session_scope, context.permission_version, tab, kind, query]);
  const read = useBalanceRead(async config => {
    const value = await api.parties({ ...(kind ? { kind } : {}), ...(query ? { q: query } : {}) }, config);
    if (!Array.isArray(value?.items)) throw new Error('invalid resource list');
    return value.items.filter(resource => isMoneyAccount(resource.kind) === account);
  }, key);
  const items = read.data || [];
  const currentPage = Math.max(1, Math.min(page, Math.max(1, Math.ceil(items.length / BALANCE_PAGE_SIZE))));
  return <><div className="sb-title"><h1>{account ? 'الحسابات' : 'الأطراف'}</h1><p>{account ? 'البنوك والصناديق المتاحة لك' : 'اختر الاسم لعرض حسابه وحركاته'}</p></div>
    <form className="sb-search" onSubmit={event => { event.preventDefault(); setQuery(draft.trim()); setPage(1); }}><Field label="بحث بالاسم"><input value={draft} onChange={event => setDraft(event.target.value)} placeholder={account ? 'اسم البنك أو الصندوق' : 'اسم الموظف أو الجهة'} /></Field><button type="submit" className="sb-secondary">بحث</button></form>
    {kinds.length > 1 && <Field label={account ? 'نوع الحساب' : 'نوع الطرف'}><select value={kind} onChange={event => { setKind(event.target.value); setPage(1); }}><option value="">{account ? 'كل الحسابات' : 'جميع الأطراف'}</option>{kinds.map(item => <option key={item} value={item}>{resourceLabels[item] || item}</option>)}</select></Field>}
    <Loading {...read} retry={read.reload} />
    {read.data && <><div className="sb-resource-list">{items.slice((currentPage - 1) * BALANCE_PAGE_SIZE, currentPage * BALANCE_PAGE_SIZE).map(resource => <button type="button" className="sb-resource" key={resourceKey(resource)} onClick={() => onOpen(resource)}><span className="sb-avatar" aria-hidden="true">{resource.name.slice(0, 1)}</span><span><strong>{resource.name}</strong><small>{resourceLabels[resource.kind] || 'حساب'}</small></span><span className="sb-resource-balance" data-side={resource.access?.balance && !isMoneyAccount(resource.kind) ? resource.balance?.side : undefined}>{resource.access?.balance ? balanceText(resource.balance, isMoneyAccount(resource.kind), resource.currency) : 'عرض الحساب'}</span></button>)}</div>{!items.length && <div className="sb-empty">لا توجد نتائج ضمن الحسابات المتاحة لك.</div>}<Pages page={currentPage} total={items.length} totalPages={Math.ceil(items.length / BALANCE_PAGE_SIZE)} onPage={setPage} noun="حساب" /></>}
  </>;
}

function Summary({ resource, amount, side, choice, reason, employee, owner, expense, source }) {
  if (!decimalAmount(amount) || !side || !choice) return <section className="sb-summary"><h2>قبل الحفظ</h2><p>حدد الحركة وأدخل المبلغ لعرض أثرها.</p></section>;
  const next = !isMoneyAccount(resource.kind) ? afterBalance(resource.balance, amount, side) : null;
  const direct = resource.kind === 'external_person' && directReasons.has(reason);
  return <section className="sb-summary" aria-live="polite"><h2>أثر الحركة</h2>
    <p>{choice.label}: <strong>{money(amount)} ريال</strong>.</p>
    {direct ? <><p>يُدفع المبلغ مباشرة من حساب {resource.name}{employee ? ` إلى ${employee.name}` : ' للمستفيد'}. بنوك المؤسسة وصناديقها لا تتحرك.</p>{owner && <p>يظهر المبلغ في سحوبات {owner.name}.</p>}{expense && <p>يُسجّل ضمن {expense.name} على حساب المؤسسة.</p>}</>
      : source ? <p>{side === 'for_party' && !isMoneyAccount(resource.kind) ? 'الإيداع في' : 'الدفع من'}: {source.name}.{source.kind === 'employee_custody' && ' تُخفض العهدة دون خصم بنكي جديد.'}</p>
        : !['accrual', 'adjustment'].includes(reason) ? <p>اختر مصدر الحركة.</p> : <p>لا يتغير رصيد البنك أو الصندوق.</p>}
    {next && resource.access?.balance && <div className="sb-summary-result"><span>رصيد {resource.name} بعد الحفظ</span><strong data-side={next.side}>{balanceText(next)}</strong></div>}
    {employee?.access?.balance && employee.balance && <div className="sb-summary-result"><span>رصيد {employee.name} بعد الحفظ</span><strong>{balanceText(afterBalance(employee.balance, amount, 'for_us'))}</strong></div>}
  </section>;
}

function restoreCommand(key, scope, resource) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return { command: null };
    const command = JSON.parse(raw);
    if (!command?.request_id || command.expected_session_scope !== scope || command.party_type !== resource.kind || command.party_id !== resource.id) throw new Error('invalid saved command');
    return { command };
  } catch {
    return { command: null, error: 'تعذر قراءة العملية المحفوظة. لا تُدخل عملية أخرى قبل التحقق من سجل الحساب.' };
  }
}

function EntryForm({ context, resource, onCancel, onSaved }) {
  const scope = context.session_scope;
  const storageKey = `mezan.simple-balances.entry.v1:${context.actor.id}:${scope}:${resourceKey(resource)}`;
  const [restored] = useState(() => restoreCommand(storageKey, scope, resource));
  const [pending, setPending] = useState(restored.command);
  const [form, setForm] = useState(() => ({
    side: restored.command?.side || (isMoneyAccount(resource.kind) ? 'for_us' : ''),
    reason: restored.command?.reason?.startsWith('charity_') ? 'charity' : restored.command?.reason || (isMoneyAccount(resource.kind) ? 'transfer' : ''),
    amount: restored.command?.amount || '', business_date: restored.command?.business_date || todaySA(),
    note: restored.command?.note || '', source: restored.command?.source_id ? JSON.stringify([restored.command.source_type, restored.command.source_id]) : '',
    employee_id: restored.command?.employee_id || '', owner_id: restored.command?.owner_id || '', expense_id: restored.command?.expense_id || '',
    charity_account: restored.command?.reason === 'charity_company' ? 'company' : restored.command?.reason === 'charity_owner' ? 'owner' : '',
  }));
  const [error, setError] = useState(restored.error || '');
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const operations = permittedOperations(context, resource);
  const globalOperations = permittedOperations(context);
  const external = resource.kind === 'external_person';
  const account = isMoneyAccount(resource.kind);
  const choices = entryChoices(resource.kind, form.side, operations);
  const implicit = account || external && form.side === 'for_us';
  const choice = implicit ? choices[0] : choices.find(item => item.reason === form.reason);
  const reason = choice?.reason ?? '';
  const needsOwner = external && (['household', 'personal'].includes(reason) || reason === 'charity' && form.charity_account === 'owner');
  const needsExpense = external && reason === 'charity' && form.charity_account === 'company';
  const direct = external && directReasons.has(reason);
  const needsSource = Boolean(choice) && !direct && !['accrual', 'adjustment'].includes(reason);
  const available = context.resources.filter(item => item.access?.use === true && (item.currency || 'SAR') === 'SAR');
  const employees = available.filter(item => item.kind === 'employee');
  const owners = available.filter(item => item.kind === 'owner_withdrawal');
  const expenses = available.filter(item => item.kind === 'operating_expense');
  const sources = available.filter(item => (item.currency || 'SAR') === 'SAR' && (isMoneyAccount(item.kind) || item.kind === 'employee_custody' && globalOperations.has('custody.spend') && form.side === 'for_us' && ['external_person', 'supplier', 'operating_expense'].includes(resource.kind)) && resourceKey(item) !== resourceKey(resource));
  const source = sources.find(item => resourceKey(item) === form.source);
  const employee = employees.find(item => item.id === form.employee_id);
  const owner = owners.find(item => item.id === form.owner_id);
  const expense = expenses.find(item => item.id === form.expense_id);
  const canWrite = context.status === 'active' && resource.access?.use === true && operations.size > 0;
  const disabled = busy || Boolean(pending) || Boolean(restored.error) || !canWrite;
  const change = patch => { if (!disabled) { setForm(value => ({ ...value, ...patch })); setError(''); } };
  const resetConditional = { source: '', employee_id: '', owner_id: '', expense_id: '', charity_account: '' };
  const chooseSide = side => change({ ...resetConditional, side, reason: '' });

  async function save(event) {
    event.preventDefault();
    if (lock.current || !canWrite || restored.error) return;
    lock.current = true; setBusy(true); setError('');
    try {
      let command = pending;
      if (!command) {
        const amount = decimalAmount(form.amount);
        if (!choice || !form.side) throw { localMessage: 'حدد له أو عليه واختر سبب المبلغ عند ظهوره.' };
        if (!amount) throw { localMessage: 'أدخل مبلغًا صحيحًا أكبر من صفر، وبحد أقصى منزلتين عشريتين.' };
        if (!/^\d{4}-\d{2}-\d{2}$/.test(form.business_date) || form.business_date > todaySA()) throw { localMessage: 'اختر تاريخًا صحيحًا حتى اليوم.' };
        if (needsSource && !source) throw { localMessage: 'اختر البنك أو الصندوق أو العهدة المتاحة لهذه الحركة.' };
        if (reason === 'adjustment' && !form.note.trim()) throw { localMessage: 'اكتب سبب الخصم لتوثيق الحركة.' };
        if (reason === 'salary_settlement') {
          if (!employee) throw { localMessage: 'اختر الموظف صاحب المستحق.' };
          if (employee.access?.balance && employee.balance) {
            const due = employee.balance.side === 'for_party' ? minorUnits(employee.balance.amount) : 0n;
            if (due === 0n) throw { localMessage: 'لا توجد مستحقات حالية لهذا الموظف يمكن تسويتها.' };
            if (due !== null && minorUnits(amount) > due) throw { localMessage: 'المبلغ أكبر من مستحق الموظف الحالي.' };
          }
        }
        if (reason === 'charity' && !['company', 'owner'].includes(form.charity_account)) throw { localMessage: 'اختر هل الصدقات على حساب المؤسسة أم المالك.' };
        if (needsOwner && !owner) throw { localMessage: 'اختر حساب المالك المتاح لك.' };
        if (needsExpense && !expense) throw { localMessage: 'اختر حساب الصدقات ضمن المصروفات المتاحة لك.' };
        command = {
          request_id: globalThis.crypto.randomUUID(), expected_session_scope: scope,
          party_type: resource.kind, party_id: resource.id, side: form.side,
          amount, business_date: form.business_date, note: form.note.trim(),
          ...(reason ? { reason: reason === 'charity' ? `charity_${form.charity_account}` : reason } : {}),
          ...(needsSource ? { source_type: source.kind, source_id: source.id } : {}),
          ...(reason === 'salary_settlement' ? { employee_id: employee.id } : {}),
          ...(needsOwner ? { owner_id: owner.id } : {}), ...(needsExpense ? { expense_id: expense.id } : {}),
        };
        try { localStorage.setItem(storageKey, JSON.stringify(command)); }
        catch { throw { localMessage: 'تعذر حفظ مرجع العملية محليًا لإعادة المحاولة الآمنة. تحقق من إعدادات التخزين ثم أعد المحاولة.' }; }
        setPending(command);
      }
      const result = await api.entry(command);
      if (result?.saved !== true || !result.movement_id || result.party_type !== resource.kind || result.party_id !== resource.id) throw { localMessage: 'لم تتأكد نتيجة الحفظ. أعد محاولة العملية نفسها للتحقق دون تكرارها.' };
      localStorage.removeItem(storageKey);
      if (mounted.current) { setPending(null); onSaved(result); }
    } catch (failure) {
      const detail = failure?.response?.data?.detail;
      if (detail?.not_applied === true || [400, 422].includes(failure?.response?.status)) {
        localStorage.removeItem(storageKey);
        if (mounted.current) setPending(null);
      }
      if (mounted.current) setError(simpleBalanceError(failure));
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  return <section><button type="button" className="sb-text" disabled={busy} onClick={onCancel}>العودة إلى الحساب</button><div className="sb-title"><h1>{account ? 'تحويل إلى هذا الحساب' : 'إضافة مبلغ'}</h1><p>{resource.name}</p></div><BalanceCard resource={resource} label="الرصيد قبل الحركة" />
    {!canWrite && <Notice error="إضافة الحركات غير متاحة لهذا الحساب في حالة النظام أو الصلاحيات الحالية." />}
    <form className="sb-entry" onSubmit={save} noValidate>
      <Notice error={error} />
      {pending && <Notice>هناك عملية بانتظار تأكيد النتيجة. إعادة المحاولة تستخدم العملية نفسها دون إنشاء قيد جديد.</Notice>}
      {!account && <fieldset className="sb-direction" disabled={disabled}><legend>المبلغ يسجل</legend><div>{[['for_party', 'له', external ? 'أخذت منه مبلغًا أو دفع عنك' : 'يزيد ما له أو يقلل ما عليه'], ['for_us', 'عليه', external ? 'سلّمت له مبلغًا' : 'يقلل ما له أو يزيد ما عليه']].map(([side, label, help]) => <button type="button" key={side} data-side={side} aria-pressed={form.side === side} disabled={disabled || !entryChoices(resource.kind, side, operations).length} onClick={() => chooseSide(side)}><strong>{label}</strong><span>{help}</span></button>)}</div></fieldset>}
      {!implicit && form.side && <Field label="سبب المبلغ"><select value={form.reason} disabled={disabled} onChange={event => change({ ...resetConditional, reason: event.target.value })} required><option value="">اختر سبب المبلغ</option>{choices.map(item => <option key={item.reason} value={item.reason}>{item.label}</option>)}</select></Field>}
      <div className="sb-form-two"><Field label="المبلغ بالريال"><input inputMode="decimal" dir="ltr" value={form.amount} disabled={disabled} onChange={event => change({ amount: event.target.value })} placeholder="0.00" required /></Field><Field label="التاريخ"><input type="date" value={form.business_date} max={todaySA()} disabled={disabled} onChange={event => change({ business_date: event.target.value })} required /></Field></div>
      {needsSource && <Field label={form.side === 'for_party' && !account ? 'الإيداع في' : 'الدفع من'}><select value={form.source} disabled={disabled} onChange={event => change({ source: event.target.value })} required><option value="">اختر الحساب</option>{sources.map(item => <option key={resourceKey(item)} value={resourceKey(item)}>{item.kind === 'employee_custody' ? 'عهدة · ' : ''}{item.name}{item.kind === 'employee_custody' && item.access?.balance && item.balance ? ` · المتبقي ${item.balance.side === 'for_us' ? money(item.balance.amount) : '0'} ريال` : ''}</option>)}</select>{!sources.length && <small>لا توجد مصادر مسموحة لهذه العملية. يحددها مسؤول الصلاحيات.</small>}</Field>}
      {reason === 'salary_settlement' && <Field label="الموظف"><select value={form.employee_id} disabled={disabled} onChange={event => change({ employee_id: event.target.value })} required><option value="">اختر الموظف</option>{employees.map(item => <option key={item.id} value={item.id}>{item.name}{item.access?.balance && item.balance ? ` · المستحق ${item.balance.side === 'for_party' ? money(item.balance.amount) : '0'} ريال` : ''}</option>)}</select></Field>}
      {reason === 'charity' && <Field label="الصدقات على حساب"><select value={form.charity_account} disabled={disabled} onChange={event => change({ charity_account: event.target.value, owner_id: '', expense_id: '' })} required><option value="">اختر صاحب المصروف</option>{operations.has('external.charity_company') && <option value="company">المؤسسة</option>}{operations.has('external.charity_owner') && <option value="owner">المالك</option>}</select></Field>}
      {needsOwner && <Field label="حساب المالك"><select value={form.owner_id} disabled={disabled} onChange={event => change({ owner_id: event.target.value })} required><option value="">اختر حساب المالك</option>{owners.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>}
      {needsExpense && <Field label="حساب الصدقات"><select value={form.expense_id} disabled={disabled} onChange={event => change({ expense_id: event.target.value })} required><option value="">اختر حساب المصروف</option>{expenses.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>}
      <Field label={reason === 'adjustment' ? 'سبب الخصم' : 'ملاحظة (اختياري)'}><textarea rows="2" maxLength="1000" value={form.note} disabled={disabled} required={reason === 'adjustment'} onChange={event => change({ note: event.target.value })} /></Field>
      <Summary resource={resource} amount={form.amount} side={form.side} choice={choice} reason={reason} employee={employee} owner={owner} expense={expense} source={source} />
      <button className="sb-primary sb-submit" type="submit" disabled={busy || Boolean(restored.error) || !canWrite}>{busy ? 'جارٍ تأكيد الحفظ…' : pending ? 'إعادة محاولة العملية نفسها' : 'حفظ والعودة للحركات'}</button>
    </form>
  </section>;
}

function Workspace({ context, identity, reloadContext }) {
  const [params, setParams] = useSearchParams();
  const [sections, setSections] = useState(false);
  const [notice, setNotice] = useState(params.get('saved') === '1' ? 'تم حفظ الحركة. تظهر الآن في حسابها مع اسم من سجّلها.' : '');
  const tab = ['movements', 'parties', 'accounts'].includes(params.get('tab')) ? params.get('tab') : 'movements';
  const kind = params.get('kind'), id = params.get('id');
  const requestedPage = Math.max(1, Number.parseInt(params.get('page') || '1', 10) || 1);
  const focusId = params.get('focus_id') || '';
  const adding = params.get('action') === 'add';
  const refunds = params.get('panel') === 'refunds';
  const selected = context.resources.find(resource => resource.kind === kind && resource.id === id);
  const queryKey = JSON.stringify([identity, context.session_scope, context.permission_version, tab, kind, id, requestedPage, focusId, adding]);
  const read = useBalanceRead(async config => {
    const query = { page: requestedPage, page_size: BALANCE_PAGE_SIZE, ...(focusId ? { focus_id: focusId } : {}) };
    const result = selected ? await api.ledger(selected.kind, selected.id, query, config) : await api.movements(query, config);
    if (!isPage(result, requestedPage, Boolean(focusId))) throw new Error('invalid ledger page');
    if (selected && (result.resource?.id !== selected.id || result.resource?.kind !== selected.kind)) throw new Error('wrong ledger resource');
    return result;
  }, queryKey, !refunds && !adding && (Boolean(selected) ? selected.access?.movements !== 'none' : !id && tab === 'movements'));
  const resource = read.data?.resource || selected;
  const operations = permittedOperations(context, resource);
  const canAdd = resource && resource.access?.use === true && context.status === 'active'
    && (entryChoices(resource.kind, 'for_party', operations).length > 0 || entryChoices(resource.kind, 'for_us', operations).length > 0);
  const go = values => { setSections(false); setParams(Object.fromEntries(Object.entries(values).filter(([, value]) => value != null && value !== ''))); };
  const open = item => { setNotice(''); go({ tab: isMoneyAccount(item.kind) ? 'accounts' : 'parties', kind: item.kind, id: item.id }); };
  const page = value => go({ tab, kind, id, page: value });
  const saved = result => {
    setNotice('تم حفظ الحركة. تظهر الآن في حسابها مع اسم من سجّلها.');
    go({ tab: isMoneyAccount(result.party_type) ? 'accounts' : 'parties', kind: result.party_type, id: result.party_id, focus_id: result.movement_id, saved: '1' });
    reloadContext();
  };
  const granted = new Set(context.operations.map(operation => typeof operation === 'string' ? operation : operation.key));
  const canRefund = granted.has('customer.refund_request') || granted.has('customer.refund_execute');
  const openRefunds = () => { setNotice(''); go({ tab: 'parties', panel: 'refunds' }); };
  return <main className="sb-workspace" dir="rtl" aria-label="الأرصدة والحركات المالية">
    <header className="sb-header"><div><span>ميزان</span><h1>الحركات المالية</h1></div><button type="button" className="sb-menu" aria-expanded={sections} aria-label="جميع الأقسام" onClick={() => setSections(value => !value)}>الأقسام</button></header>
    {sections ? <section className="sb-content"><h2>جميع الأقسام</h2><div className="sb-section-links">{[...new Set(context.resources.map(item => item.kind))].filter(item => !isMoneyAccount(item)).map(item => <button type="button" key={item} onClick={() => go({ tab: 'parties', filter: item })}>{resourceLabels[item] || item}</button>)}{canRefund && <button type="button" onClick={openRefunds}><strong>الطلبات ورد المبالغ</strong><span>ابحث برقم الطلب، ثم سجّل استرجاعًا جزئيًا أو كامل المتاح.</span></button>}{context.actor.is_owner && operationGroups.filter(item => item.owner || item.keys.some(key => granted.has(key))).map(item => <Link key={item.to} to={item.to}><strong>{item.title}</strong><span>{item.text}</span></Link>)}{context.capabilities?.manage_access === true && <Link to="/operational-balances/access"><strong>صلاحيات الموظفين</strong><span>الحسابات المتاحة لكل موظف والعمليات المسموحة له.</span></Link>}</div></section>
      : <div className="sb-content"><Notice>{notice}</Notice>{context.status !== 'active' && <Notice>{context.status === 'draft' ? 'النظام في مرحلة الأرصدة الافتتاحية. تسجيل الحركات يبدأ بعد اعتماد البداية.' : 'النظام متاح للمراجعة، وتسجيل حركات جديدة متوقف حاليًا.'}</Notice>}
        {refunds ? <SimpleOrderRefunds context={context} identity={identity} onBalancesChanged={reloadContext} onExit={() => go({ tab: 'parties' })} />
          : id && !selected ? <Notice error="هذا الحساب غير متاح ضمن صلاحياتك الحالية." />
          : selected && [403, 404].includes(read.status) ? <><Notice error={read.error} /><button type="button" className="sb-secondary" onClick={reloadContext}>تحديث الصلاحيات</button></>
          : adding && selected ? <EntryForm key={`${context.session_scope}:${resourceKey(selected)}`} context={context} resource={selected} onCancel={() => open(selected)} onSaved={saved} />
            : selected ? <><button type="button" className="sb-text" onClick={() => { setNotice(''); go({ tab }); }}>العودة إلى {tab === 'accounts' ? 'الحسابات' : 'الأطراف'}</button><div className="sb-identity"><span className="sb-avatar" aria-hidden="true">{resource.name.slice(0, 1)}</span><div><p>{resourceLabels[resource.kind] || 'حساب'}</p><h1>{resource.name}</h1></div></div><BalanceCard resource={resource} />{canAdd && <button type="button" className="sb-primary" onClick={() => go({ tab, kind, id, action: 'add' })}>{isMoneyAccount(resource.kind) ? 'تحويل إلى هذا الحساب' : '＋ إضافة مبلغ'}</button>}{resource.access?.movements === 'own' && <p className="sb-small-note">يعرض السجل الحركات التي سجّلتها أنت فقط.</p>}{resource.access?.movements === 'none' ? <p className="sb-small-note">عرض حركات هذا الحساب غير متاح ضمن صلاحياتك.</p> : <><Loading {...read} retry={read.reload} />{read.data && <Ledger result={read.data} resource={resource} focusId={focusId} onPage={page} />}</>}</>
              : tab === 'movements' ? <><div className="sb-title"><h1>الحركات اليومية</h1><p>الحركات المتاحة لك، مع اسم الموظف الذي سجّلها.</p></div><div className="sb-actions">{context.capabilities?.create_entries === true && <button type="button" className="sb-primary" onClick={() => go({ tab: 'parties' })}>إضافة مبلغ إلى حساب</button>}<button type="button" className="sb-secondary" onClick={read.reload}>تحديث السجل</button></div><Loading {...read} retry={read.reload} />{read.data && <Ledger result={read.data} focusId={focusId} onPage={page} />}</>
                : <>{tab === 'parties' && canRefund && <button type="button" className="sb-secondary" onClick={openRefunds}>بحث برقم الطلب</button>}<ResourceList key={`${tab}:${params.get('filter') || ''}`} context={context} tab={tab} identity={identity} onOpen={open} initialKind={params.get('filter') || ''} /></>}
      </div>}
    <nav className="sb-tabs" aria-label="صفحات الأرصدة">{[['movements', 'الحركات'], ['parties', 'الأطراف'], ['accounts', 'الحسابات']].map(([value, label]) => <button type="button" key={value} aria-current={!sections && tab === value ? 'page' : undefined} onClick={() => { setNotice(''); go({ tab: value }); }}>{label}</button>)}</nav>
  </main>;
}

export default function SimpleBalances() {
  const auth = useOptionalAuth();
  const identity = authIdentity(auth);
  const enabled = !auth || !auth.loading && Boolean(auth.user?.id);
  const read = useBalanceRead(async config => {
    const value = await api.context(config);
    if (!validContext(value)) throw new Error('invalid workspace context');
    if (auth?.user?.id && value.actor.id !== auth.user.id) throw new Error('wrong workspace actor');
    return value;
  }, identity, enabled);
  if (auth?.loading) return <main className="sb-workspace sb-content" dir="rtl" role="status">جارٍ التحقق من الحساب…</main>;
  if (auth && !auth.user) return <main className="sb-workspace sb-content" dir="rtl"><Notice error="سجّل الدخول لعرض الحسابات المتاحة لك." /></main>;
  if (!read.data) return <main className="sb-workspace sb-content" dir="rtl"><h1>الحركات المالية</h1><Loading loading={!read.error} error={read.error} retry={read.reload} />{auth?.user?.is_owner && <Link className="sb-text" to="/operational-balances/access">صلاحيات الموظفين</Link>}</main>;
  return <Workspace key={`${identity}:${read.data.session_scope}`} context={read.data} identity={identity} reloadContext={read.reload} />;
}

export { BalanceCard, EntryForm, Ledger, Pages, routeBase };
