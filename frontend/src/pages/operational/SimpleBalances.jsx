import React, { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import { todaySA } from '../../lib/dates';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { authIdentity, useBalanceRead } from './simpleBalancesHooks';
import { sameWorkspace, useWorkspaceRead } from './workspaceReadHooks';
import { useVisibleBalanceFreshness } from './useVisibleBalanceFreshness';
import BalanceFreshnessNotice from './BalanceFreshnessNotice';
import WorkspaceResourcePicker from './WorkspaceResourcePicker';
import { usePendingBalanceCommand } from './simpleBalancesPending';
import PendingEntryActions from './PendingEntryActions';
import PaymentSourceFields, { exceedsSource, insufficientSource, sourceAvailability, usePaymentSourceGroup } from './PaymentSourceFields';
import SimpleOrderRefunds from './SimpleOrderRefunds';
import SimpleBalanceStatement, { SignedAmount } from './SimpleBalanceStatement';
import SimpleBalanceReviews from './SimpleBalanceReviews';
import {
  BALANCE_PAGE_SIZE, afterBalance, balanceText, decimalAmount, entryChoices, isMoneyAccount,
  isPage, minorUnits, money, permittedOperations, resourceKey, resourceLabels, validContext,
  canReadStatement, movementDate, movementRecorder, movementTitle, signedResourceBalance,
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
  { title: 'الإعدادات والعمليات المتخصصة', text: 'الأرصدة الافتتاحية ومسارات المراجعة الحالية.', to: '/operational-balances?view=specialized', owner: true },
];

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
  const signed = signedResourceBalance(resource);
  const balance = resource.balance;
  return <section className="sb-balance" aria-label={label}>
    {signed !== null ? <><div><span>{label}</span><strong className="sb-balance-value"><SignedAmount value={signed} /><small>{resource.currency && resource.currency !== 'SAR' ? resource.currency : 'ريال'} {!isMoneyAccount(resource.kind) && (balance.side === 'for_party' ? 'له' : balance.side === 'for_us' ? 'عليه' : '')}</small></strong></div><p>الرصيد الكامل للحساب</p></>
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

function MovementDetails({ row }) {
  const details = [
    ['البيان', row.description], ['المبلغ', `${money(row.amount)} ${row.currency && row.currency !== 'SAR' ? row.currency : 'ريال'}`],
    ['تاريخ القيد', row.business_date], ['وقت التسجيل', row.recorded_at ? movementDate({ recorded_at: row.recorded_at }, true) : null], ['سجّلها', movementRecorder(row)],
    ['الحساب', row.party_name], ['المصدر', row.source_label], ['رقم الطلب', row.order_number],
    ['سبب المبلغ', row.reason_label || reasonLabels[row.reason]], ['الملاحظة', typeof row.note === 'string' && row.note.trim() ? row.note : null],
  ].filter(([, value]) => value);
  return <section className="sb-details">{row.informational && <p className="sb-small-note">صف توثيقي؛ لا يُضاف إلى إجماليات له أو عليه.</p>}<dl>{details.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></section>;
}

function Ledger({ result, resource, focusId, onPage, onDetail, onHistory, onStatement, heading = true }) {
  const account = resource && isMoneyAccount(resource.kind);
  return <section className="sb-ledger-section">{heading && <div className="sb-section-title"><h2>الحركات المالية</h2>{onStatement ? <button className="sb-secondary sb-period-button" type="button" onClick={onStatement}>كشف فترة</button> : <span>{resource ? `المبالغ ${resource.currency && resource.currency !== 'SAR' ? resource.currency : 'بالريال'}` : 'العملة موضحة لكل حركة'}</span>}</div>}
    {typeof result?.balance_note === 'string' && result.balance_note && <p className="sb-small-note">{result.balance_note}</p>}
    {!result?.items?.length ? <div className="sb-empty"><h2>لا توجد حركات في هذا السجل</h2><p>ستظهر هنا الحركات المتاحة لك.</p></div> : <div className="sb-table-wrap"><table className="sb-ledger sb-with-running"><thead><tr><th scope="col">البيان</th>{resource ? <><th scope="col" data-side={account ? 'for_us' : 'for_party'}>{account ? 'وارد' : 'له'}</th><th scope="col" data-side={account ? 'for_party' : 'for_us'}>{account ? 'صادر' : 'عليه'}</th></> : <><th scope="col">المبلغ</th><th scope="col">الحساب</th></>}<th scope="col">الرصيد<small>بعد الحركة</small></th></tr></thead>
      <tbody>{result.items.map(row => {
        const firstSide = account ? 'for_us' : 'for_party';
        return <tr key={row.id} className={row.id === focusId ? 'sb-new-row' : undefined} data-testid="simple-movement-row"><td><button type="button" className="sb-row-button" onClick={() => row.automatic_history && onHistory ? onHistory(row) : onDetail?.(row)}><strong>{movementTitle(row, Boolean(onHistory))}{row.id === focusId && <span className="sb-new-label">جديدة</span>}</strong>{row.customer_name && row.order_number && <span>طلب {row.order_number}</span>}<span><bdi>{movementDate(row)}</bdi></span>{row.informational && <small>{money(row.amount)} {row.currency || 'SAR'} · إثبات للمؤسسة</small>}{row.automatic_history && onHistory ? <small className="sb-history-link">{row.automatic_history === 'salary' ? 'سجل الرواتب' : 'سجل التوصيلات'} ({row.history_count})</small> : <small>سجّلها: {movementRecorder(row)}</small>}</button></td>
          {resource ? <><td data-side={row.side === firstSide ? row.side : undefined}>{!row.informational && row.side === firstSide ? money(row.amount) : '—'}</td><td data-side={row.side !== firstSide && ['for_party', 'for_us'].includes(row.side) ? row.side : undefined}>{!row.informational && row.side !== firstSide && ['for_party', 'for_us'].includes(row.side) ? money(row.amount) : '—'}</td></> : <><td data-side={row.side}>{row.informational ? '—' : money(row.amount)}<small className="sb-currency">{row.currency || 'SAR'}</small></td><td>{row.party_name || row.source_label || '—'}</td></>}
          <td><SignedAmount kind={resource?.kind || row.party_type} value={row.balance_after} hidden={Boolean(resource && resource.access?.balance !== true)} />{row.informational && <small className="sb-small-note">دون تغيير</small>}</td>
        </tr>;
      })}</tbody></table></div>}
    <Pages page={result.page} total={result.total} totalPages={result.total_pages} onPage={onPage} />
  </section>;
}

function ResourceList({ context, tab, identity, onOpen, initialKind = '', initialQuery = '', initialPage = 1, onStateChange }) {
  const [kind, setKind] = useState(initialKind);
  const [draft, setDraft] = useState(initialQuery);
  const [query, setQuery] = useState(initialQuery);
  const [page, setPage] = useState(initialPage);
  const updatePage = value => { setPage(value); onStateChange?.({ filter: kind, q: query, page: value }); };
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
    <form className="sb-search" onSubmit={event => { event.preventDefault(); setQuery(draft.trim()); setPage(1); onStateChange?.({ filter: kind, q: draft.trim(), page: 1 }); }}><Field label="بحث بالاسم"><input value={draft} onChange={event => setDraft(event.target.value)} placeholder={account ? 'اسم البنك أو الصندوق' : 'اسم الموظف أو الجهة'} /></Field><button type="submit" className="sb-secondary">بحث</button></form>
    {kinds.length > 1 && <Field label={account ? 'نوع الحساب' : 'نوع الطرف'}><select value={kind} onChange={event => { setKind(event.target.value); setPage(1); onStateChange?.({ filter: event.target.value, q: query, page: 1 }); }}><option value="">{account ? 'كل الحسابات' : 'جميع الأطراف'}</option>{kinds.map(item => <option key={item} value={item}>{resourceLabels[item] || item}</option>)}</select></Field>}
    <Loading {...read} retry={read.reload} />
    {read.data && <><div className="sb-resource-list">{items.slice((currentPage - 1) * BALANCE_PAGE_SIZE, currentPage * BALANCE_PAGE_SIZE).map(resource => <button type="button" className="sb-resource" key={resourceKey(resource)} onClick={() => onOpen(resource)}><span className="sb-avatar" aria-hidden="true">{resource.name.slice(0, 1)}</span><span><strong>{resource.name}</strong><small>{resourceLabels[resource.kind] || 'حساب'}</small></span><span className="sb-resource-balance" data-side={resource.access?.balance && !isMoneyAccount(resource.kind) ? resource.balance?.side : undefined}>{resource.access?.balance ? balanceText(resource.balance, isMoneyAccount(resource.kind), resource.currency) : 'عرض الحساب'}</span></button>)}</div>{!items.length && <div className="sb-empty">لا توجد نتائج ضمن الحسابات المتاحة لك.</div>}<Pages page={currentPage} total={items.length} totalPages={Math.ceil(items.length / BALANCE_PAGE_SIZE)} onPage={updatePage} noun="حساب" /></>}
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

function EntryForm({ context, resource, onCancel, onSaved }) {
  const demand = context.demandOnly === true;
  const [picked, setPicked] = useState({});
  const scope = context.session_scope;
  const mutation = usePendingBalanceCommand({ context,
    storageIdentity: JSON.stringify(['entry', context.actor.id, scope, resource.kind, resource.id]),
    validate: value => value.action === 'entry' && value.target_id === null
      && value.command.party_type === resource.kind && value.command.party_id === resource.id,
    execute: value => api.entry(value.command),
    validateSaved: (result, pending) => Boolean(result.movement_id)
      && result.party_type === pending.command.party_type && result.party_id === pending.command.party_id,
    onSaved, onRetired: onCancel });
  const pending = mutation.pending?.command;
  const { error, busy } = mutation;
  const ad = resource.ad_bank_funding;
  const [quote, setQuote] = useState(null);
  const [form, setForm] = useState(() => ({ side: ad ? 'for_us' : isMoneyAccount(resource.kind) ? 'for_us' : resource.kind === 'pos_receivable' ? 'for_party' : '',
    adUnit: ad?.terms ? 'SAR' : 'receipt', adUsd: '', adFee: '', adWallet: false,
    reason: ad ? 'payment' : isMoneyAccount(resource.kind) ? 'transfer' : resource.kind === 'pos_receivable' ? 'collection' : '', reference: '', arrival: false, fee: '', feeReference: '', amount: '', business_date: todaySA(), note: '',
    source: '', sourceKind: '', employee_id: '', owner_id: '', expense_id: '', charity_account: '' }));
  useEffect(() => {
    if (!pending) return;
    setForm({ side: pending.side, reason: pending.reason?.startsWith('charity_') ? 'charity' : pending.reason || '',
      amount: pending.ad_bank_funding?.bank_amount || pending.amount, adUnit: 'receipt', adUsd: pending.amount,
      adFee: pending.ad_bank_funding?.bank_fee_amount || '', adWallet: pending.ad_bank_funding?.kind === 'wallet_funding', business_date: pending.business_date, note: pending.note || '',
      reference: pending.reference || '', arrival: pending.bank_arrival_confirmed === true,
      fee: pending.actual_fee_amount || '', feeReference: pending.fee_reference || '',
      sourceKind: pending.source_type || '', source: pending.source_id ? JSON.stringify([pending.source_type, pending.source_id]) : '',
      employee_id: pending.employee_id || '', owner_id: pending.owner_id || '', expense_id: pending.expense_id || '',
      charity_account: pending.reason === 'charity_company' ? 'company' : pending.reason === 'charity_owner' ? 'owner' : '' });
  }, [pending]);
  const operations = permittedOperations(context, resource);
  const globalOperations = permittedOperations(context);
  const external = resource.kind === 'external_person';
  const account = isMoneyAccount(resource.kind);
  const choices = entryChoices(resource.kind, form.side, operations);
  const pos = resource.kind === 'pos_receivable';
  const implicit = account || pos || Boolean(ad) || external && form.side === 'for_us';
  const choice = implicit ? choices[0] : choices.find(item => item.reason === form.reason);
  const reason = choice?.reason ?? '';
  const needsOwner = external && (['household', 'personal'].includes(reason) || reason === 'charity' && form.charity_account === 'owner');
  const needsExpense = external && reason === 'charity' && form.charity_account === 'company';
  const direct = external && directReasons.has(reason);
  const needsSource = Boolean(choice) && !direct && !['accrual', 'adjustment'].includes(reason);
  const pendingRefs = pending ? [[pending.source_type, pending.source_id], ['employee', pending.employee_id],
    ['owner_withdrawal', pending.owner_id], ['operating_expense', pending.expense_id]].filter(([kind,id])=>kind&&id) : [];
  const pendingRead = useWorkspaceRead(async config => {
    const items=[];
    for(const [kind,id] of pendingRefs){
      const item=await api.selection(kind,id,config);
      if(item?.kind!==kind||item.id!==id||!item.access||!sameWorkspace(item,context))throw new Error('Invalid pending command resource');
      items.push(item);
    }
    return items;
  },JSON.stringify([context.actor.id,scope,context.permission_version,pendingRefs,'entry-recovery']),demand&&pendingRefs.length>0);
  const available = (demand ? [...Object.values(picked),...(pendingRead.data||[])] : context.resources)
    .filter(item => item?.access?.use === true && (item.currency || 'SAR') === 'SAR');
  const employees = available.filter(item => item.kind === 'employee');
  const owners = available.filter(item => item.kind === 'owner_withdrawal');
  const expenses = available.filter(item => item.kind === 'operating_expense');
  const sources = available.filter(item => (!(pos || ad) || item.kind === 'bank') && (item.currency || 'SAR') === 'SAR' && (isMoneyAccount(item.kind) || item.kind === 'employee_custody' && globalOperations.has('custody.spend') && form.side === 'for_us' && ['external_person', 'supplier', 'operating_expense'].includes(resource.kind)) && resourceKey(item) !== resourceKey(resource));
  const payout = needsSource && form.side === 'for_us' && !account && !ad;
  const legacyGroup = usePaymentSourceGroup(context, sources, form.sourceKind, !demand && payout);
  const group = demand ? {rows:sources,onDemand:true} : legacyGroup;
  const source = group.rows.find(item => resourceKey(item) === form.source && (!payout || item.kind === form.sourceKind && !item.prepaid_shipping_cash));
  const courierBankArrival = resource.kind === 'courier' && form.side === 'for_party'
    && reason === 'collection' && source?.kind === 'bank';
  const bankArrival = pos || courierBankArrival;
  const employee = employees.find(item => item.id === form.employee_id);
  const owner = owners.find(item => item.id === form.owner_id);
  const expense = expenses.find(item => item.id === form.expense_id);
  const canWrite = context.status === 'active' && resource.access?.use === true && operations.size > 0;
  const disabled = busy || Boolean(pending) || mutation.recovering || mutation.blocked || !canWrite;
  const change = patch => { if (!disabled) { setForm(value => ({ ...value, ...patch })); mutation.clearError(); } };
  const resetConditional = { source: '', sourceKind: '', arrival: false, employee_id: '', owner_id: '', expense_id: '', charity_account: '' };
  const chooseSide = side => change({ ...resetConditional, side, reason: '' });
  const chooseResource = (field, item) => {
    setPicked(previous=>({...previous,[field]:item}));
    change({[field]:item ? field==='source'?resourceKey(item):item.id : '', ...(field === 'source' ? { arrival: false } : {})});
  };
  const optionOperation = reason==='charity' ? `external.charity_${form.charity_account}` : choice?.operation;
  const picker = (kind, field, value, label, role='recipient') => <WorkspaceResourcePicker
    key={JSON.stringify([optionOperation,kind,field,form.side])} context={context} operation={optionOperation} role={role} kind={kind}
    selectedResource={resource} value={value} label={label} disabled={disabled}
    optionDisabled={item=>field==='source'&&payout&&sourceAvailability(item)!==true}
    onSelect={item=>chooseResource(field,item)}/>;

  const quoteKey = JSON.stringify([resource.id, form.adUnit, form.amount, ad?.terms]);
  const shownQuote = quote?.key === quoteKey ? quote : null;
  async function calculateAd() {
    const amount = decimalAmount(form.amount);
    if (!amount) { setQuote({ key: quoteKey, error: 'أدخل مبلغًا صحيحًا أكبر من صفر' }); return; }
    try {
      const values = await api.quoteAd({ party_id: resource.id, currency: form.adUnit, amount });
      setQuote({ ...values, key: quoteKey });
    } catch (problem) { setQuote({ key: quoteKey, error: simpleBalanceError(problem) }); }
  }
  async function save(event) {
    event.preventDefault();
    if (!canWrite) return;
    await mutation.submit('entry', null, () => {
        const amount = decimalAmount(form.amount);
        if (!choice || !form.side) throw { localMessage: 'حدد له أو عليه واختر سبب المبلغ عند ظهوره.' };
        if (!amount) throw { localMessage: 'أدخل مبلغًا صحيحًا أكبر من صفر، وبحد أقصى منزلتين عشريتين.' };
        if (!/^\d{4}-\d{2}-\d{2}$/.test(form.business_date) || form.business_date > todaySA()) throw { localMessage: 'اختر تاريخًا صحيحًا حتى اليوم.' };
        if (needsSource && !source) throw { localMessage: 'اختر البنك أو الصندوق أو العهدة المتاحة لهذه الحركة.' };
        if (bankArrival && (!form.arrival || !form.reference.trim())) throw { localMessage: 'طابق وصول التحويل واكتب مرجعه.' };
        if (payout && ((group.onDemand && sourceAvailability(source) !== true) || exceedsSource(source, amount))) throw { localMessage: insufficientSource };
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
        let funding = null;
        let usd = null;
        if (ad) {
          const manual = form.adUnit === 'receipt';
          usd = decimalAmount(manual ? form.adUsd : shownQuote?.amount_usd);
          const gross = manual ? amount : decimalAmount(shownQuote?.gross_sar);
          const fee = minorUnits(manual ? form.adFee : shownQuote?.fee_sar);
          if (!usd || !gross || fee === null || fee < 0n || fee >= minorUnits(gross)) throw { localMessage: 'احسب المقابل أو أدخل مبالغ الإيصال الصحيحة قبل الحفظ.' };
          if (form.reference.trim().length < 3) throw { localMessage: 'أدخل مرجع العملية البنكية.' };
          funding = { bank_amount: gross, bank_fee_amount: `${fee / 100n}.${String(fee % 100n).padStart(2, '0')}`,
            kind: ad.mode === 'prepaid' || (ad.mode === 'hybrid' && !ad.wallet_first && form.adWallet) ? 'wallet_funding' : 'payment' };
        }
        return {
          party_type: resource.kind, party_id: resource.id, side: form.side,
          amount: usd || amount, ...(funding ? { ad_bank_funding: funding } : {}), business_date: form.business_date, note: form.note.trim(),
          ...(reason ? { reason: reason === 'charity' ? `charity_${form.charity_account}` : reason } : {}),
          ...((bankArrival || account || ad) && form.reference.trim() ? { reference: form.reference.trim() } : {}),
          ...(bankArrival && form.arrival ? { bank_arrival_confirmed: true } : {}),
          ...(pos && form.fee.trim() ? { actual_fee_amount: form.fee.trim(), fee_reference: form.feeReference.trim() } : {}),
          ...(needsSource ? { source_type: source.kind, source_id: source.id } : {}),
          ...(reason === 'salary_settlement' ? { employee_id: employee.id } : {}),
          ...(needsOwner ? { owner_id: owner.id } : {}), ...(needsExpense ? { expense_id: expense.id } : {}),
        };
    });
  }

  return <section><button type="button" className="sb-text" disabled={busy} onClick={onCancel}>العودة إلى الحساب</button><div className="sb-title"><h1>{pos ? 'موازنة الشبكة / تحويل إلى بنك' : account ? 'تحويل إلى هذا الحساب' : 'إضافة مبلغ'}</h1><p>{resource.name}</p></div><BalanceCard resource={resource} label="الرصيد قبل الحركة" />
    {!canWrite && <Notice error="إضافة الحركات غير متاحة لهذا الحساب في حالة النظام أو الصلاحيات الحالية." />}
    <form className="sb-entry" onSubmit={save} noValidate>
      <Notice error={error} />
      {mutation.notice && <Notice>{mutation.notice}</Notice>}
      {mutation.recovering && <Notice>جارٍ استعادة العملية السابقة من الخادم…</Notice>}
      {mutation.blocked && <button className="sb-secondary" type="button" onClick={mutation.recover} disabled={busy || mutation.recovering}>استعادة العملية السابقة</button>}
      <PendingEntryActions mutation={mutation} className="sb-secondary" />
      {pending && <Notice>هناك عملية بانتظار تأكيد النتيجة. إعادة المحاولة تستخدم العملية نفسها دون إنشاء قيد جديد.</Notice>}
      {!account && !pos && !ad && <fieldset className="sb-direction" disabled={disabled}><legend>المبلغ يسجل</legend><div>{[['for_party', 'له', external ? 'أخذت منه مبلغًا أو دفع عنك' : 'يزيد ما له أو يقلل ما عليه'], ['for_us', 'عليه', external ? 'سلّمت له مبلغًا' : 'يقلل ما له أو يزيد ما عليه']].map(([side, label, help]) => <button type="button" key={side} data-side={side} aria-pressed={form.side === side} disabled={disabled || !entryChoices(resource.kind, side, operations).length} onClick={() => chooseSide(side)}><strong>{label}</strong><span>{help}</span></button>)}</div></fieldset>}
      {!implicit && form.side && <Field label="سبب المبلغ"><select value={form.reason} disabled={disabled} onChange={event => change({ ...resetConditional, reason: event.target.value })} required><option value="">اختر سبب المبلغ</option>{choices.map(item => <option key={item.reason} value={item.reason}>{item.label}</option>)}</select></Field>}
      {ad && <Field label="طريقة إدخال الدفعة"><select disabled={disabled} value={form.adUnit} onChange={e => change({ adUnit: e.target.value, amount: '' })}>
        {ad.terms && <><option value="SAR">إجمالي الخصم بالريال</option><option value="USD">مبلغ الإعلان بالدولار</option></>}
        <option value="receipt">إدخال مبالغ الإيصال</option></select></Field>}
      <div className="sb-form-two"><Field label={ad ? form.adUnit === 'USD' ? 'مبلغ الإعلان بالدولار' : 'إجمالي الخصم بالريال شامل العمولة' : pos ? "صافي المبلغ الوارد إلى البنك بالريال" : "المبلغ بالريال"}><input inputMode="decimal" dir="ltr" value={form.amount} disabled={disabled} onChange={event => change({ amount: event.target.value })} placeholder="0.00" required /></Field><Field label="التاريخ"><input type="date" value={form.business_date} max={todaySA()} disabled={disabled} onChange={event => change({ business_date: event.target.value })} required /></Field></div>
      {ad && <>
        {form.adUnit === 'receipt' ? <div className="sb-form-two">
          <Field label="المبلغ الذي وصل للمنصة بالدولار"><input inputMode="decimal" value={form.adUsd} disabled={disabled} onChange={e => change({ adUsd: e.target.value })} /></Field>
          <Field label="عمولة البنك وضريبتها بالريال"><input inputMode="decimal" value={form.adFee} disabled={disabled} onChange={e => change({ adFee: e.target.value })} /></Field>
        </div> : <>
          <button className="sb-secondary" type="button" disabled={disabled || !form.amount} onClick={calculateAd}>احسب المقابل</button>
          {shownQuote?.error && <Notice error={shownQuote.error} />}
          {shownQuote?.amount_usd && <Notice>للمنصة {shownQuote.amount_usd} دولار. خصم البنك {shownQuote.gross_sar} ريال، منه {shownQuote.fee_sar} ريال عمولة وضريبتها. طابق المبالغ مع الإيصال قبل الحفظ.</Notice>}
        </>}
        {ad.mode === 'hybrid' && !ad.wallet_first && <Field label="نوع الدفعة"><select disabled={disabled} value={form.adWallet ? 'wallet' : 'payment'} onChange={e => change({ adWallet: e.target.value === 'wallet' })}><option value="payment">سداد المستحقات</option><option value="wallet">شحن المحفظة</option></select></Field>}
        <Field label="مرجع العملية البنكية"><input maxLength={160} value={form.reference} disabled={disabled} onChange={e => change({ reference: e.target.value })} /></Field>
      </>}
      {needsSource && demand && <>
        <Field label="نوع مصدر الدفع"><select value={form.sourceKind} required disabled={disabled} onChange={event=>change({sourceKind:event.target.value,source:'',arrival:false})}>
          <option value="">اختر نوع المصدر</option>{(pos||ad?['bank']:['bank','cash',...(payout&&globalOperations.has('custody.spend')&&['external_person','supplier','operating_expense'].includes(resource.kind)?['employee_custody']:[])])
            .map(kind=><option key={kind} value={kind}>{resourceLabels[kind]}</option>)}
        </select></Field>
        {form.sourceKind && picker(form.sourceKind,'source',source,form.side==='for_party'&&!account?'الإيداع في':'الدفع من','source')}
      </>}
      {payout && !demand && <PaymentSourceFields sources={sources} group={group} kind={form.sourceKind} value={source ? form.source : ''} disabled={disabled}
        label="الدفع من" fieldClass="sb-field" onKind={sourceKind => change({ sourceKind, source: '' })}
        onSource={sourceId => change({ source: sourceId })} />}
      {needsSource && !payout && !demand && <Field label={form.side === 'for_party' && !account ? 'الإيداع في' : 'الدفع من'}><select value={form.source} disabled={disabled} onChange={event => change({ source: event.target.value, arrival: false })} required><option value="">اختر الحساب</option>{sources.map(item => <option key={resourceKey(item)} value={resourceKey(item)}>{item.kind === 'employee_custody' ? 'عهدة · ' : ''}{item.name}{item.kind === 'employee_custody' && item.access?.balance && item.balance ? ` · المتبقي ${item.balance.side === 'for_us' ? money(item.balance.amount) : '0'} ريال` : ''}</option>)}</select>{!sources.length && <small>لا توجد مصادر مسموحة لهذه العملية. يحددها مسؤول الصلاحيات.</small>}</Field>}
      {(bankArrival || account) && <Field label="مرجع التحويل"><input maxLength={160} value={form.reference} required={bankArrival || source?.kind === 'bank' && resource.prepaid_shipping_cash === true} disabled={disabled} onChange={event => change({ reference: event.target.value })} /></Field>}
      {pos && <>
        <Field label="رسوم الشبكة الفعلية إن وجدت"><input inputMode="decimal" value={form.fee} disabled={disabled} onChange={event => change({ fee: event.target.value })} /></Field>
        {form.fee && <Field label="مرجع مستند الرسوم"><input required maxLength={160} value={form.feeReference} disabled={disabled} onChange={event => change({ feeReference: event.target.value })} /></Field>}
      </>}
      {bankArrival && <label><input type="checkbox" required checked={form.arrival} disabled={disabled} onChange={event => change({ arrival: event.target.checked })} /> طابقت وصول صافي التحويل مع حساب البنك المحدد</label>}
      {reason === 'salary_settlement' && (demand ? picker('employee','employee_id',employee,'الموظف') : <Field label="الموظف"><select value={form.employee_id} disabled={disabled} onChange={event => change({ employee_id: event.target.value })} required><option value="">اختر الموظف</option>{employees.map(item => <option key={item.id} value={item.id}>{item.name}{item.access?.balance && item.balance ? ` · المستحق ${item.balance.side === 'for_party' ? money(item.balance.amount) : '0'} ريال` : ''}</option>)}</select></Field>)}
      {reason === 'charity' && <Field label="الصدقات على حساب"><select value={form.charity_account} disabled={disabled} onChange={event => change({ charity_account: event.target.value, owner_id: '', expense_id: '' })} required><option value="">اختر صاحب المصروف</option>{operations.has('external.charity_company') && <option value="company">المؤسسة</option>}{operations.has('external.charity_owner') && <option value="owner">المالك</option>}</select></Field>}
      {needsOwner && (demand ? picker('owner_withdrawal','owner_id',owner,'حساب المالك') : <Field label="حساب المالك"><select value={form.owner_id} disabled={disabled} onChange={event => change({ owner_id: event.target.value })} required><option value="">اختر حساب المالك</option>{owners.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>)}
      {needsExpense && (demand ? picker('operating_expense','expense_id',expense,'حساب الصدقات') : <Field label="حساب الصدقات"><select value={form.expense_id} disabled={disabled} onChange={event => change({ expense_id: event.target.value })} required><option value="">اختر حساب المصروف</option>{expenses.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>)}
      <Field label={reason === 'adjustment' ? 'سبب الخصم' : 'ملاحظة (اختياري)'}><textarea rows="2" maxLength="1000" value={form.note} disabled={disabled} required={reason === 'adjustment'} onChange={event => change({ note: event.target.value })} /></Field>
      {!ad && <Summary resource={resource} amount={form.amount} side={form.side} choice={choice} reason={reason} employee={employee} owner={owner} expense={expense} source={source} />}
      <button className="sb-primary sb-submit" type="submit" disabled={busy || mutation.blocked || mutation.recovering || !canWrite}>{busy ? 'جارٍ تأكيد الحفظ…' : pending ? 'إعادة محاولة العملية نفسها' : 'حفظ والعودة للحركات'}</button>
    </form>
  </section>;
}

function BalanceDialog({ title, onClose, children }) {
  const box = useRef(null), back = useRef(null), close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement, overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden'; back.current?.focus();
    const keydown = event => {
      if (event.key === 'Escape') { event.preventDefault(); close.current(); return; }
      if (event.key !== 'Tab') return;
      const controls = [...box.current.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]')];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', keydown);
    return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown', keydown); if (previous?.isConnected) previous.focus(); };
  }, []);
  return <div className="sb-dialog-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose(); }}><section className="sb-dialog" ref={box} role="dialog" aria-modal="true" aria-labelledby="sb-dialog-title"><header><h2 id="sb-dialog-title">{title}</h2><button className="sb-secondary" type="button" ref={back} onClick={onClose}>رجوع</button></header>{children}</section></div>;
}

function AutomaticHistory({ resource, context, identity, page, onPage, onDetail }) {
  const key = JSON.stringify([identity, context.session_scope, context.permission_version, resource?.kind, resource?.id, page]);
  const read = useWorkspaceRead(async config => {
    const value = await api.history(resource.kind, resource.id, { page, page_size: BALANCE_PAGE_SIZE }, config);
    if (!isPage(value, page) || value.resource?.kind !== resource.kind || value.resource?.id !== resource.id
      || !['salary', 'delivery'].includes(value.automatic_history) || !Array.isArray(value.previous_months)) throw new Error('invalid automatic history');
    return value;
  }, key, Boolean(resource && resource.access?.movements !== 'none'));
  if (!resource) return <Notice error="هذا السجل غير متاح ضمن صلاحياتك." />;
  return <><Loading {...read} retry={read.reload} />{read.data && <><p className="sb-small-note">{resource.name} · {read.data.current_month} · {read.data.total} {read.data.automatic_history === 'salary' ? 'يوم' : 'توصيلة'} · {money(read.data.current_month_total)} {resource.currency || 'SAR'}</p>{read.data.resource.access?.movements === 'own' && <p className="sb-small-note">الحركات التي سجّلتها أنت فقط.</p>}<Ledger result={read.data} resource={read.data.resource} onPage={onPage} onDetail={onDetail} heading={false} />
    <h3 className="sb-history-months-title">الأشهر السابقة</h3><div className="sb-table-wrap"><table className="sb-ledger"><thead><tr><th>الشهر</th><th>{read.data.automatic_history === 'salary' ? 'الأيام' : 'التوصيلات'}</th><th>الإجمالي</th></tr></thead><tbody>{read.data.previous_months.map(month => <tr key={month.month}><td>{month.month}</td><td>{month.count}</td><td>{money(month.amount)} {month.currency || resource.currency}</td></tr>)}</tbody></table></div>{!read.data.previous_months.length && <p className="sb-small-note">لا توجد أشهر سابقة في السجل المتاح.</p>}</>}</>;
}

function Workspace({ context, identity, reloadContext }) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate(), location = useLocation();
  const [notice, setNotice] = useState(params.get('saved') === '1' ? 'تم حفظ الحركة. تظهر الآن في حسابها مع اسم من سجّلها.' : '');
  const tab = ['movements', 'parties', 'accounts'].includes(params.get('tab')) ? params.get('tab') : 'movements';
  const panel = ['sections', 'refunds', 'reports', 'reviews'].includes(params.get('panel')) ? params.get('panel') : '';
  const dialog = ['movement', 'history', 'statement', 'expected'].includes(params.get('dialog')) ? params.get('dialog') : '';
  const sections = panel === 'sections';
  const kind = params.get('kind'), id = params.get('id');
  const requestedPage = Math.max(1, Number.parseInt(params.get('page') || '1', 10) || 1);
  const dialogPage = Math.max(1, Number.parseInt(params.get('dialog_page') || '1', 10) || 1);
  const focusId = params.get('focus_id') || '', adding = !panel && params.get('action') === 'add';
  const selected = context.resources.find(resource => resource.kind === kind && resource.id === id);
  const filterResource = context.resources.find(resource => resource.kind === params.get('account_kind') && resource.id === params.get('account_id') && resource.access?.movements !== 'none');
  const invalidFilter = Boolean((params.get('account_kind') || params.get('account_id')) && !filterResource);
  const accessScope = JSON.stringify([identity, context.session_scope, context.permission_version]);
  const queryKey = JSON.stringify([accessScope, tab, kind, id, requestedPage, focusId, filterResource?.kind, filterResource?.id]);
  const lastPage = useRef(null);
  const read = useBalanceRead(async config => {
    const query = { page: requestedPage, page_size: BALANCE_PAGE_SIZE, ...(focusId ? { focus_id: focusId } : {}) };
    const result = selected ? await api.ledger(selected.kind, selected.id, query, config)
      : await api.movements({ ...query, ...(filterResource ? { kind: filterResource.kind, id: filterResource.id } : {}) }, config);
    if (!isPage(result, requestedPage, Boolean(focusId))) throw new Error('invalid ledger page');
    if (selected && (result.resource?.id !== selected.id || result.resource?.kind !== selected.kind)) throw new Error('wrong ledger resource');
    return result;
  }, queryKey, !panel && !dialog && !adding && !invalidFilter && (Boolean(selected) ? selected.access?.movements !== 'none' : !id && tab === 'movements'));
  const freshness = useVisibleBalanceFreshness({ context, identity,
    canRefresh: !dialog && !adding && !['refunds', 'reviews'].includes(panel),
    onConfirmed: () => { read.reload(); reloadContext(); },
  });
  if (read.data) lastPage.current = { key: queryKey, value: read.data };
  if ([403, 404].includes(read.status)) lastPage.current = null;
  const result = invalidFilter ? null : read.data || (lastPage.current?.key === queryKey ? lastPage.current.value : null);
  const resource = result?.resource || selected;
  const operations = permittedOperations(context, resource);
  const canAdd = resource && resource.access?.use === true && context.status === 'active'
    && (entryChoices(resource.kind, 'for_party', operations).length > 0 || entryChoices(resource.kind, 'for_us', operations).length > 0);
  const values = () => Object.fromEntries(params);
  const go = (next, options = {}) => setParams(Object.fromEntries(Object.entries(next).filter(([, value]) => value != null && value !== '')), {
    replace: Boolean(options.replace), state: options.replace ? location.state : { sbReturn: `${location.pathname}${location.search}`, ...options.state },
  });
  const back = () => {
    if (location.state?.sbReturn) { navigate(-1); return; }
    const next = values();
    if (dialog) { ['dialog', 'dialog_kind', 'dialog_id', 'dialog_page', 'movement_id'].forEach(key => delete next[key]); }
    else if (adding) delete next.action;
    else if (panel) delete next.panel;
    else { delete next.kind; delete next.id; next.tab = tab; }
    go(next, { replace: true });
  };
  const open = item => { setNotice(''); go({ tab: isMoneyAccount(item.kind) ? 'accounts' : 'parties', kind: item.kind, id: item.id }); };
  const page = value => go({ ...values(), page: value }, { replace: true });
  const openDialog = (name, row, extra = {}) => go({ ...values(), dialog: name, dialog_kind: row?.party_type || resource?.kind,
    dialog_id: row?.party_id || resource?.id, ...(row ? { movement_id: row.id } : {}), ...extra },
  { state: { sbRow: row, sbExpected: name === 'expected' ? result?.expected_collections : undefined, sbSnapshotScope: accessScope } });
  const saved = savedResult => {
    setNotice('تم حفظ الحركة. تظهر الآن في حسابها مع اسم من سجّلها.');
    go({ tab: isMoneyAccount(savedResult.party_type) ? 'accounts' : 'parties', kind: savedResult.party_type, id: savedResult.party_id, focus_id: savedResult.movement_id, saved: '1' });
    reloadContext();
  };
  const granted = new Set(context.operations.map(operation => typeof operation === 'string' ? operation : operation.key));
  const canRefund = granted.has('customer.refund_request') || granted.has('customer.refund_execute');
  const canReview = granted.has('courier.review') && context.resources.some(item => item.kind === 'store_driver' && item.access?.movements === 'all');
  const canReviewDriver = canReview && resource?.kind === 'store_driver' && resource.access?.movements === 'all';
  const canReports = context.resources.some(item => canReadStatement(context, item));
  const openRefunds = () => { setNotice(''); go({ tab: 'parties', panel: 'refunds' }); };
  const popupResource = context.resources.find(item => item.kind === (params.get('dialog_kind') || kind) && item.id === (params.get('dialog_id') || id));
  const snapshotValid = location.state?.sbSnapshotScope === accessScope && popupResource && popupResource.access?.movements !== 'none';
  const snapshotRow = snapshotValid && location.state?.sbRow?.id === params.get('movement_id') ? location.state.sbRow : null;
  const snapshotExpected = snapshotValid ? location.state?.sbExpected : null;
  const popupRead = useBalanceRead(async config => {
    const value = await api.ledger(popupResource.kind, popupResource.id, { page: 1, page_size: BALANCE_PAGE_SIZE,
      ...(dialog === 'movement' ? { focus_id: params.get('movement_id') } : {}) }, config);
    if (!isPage(value, 1, dialog === 'movement') || value.resource?.kind !== popupResource.kind || value.resource?.id !== popupResource.id) throw new Error('invalid movement detail');
    return value;
  }, JSON.stringify([accessScope, dialog, popupResource?.kind, popupResource?.id, params.get('movement_id')]),
  Boolean(popupResource && popupResource.access?.movements !== 'none' && (dialog === 'movement' && !snapshotRow || dialog === 'expected' && !snapshotExpected)));
  const detailRow = snapshotRow || popupRead.data?.items?.find(row => row.id === params.get('movement_id'));
  const expected = snapshotExpected || popupRead.data?.expected_collections;
  const reportFilters = { kind: params.get('report_kind') || '', account: params.get('report_account') || '', date_from: params.get('date_from') || '', date_to: params.get('date_to') || '' };
  const reportChange = form => go({ ...values(), report_kind: form.kind, report_account: form.account, date_from: form.date_from, date_to: form.date_to }, { replace: true });
  const title = sections ? 'جميع الأقسام' : panel === 'reports' ? 'التقارير المالية' : panel === 'reviews' ? 'المراجعات' : panel === 'refunds' ? 'الطلبات ورد المبالغ'
    : selected ? selected.name : tab === 'movements' ? 'الحركات المالية' : tab === 'accounts' ? 'الحسابات' : 'الأطراف';
  const subtitle = selected && !panel ? resourceLabels[selected.kind] : panel === 'reviews' ? 'تحصيلات الموصلين' : 'الأرصدة';
  return <main className="sb-workspace sb-compact" dir="rtl" aria-label="الأرصدة والحركات المالية">
    <div inert={dialog ? true : undefined}>
      <header className="sb-header"><div><h1>{title}</h1><span>{subtitle}</span></div><div className="sb-header-actions">
        {selected && canAdd && !adding && !panel && <button type="button" className="sb-menu" onClick={() => go({ ...values(), action: 'add' })}>{resource.kind === 'ad_account' ? 'إضافة دفعة للمنصة' : resource.kind === 'pos_receivable' ? 'موازنة / تحويل إلى بنك' : '+ إضافة'}</button>}
        {panel !== 'reviews' && (selected || panel || adding || location.state?.sbReturn) && <button type="button" className="sb-menu" onClick={back}>رجوع</button>}
        {!selected && !panel && <button type="button" className="sb-menu" aria-label="جميع الأقسام" onClick={() => go({ ...values(), panel: 'sections' })}>الأقسام</button>}
      </div></header>
      {freshness.owner && <BalanceFreshnessNotice state={freshness} canUpdate={freshness.canRefresh} onCheck={freshness.canRefresh ? freshness.retry : undefined} />}
      {sections ? <section className="sb-content"><div className="sb-section-links">
        {[...new Set(context.resources.map(item => item.kind))].filter(item => !isMoneyAccount(item)).map(item => <button type="button" key={item} onClick={() => go({ tab: 'parties', filter: item })}>{resourceLabels[item] || item}</button>)}
        {canReports && <button type="button" onClick={() => go({ tab, panel: 'reports' })}><strong>التقارير المالية</strong><span>كشف حساب حسب النوع والحساب والفترة.</span></button>}
        {canReview && <button type="button" onClick={() => go({ tab: 'parties', panel: 'reviews' })}><strong>المراجعات</strong><span>تحويلات العملاء وتحصيلات الشبكة للموصلين.</span></button>}
        {canRefund && <button type="button" onClick={openRefunds}><strong>الطلبات ورد المبالغ</strong><span>ابحث برقم الطلب، ثم سجّل استرجاعًا جزئيًا أو كامل المتاح.</span></button>}
        {context.actor.is_owner && operationGroups.filter(item => item.owner || item.keys.some(key => granted.has(key))).map(item => <Link key={item.to} to={item.to}><strong>{item.title}</strong><span>{item.text}</span></Link>)}
        {context.capabilities?.manage_access === true && <Link to="/operational-balances/access"><strong>صلاحيات الموظفين</strong><span>الحسابات المتاحة لكل موظف والعمليات المسموحة له.</span></Link>}
      </div></section> : <div className="sb-content"><Notice>{notice}</Notice>
        {!panel && context.status !== 'active' && <Notice>{context.status === 'draft' ? 'النظام في مرحلة الأرصدة الافتتاحية. تسجيل الحركات يبدأ بعد اعتماد البداية.' : 'النظام متاح للمراجعة، وتسجيل حركات جديدة متوقف حاليًا.'}</Notice>}
        {panel === 'reports' ? <SimpleBalanceStatement context={context} identity={identity} initialFilters={reportFilters} onFiltersChange={reportChange} />
          : panel === 'reviews' ? canReview ? <SimpleBalanceReviews context={context} identity={identity} driverId={selected?.kind === 'store_driver' ? selected.id : ''} onBack={back} onChanged={read.reload} /> : <Notice error="مراجعة التحصيلات غير متاحة ضمن صلاحياتك." />
          : panel === 'refunds' ? <SimpleOrderRefunds context={context} identity={identity} onBalancesChanged={reloadContext} onExit={back} />
          : id && !selected ? <Notice error="هذا الحساب غير متاح ضمن صلاحياتك الحالية." />
          : invalidFilter ? <><Notice error="الحساب المحدد غير متاح ضمن صلاحياتك الحالية." /><button className="sb-secondary" type="button" onClick={() => go({ ...values(), account_kind: '', account_id: '', page: 1 }, { replace: true })}>عرض الجميع</button></>
          : selected && [403, 404].includes(read.status) ? <><Notice error={read.error} /><button type="button" className="sb-secondary" onClick={reloadContext}>تحديث الصلاحيات</button></>
          : adding && selected ? <EntryForm key={`${context.session_scope}:${resourceKey(selected)}`} context={context} resource={selected} onCancel={back} onSaved={saved} />
          : selected ? <><BalanceCard resource={resource} />
            {result?.pos_summary && <dl className="sb-pos-summary"><dt>إيصالات الشبكة المعتمدة</dt><dd>{money(result.pos_summary.approved)} ريال</dd><dt>المحوّل إلى البنوك</dt><dd>{money(result.pos_summary.bank_received)} ريال</dd><dt>الرسوم الفعلية</dt><dd>{money(result.pos_summary.fees)} ريال</dd></dl>}
            {(canReviewDriver || result?.expected_collections?.total > 0) && <div className="sb-compact-actions">{canReviewDriver && <button type="button" className="sb-secondary" onClick={() => go({ ...values(), panel: 'reviews', review_driver: resource.id })}>مراجعاته</button>}{result?.expected_collections?.total > 0 && <button type="button" className="sb-secondary" onClick={() => openDialog('expected')}><strong>متوقع تحصيله · {money(result.expected_collections.amount)} {resource.currency || 'SAR'}</strong><small>{result.expected_collections.total} طلب · خارج الرصيد المؤكد</small></button>}</div>}
            {resource.access?.movements === 'own' && <p className="sb-small-note">يعرض السجل الحركات التي سجّلتها أنت فقط.</p>}
            {resource.access?.movements === 'none' ? <p className="sb-small-note">عرض حركات هذا الحساب غير متاح ضمن صلاحياتك.</p> : <><Loading {...read} retry={read.reload} />{result && <Ledger result={result} resource={resource} focusId={focusId} onPage={page} onDetail={row => openDialog('movement', row)} onHistory={row => openDialog('history', row)} onStatement={canReadStatement(context, resource) ? () => openDialog('statement') : undefined} />}</>}</>
          : tab === 'movements' ? <><div className="sb-daily-tools"><Field label="الحساب"><select value={filterResource ? resourceKey(filterResource) : ''} onChange={event => { const item = context.resources.find(value => resourceKey(value) === event.target.value); go({ ...values(), account_kind: item?.kind, account_id: item?.id, page: 1, focus_id: '' }, { replace: true }); }}><option value="">الجميع</option>{context.resources.filter(item => item.access?.movements !== 'none').map(item => <option key={resourceKey(item)} value={resourceKey(item)}>{item.name}</option>)}</select></Field><button type="button" className="sb-secondary" onClick={read.reload}>تحديث</button></div><Loading {...read} retry={read.reload} />{result && <Ledger result={result} focusId={focusId} onPage={page} onDetail={row => openDialog('movement', row)} onHistory={row => openDialog('history', row)} onStatement={canReports ? () => openDialog('statement', null, { dialog_kind: filterResource?.kind, dialog_id: filterResource?.id }) : undefined} />}</>
          : <>{tab === 'parties' && canRefund && <button type="button" className="sb-secondary" onClick={openRefunds}>بحث برقم الطلب</button>}<ResourceList key={tab} context={context} tab={tab} identity={identity} onOpen={open} initialKind={params.get('filter') || ''} initialQuery={params.get('q') || ''} initialPage={requestedPage} onStateChange={patch => go({ ...values(), ...patch }, { replace: true })} /></>}
      </div>}
      <nav className="sb-tabs" aria-label="صفحات الأرصدة">{[['movements', 'الحركات'], ['parties', 'الأطراف'], ['accounts', 'الحسابات']].map(([value, label]) => <button type="button" key={value} aria-current={!panel && tab === value ? 'page' : undefined} onClick={() => { setNotice(''); go({ tab: value }); }}>{label}</button>)}</nav>
    </div>
    {dialog && <BalanceDialog title={dialog === 'statement' ? 'كشف فترة' : dialog === 'history' ? popupResource?.kind === 'employee' ? 'سجل الرواتب' : 'سجل التوصيلات' : dialog === 'expected' ? 'متوقع تحصيله' : 'تفاصيل الحركة'} onClose={back}>
      {dialog === 'statement' ? params.get('dialog_id') && !popupResource ? <Notice error="هذا الحساب غير متاح ضمن صلاحياتك الحالية." /> : <SimpleBalanceStatement key={popupResource ? resourceKey(popupResource) : 'all'} context={context} identity={identity} resource={popupResource} initialFilters={reportFilters} onFiltersChange={reportChange} />
        : dialog === 'history' ? <AutomaticHistory resource={popupResource} context={context} identity={identity} page={dialogPage} onPage={value => go({ ...values(), dialog_page: value }, { replace: true })} onDetail={row => openDialog('movement', row)} />
        : <><Loading {...popupRead} retry={popupRead.reload} />{dialog === 'movement' ? detailRow ? <MovementDetails row={detailRow} /> : !popupRead.loading && !popupRead.error && <Notice error="هذه الحركة غير متاحة. ارجع إلى السجل وأعد فتحها." />
          : expected ? <><p className="sb-small-note">{expected.total} طلب · {money(expected.amount)} {popupResource?.currency || 'SAR'} متوقع من العملاء، خارج رصيد الموصل المؤكد.</p><div className="sb-table-wrap"><table className="sb-ledger"><thead><tr><th>الطلب / المصدر</th><th>المتوقع</th></tr></thead><tbody>{expected.items.map(row => <tr key={row.id}><td>{movementTitle(row)}<small>لم يتأكد التحصيل</small></td><td>{money(row.amount)}</td></tr>)}</tbody></table></div><p className="sb-small-note">عرض {expected.items.length} من {expected.total}. يصبح النقد عهدة عند ثبوت التسليم والتحصيل.</p></> : !popupRead.loading && !popupRead.error && <p className="sb-small-note">لا توجد مبالغ متوقعة متاحة.</p>}</>}
    </BalanceDialog>}
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

export { AutomaticHistory, BalanceCard, EntryForm, Ledger, Pages, routeBase };
