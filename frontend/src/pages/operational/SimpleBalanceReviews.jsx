import React, { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { useBalanceRead } from './simpleBalancesHooks';
import { money, movementDate } from './simpleBalancesModel';
import WorkspaceResourcePicker from './WorkspaceResourcePicker';
import './simpleBalanceReviews.css';

const statusLabels = { pending: 'بانتظار المراجعة', approved: 'معتمدة', rejected: 'مرفوضة' };
const methodLabel = value => value === 'bank_transfer' ? 'تحويل بنكي' : 'شبكة';
const reviewTitle = item => item.customer_name || (item.order_number ? `طلب #${item.order_number}` : 'إيصال دفع');
const dateLabel = value => value && Number.isFinite(new Date(value).getTime())
  ? new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn', { timeZone: 'Asia/Riyadh', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '—';
const initialDraft = () => ({ bank: '', movement: '', movementLabel: '', pos: '', amount: '', reference: '', note: '', matched: false });
const cents = value => {
  const text = String(value ?? '').trim();
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return null;
  const [whole, fraction = ''] = text.split('.');
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
};

function Receipt({ id, identity, onBack }) {
  const read = useBalanceRead(config => api.reviewReceipt(id, config), JSON.stringify([identity, id, 'receipt']));
  const [image, setImage] = useState(null);
  const heading = useRef(null);
  useEffect(() => { const previous = document.activeElement; heading.current?.focus(); return () => previous?.focus?.(); }, []);
  useEffect(() => {
    if (!(read.data instanceof Blob)) { setImage(null); return undefined; }
    const url = URL.createObjectURL(read.data);
    setImage({ blob: read.data, url });
    return () => URL.revokeObjectURL(url);
  }, [read.data]);
  const source = image?.blob === read.data ? image.url : null;
  return <section className="sbr-proof" aria-label="صورة الإيصال" onKeyDown={event => { if (event.key === 'Escape') onBack(); }}>
    <header><h2 ref={heading} tabIndex={-1}>صورة الإيصال</h2><button type="button" className="sb-secondary" onClick={onBack}>رجوع</button></header>
    {read.error && <p role="alert">{read.error}</p>}
    {read.loading && <p role="status">جارٍ تحميل الإيصال…</p>}
    {source && (read.data.type === 'application/pdf'
      ? <object data={source} type="application/pdf" aria-label="ملف الإيصال"><a href={source} target="_blank" rel="noreferrer">فتح الإيصال</a></object>
      : <img src={source} alt="الإيصال المرفق بالطلب" />)}
  </section>;
}

function BankMovements({ id, identity, bank, selected, selectedLabel, disabled, onSelect }) {
  const [page, setPage] = useState(1);
  const read = useBalanceRead(config => api.reviewBankMovements(id, { page, page_size: 5 }, config),
    JSON.stringify([identity, id, bank, page, 'bank-candidates']), Boolean(bank));
  return <section className="sbr-bank-candidates" aria-label="حركات البنك المطابقة">
    <strong>الحركة الواردة في كشف البنك</strong>
    {!bank && <p className="sb-small-note">اختر البنك لعرض الحركات الواردة بالمبلغ نفسه.</p>}
    {read.error && <p role="alert">{read.error}</p>}
    {bank && read.loading && <p role="status">جارٍ تحميل حركات البنك…</p>}
    {bank && read.data && <>
      {read.data.items.map(row => <label className="sbr-bank-candidate" key={row.id}>
        <input type="radio" name={`bank-movement-${id}`} checked={selected === row.id} disabled={disabled}
          onChange={() => onSelect(row.id, `${row.movement_date} · ${money(row.amount)} ريال${row.reference ? ` · ${row.reference}` : ''}`)} />
        <span><b dir="ltr">{money(row.amount)} ريال</b><small>{row.movement_date}</small>
          {row.reference && <small>المرجع: <bdi>{row.reference}</bdi></small>}{row.description && <small>{row.description}</small>}</span>
      </label>)}
      {!read.data.items.length && <p className="sb-small-note">لا توجد حركة واردة مطابقة متاحة. استورد كشف البنك ثم حدّث القائمة.</p>}
      <div className="sb-pagination"><button type="button" disabled={disabled || read.data.page <= 1} onClick={() => setPage(read.data.page - 1)}>الأحدث</button><span>{read.data.page} من {read.data.total_pages}</span><button type="button" disabled={disabled || read.data.page >= read.data.total_pages} onClick={() => setPage(read.data.page + 1)}>الأقدم</button></div>
    </>}
    {selected && <p className="sb-small-note">الحركة المختارة: <bdi>{selectedLabel}</bdi></p>}
    {bank && <button type="button" className="sb-secondary" disabled={disabled || read.loading} onClick={read.reload}>تحديث حركات البنك</button>}
  </section>;
}

function ReviewDetail({ id, identity, onProof, onChanged, readOnly, visible = true }) {
  const read = useBalanceRead(async config => {
    const item = await api.review(id, config);
    if (item?.assignment_id !== id) throw new Error('invalid review');
    const options = item.status === 'pending' && !readOnly ? await api.reviewOptions(id, config) : null;
    return { item, options };
  }, JSON.stringify([identity, id]), visible);
  const [draft, setDraft] = useState(initialDraft);
  const [decision, setDecision] = useState(null);
  const [uncertain, setUncertain] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const item = read.data?.item;
  const options = read.data?.options;
  const update = (field, value) => setDraft(previous => ({ ...previous, [field]: value }));
  const contextValid = options?.financial_source === 'operational' && options.session_scope && Number.isInteger(options.expected_review_revision);
  const bank = item?.payment_method === 'bank_transfer';
  const defaultPos = options?.pos_receivables?.find(row => row.is_default);
  const selectedPos = defaultPos?.id || draft.pos;
  async function bindPos() {
    if (lock.current || !options?.can_configure_pos) return;
    lock.current = true; setBusy(true); setError('');
    try {
      await api.bindPos({ expected_session_scope: options.session_scope, existing_id: draft.pos || null, confirmed: true });
      if (mounted.current) read.reload();
    } catch (failure) {
      if (mounted.current) setError(simpleBalanceError(failure));
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  const validTarget = bank
    ? options?.banks?.some(row => row.id === draft.bank) && draft.bank === item.bank_account_id && draft.movement.trim()
    : options?.pos_receivables?.some(row => row.id === selectedPos) && cents(draft.amount) !== null && cents(draft.amount) === cents(item?.amount);
  const approved = Boolean(contextValid && options?.can_approve && item?.has_receipt && draft.matched && validTarget);
  async function decide(choice, retry = false) {
    if (lock.current || !item || !contextValid) return;
    if (!retry && (uncertain || (choice === 'approved' ? !approved : !options.can_reject || !draft.note.trim()))) return;
    const payload = retry ? uncertain : {
      decision: choice, note: draft.note.trim() || 'تمت مطابقة الإيصال',
      expected_financial_source: options.financial_source,
      expected_review_revision: options.expected_review_revision,
      expected_session_scope: options.session_scope,
      ...(choice === 'approved' ? bank
        ? { destination_financial_id: draft.bank, settlement_reference: draft.movement.trim() }
        : { destination_financial_id: selectedPos, pos_reviewed_amount: draft.amount.trim(), ...(draft.reference.trim() ? { settlement_reference: draft.reference.trim() } : {}) }
        : {}),
    };
    if (!payload) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const result = await api.reviewDecision(id, payload);
      if (!['approved', 'rejected'].includes(result?.decision) || result.decision !== payload.decision) throw new Error('invalid decision');
      if (!mounted.current) return;
      setDecision(result.decision); setUncertain(null);
      read.reload();
      onChanged?.();
    } catch (failure) {
      if (!mounted.current) return;
      if (!failure?.response?.status || failure.response.status >= 500) setUncertain(payload);
      else setUncertain(null);
      setError(simpleBalanceError(failure));
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  if (read.error) return <p role="alert" className="sb-notice sb-error">{read.error}<button type="button" onClick={read.reload}>إعادة المحاولة</button></p>;
  if (!item) return <p role="status">جارٍ تحميل تفاصيل الإيصال…</p>;
  const currentStatus = decision || item.status;
  return <article className="sbr-detail">
    <div className="sbr-summary"><div><h2>{reviewTitle(item)}</h2>{item.customer_name && item.order_number && <p>طلب #{item.order_number}</p>}<p>{item.driver_name} · {methodLabel(item.payment_method)}</p></div><strong dir="ltr">{money(item.amount)} <small>ريال</small></strong></div>
    <p className={`sbr-status sbr-${currentStatus}`}>{statusLabels[currentStatus]}</p>
    <p className="sb-small-note">أرسل: {movementDate({ recorded_at: item.submitted_at }, true)}</p>
    {item.reviewed_at && <p className="sb-small-note">راجعه: {item.reviewed_by_name || 'غير موثق'} · {movementDate({ recorded_at: item.reviewed_at }, true)}</p>}
    <p className="sb-small-note">أثر مبلغ الإيصال على صافي مستحق الموصل: <bdi>0.00 ريال</bdi>. المبلغ محصل لحساب المنشأة.</p>
    {item.has_receipt && <button type="button" className="sb-secondary" onClick={onProof}>عرض الإيصال</button>}
    {item.review_note && <p>{item.review_note}</p>}
    {readOnly && currentStatus === 'pending' ? <p>بانتظار المراجعة</p> : currentStatus !== 'pending' ? <p role="status" className="sb-notice">{currentStatus === 'approved' ? 'تم الاعتماد. سيظهر الإيصال ضمن المعتمدة، وتُحدّث حالته عند قراءة الموصل لحسابه.' : 'الإيصال مرفوض. يظهر سبب الرفض للموصل.'}</p> : <>
      <p className="sb-small-note">{bank ? 'طابق الإيصال بحركة البنك الواردة المستوردة.' : 'الاعتماد يثبت المبلغ في ذمة الشبكة حتى تسويته للبنك.'} لا يضيف مبلغ العميل إلى أجرة الموصل.</p>
      {options?.blocker && <p role="alert" className="sb-notice sb-error">تعذر الاعتماد لعدم اكتمال دليل التحصيل أو التسليم. {options.note || ''}</p>}
      {!contextValid && <p role="alert" className="sb-notice sb-error">مسار الاعتماد غير متاح. حدّث تفاصيل الإيصال.</p>}
      <fieldset disabled={busy || Boolean(uncertain)} className="sbr-fields">
        {bank ? <>
          <label className="sb-field"><span>البنك المستلم</span><select value={draft.bank} onChange={event => setDraft(previous => ({ ...previous, bank: event.target.value, movement: '', movementLabel: '' }))}><option value="">اختر البنك</option>{(options?.banks || []).map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
          {options && !options.banks?.length && <p className="sb-small-note">يلزم السماح باستخدام البنك ومشاهدة حركاته لمطابقة هذا الإيصال.</p>}
          <BankMovements key={draft.bank} id={id} identity={identity} bank={draft.bank} selected={draft.movement} selectedLabel={draft.movementLabel} disabled={busy || Boolean(uncertain)} onSelect={(movement, movementLabel) => setDraft(previous => ({ ...previous, movement, movementLabel }))} />
        </> : <>
          <label className="sb-field"><span>حساب الشبكة</span><select value={selectedPos} disabled={Boolean(defaultPos)} onChange={event => update('pos', event.target.value)}><option value="">اختر الشبكة</option>{(options?.pos_receivables || []).map(row => <option key={row.id} value={row.id}>{row.display_name}</option>)}</select></label>
          {options?.can_configure_pos && <button type="button" className="sb-secondary" disabled={busy || Boolean(options.pos_receivables?.length && !draft.pos)} onClick={bindPos}>{options.pos_receivables?.length ? 'اعتماد الذمة المختارة لتحصيلات الشبكة القادمة' : 'إنشاء وربط ذمة الشبكة دون رصيد افتتاحي'}</button>}
          <label className="sb-field"><span>مبلغ الإيصال</span><input inputMode="decimal" value={draft.amount} onChange={event => update('amount', event.target.value)} /></label>
          <label className="sb-field"><span>مرجع الشبكة · اختياري</span><input value={draft.reference} onChange={event => update('reference', event.target.value)} /></label>
        </>}
        <label className="sb-field"><span>ملاحظة · مطلوبة عند الرفض</span><textarea value={draft.note} onChange={event => update('note', event.target.value)} maxLength={1000} /></label>
        <label className="sb-check"><input type="checkbox" checked={draft.matched} onChange={event => update('matched', event.target.checked)} /><span>راجعت الإيصال وطابقت المبلغ</span></label>
      </fieldset>
      {error && <p role="alert" className="sb-notice sb-error">{error}</p>}
      {uncertain ? <div className="sb-notice"><p>نتيجة الطلب غير مؤكدة. تحقق من حالته أو أعد نفس الطلب.</p><button type="button" disabled={busy} onClick={() => decide(uncertain.decision, true)}>إعادة نفس الطلب</button><button type="button" disabled={busy} onClick={read.reload}>التحقق من الحالة</button></div>
        : <div className="sb-actions"><button type="button" className="sb-primary" disabled={busy || !approved} onClick={() => decide('approved')}>اعتماد الإيصال</button><button type="button" className="sb-secondary" disabled={busy || !contextValid || !options?.can_reject || !draft.note.trim()} onClick={() => decide('rejected')}>رفض الإيصال</button></div>}
    </>}
  </article>;
}

export default function SimpleBalanceReviews({ context, driverId = '', identity = '', onBack, onChanged, readOnly=false }) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate(), location = useLocation();
  const id = params.get('review_id') || '';
  const proof = params.get('review_proof') === '1';
  const status = Object.hasOwn(statusLabels, params.get('review_status')) ? params.get('review_status') : 'pending';
  const driver = driverId || params.get('review_driver') || '';
  const method = params.get('review_method') || '';
  const bankId = params.get('review_bank') || '';
  const page = Math.max(1, Number(params.get('review_page')) || 1);
  const key = JSON.stringify([identity, context.session_scope, context.permission_version, status, driver, method, bankId, page]);
  const allowed = context.operations.some(row => (typeof row === 'string' ? row : row.key) === 'courier.review');
  const read = useBalanceRead(config => api.reviews({ status, ...(driver ? { driver_id: driver } : {}), ...(method ? { method } : {}), ...(bankId ? { bank_id: bankId } : {}), page, page_size: 5 }, config), key, allowed && !id);
  const go = (patch, replace = !('review_id' in patch || 'review_proof' in patch)) => {
    const next = new URLSearchParams(params);
    for (const [name, value] of Object.entries(patch)) value ? next.set(name, String(value)) : next.delete(name);
    setParams(next, { replace, state: replace ? location.state : { ...location.state, sbrReturn: `${location.pathname}${location.search}` } });
  };
  const back = () => {
    if ((id || proof) && location.state?.sbrReturn) navigate(-1);
    else if (proof) go({ review_proof: null }, true);
    else if (id) go({ review_id: null, review_proof: null }, true);
    else onBack();
  };
  const drivers = context.resources.filter(row => row.kind === 'store_driver' && row.access.movements === 'all');
  const banks = context.resources.filter(row => row.kind === 'bank');
  return <section className="sbr-page">
    {!proof && <header className="sb-section-title"><h2>مراجعات الإيصالات</h2><button type="button" className="sb-secondary" onClick={back}>رجوع</button></header>}
    {!allowed ? <p role="alert">لا توجد صلاحية مراجعة الإيصالات.</p> : id ? <>
      <div hidden={proof}><ReviewDetail visible={!proof} readOnly={readOnly} key={`${identity}:${context.session_scope}:${context.permission_version}:${id}`} id={id} identity={key} onProof={() => go({ review_proof: '1' })} onChanged={onChanged} /></div>
      {proof && <Receipt id={id} identity={key} onBack={back} />}
    </> : <>
      <div className="sbr-tabs" role="group" aria-label="حالة المراجعة">{Object.entries(statusLabels).map(([value, label]) => <button type="button" key={value} aria-pressed={status === value} onClick={() => go({ review_status: value, review_page: null })}>{label}</button>)}</div>
      <div className="sbr-filters">
        {!driverId && (context.demandOnly ? <ReviewFilter context={context} kind="store_driver" role="recipient" label="الموصل" value={driver} onSelect={value => go({ review_driver: value, review_page: null })}/>
          : <label className="sb-field"><span>الموصل</span><select value={driver} onChange={event => go({ review_driver: event.target.value, review_page: null })}><option value="">جميع الموصلين المسموحين</option>{drivers.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>)}
        <label className="sb-field"><span>طريقة الدفع</span><select value={method} onChange={event => go({ review_method: event.target.value, review_bank: null, review_page: null })}><option value="">الجميع</option><option value="bank_transfer">تحويل بنكي</option><option value="card_terminal">شبكة</option></select></label>
        {method !== 'card_terminal' && (context.demandOnly ? <ReviewFilter context={context} kind="bank" role="source" label="البنك" value={bankId} onSelect={value => go({ review_bank: value, review_page: null })}/>
          : <label className="sb-field"><span>البنك</span><select value={bankId} onChange={event => go({ review_bank: event.target.value, review_page: null })}><option value="">الجميع</option>{banks.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>)}
      </div>
      {read.error && <p role="alert" className="sb-notice sb-error">{read.error}</p>}
      {read.loading && <p role="status">جارٍ تحميل المراجعات…</p>}
      {read.data && <><p className="sb-small-note">{read.data.total} إيصالات · 5 في الصفحة</p><div className="sbr-list">{read.data.items.map(row => <button type="button" key={row.assignment_id} className="sbr-row" onClick={() => go({ review_id: row.assignment_id })}><span><strong>{reviewTitle(row)}</strong><small>{row.driver_name} · {methodLabel(row.payment_method)}</small><small>{row.bank_name_snapshot || ''} · {dateLabel(row.submitted_at)}</small></span><b dir="ltr">{money(row.amount)}<small>ريال</small></b></button>)}</div>
        {!read.data.items.length && <p className="sb-empty">لا توجد إيصالات ضمن هذا الاختيار.</p>}
        <div className="sb-pagination"><button type="button" disabled={read.data.page <= 1} onClick={() => go({ review_page: read.data.page - 1 })}>الأحدث</button><span>الصفحة {read.data.page} من {read.data.total_pages}</span><button type="button" disabled={read.data.page >= read.data.total_pages} onClick={() => go({ review_page: read.data.page + 1 })}>الأقدم</button></div></>}
      <button type="button" className="sb-secondary" onClick={read.reload}>تحديث</button>
    </>}
  </section>;
}

function ReviewFilter({context,kind,role,label,value,onSelect}) {
  const [opened,setOpened]=useState(false), [picked,setPicked]=useState(null);
  const selected=picked?.id===value?picked:context.resources.find(item=>item.kind===kind&&item.id===value);
  return <div><button type="button" className="sb-secondary" onClick={()=>setOpened(current=>!current)}>
    {label}: {selected?.name || (value?'المحدد':'الجميع')}</button>
    {opened && <WorkspaceResourcePicker context={context} operation="courier.review" role={role} kind={kind} label={label} value={selected} optional
      onSelect={item=>{setPicked(item);setOpened(false);onSelect(item?.id||'');}}/>}
  </div>;
}
