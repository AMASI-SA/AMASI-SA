import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Amount, Icon } from './ApprovedFinanceUI';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { useBalanceRead } from './simpleBalancesHooks';
import { usePendingBalanceCommand } from './simpleBalancesPending';
import { BALANCE_PAGE_SIZE, decimalAmount, isPage, minorUnits, money, permittedOperations } from './simpleBalancesModel';

const statusLabel = status => ({ pending: 'بانتظار الرد', refunded: 'تم تسجيل رد المبلغ', cancelled: 'ملغى' }[status] || 'قيد المراجعة');
const unit = currency => currency === 'SAR' || !currency ? 'ريال' : currency;
const methodLabel = item => item.method === 'bank' ? 'تحويل بنكي' : item.source?.name || 'مزود الدفع';
const localTime = value => {
  const date = new Date(value || Date.now());
  return Number.isNaN(date.getTime()) ? '' : new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
const dateLabel = value => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'غير موثق' : new Intl.DateTimeFormat('ar-SA-u-ca-gregory-nu-latn', { dateStyle: 'medium', timeZone: 'Asia/Riyadh' }).format(date);
};
const Field = ({ label, children }) => <label className="sb-field"><span>{label}</span>{children}</label>;

function useRefundMutation({ context, identity, targetId = null, validate, execute, validateSaved, onSaved, onRetired, resolved = false }) {
  const unpack = value => ({ action: value.action.slice('refund.'.length), body: value.command });
  const mutation = usePendingBalanceCommand({ context,
    storageIdentity: JSON.stringify(['refund', context.actor.id, context.session_scope, identity]),
    validate: value => value.action.startsWith('refund.') && value.target_id === targetId && validate(unpack(value)),
    execute: value => execute(unpack(value)), validateSaved,
    onSaved: result => onSaved(result.item), onRetired });
  return { ...mutation, resolved, command: !resolved && mutation.pending ? unpack(mutation.pending) : null,
    submit: (action, makeBody) => mutation.submit(`refund.${action}`, targetId, makeBody) };
}

function MutationStatus({ mutation }) {
  if (mutation.resolved) return null;
  return <>{mutation.error && <p className="sb-notice sb-error" role="alert">{mutation.error}</p>}
    {mutation.recovering && <p className="sb-notice" role="status">جارٍ استعادة العملية السابقة من الخادم…</p>}
    {mutation.blocked && <button type="button" className="sb-secondary" disabled={mutation.busy || mutation.recovering} onClick={mutation.recover}>استعادة العملية السابقة</button>}
    {mutation.command && <p className="sb-notice" role="status">هناك عملية بانتظار تأكيد النتيجة. إعادة المحاولة تستخدم المرجع نفسه.</p>}</>;
}

export function RefundRequestForm({ context, order, onSaved, onRecoveryRetired, approved = false, showForm = true }) {
  const operations = permittedOperations(context);
  const permitted = context.status === 'active' && operations.has('customer.refund_request');
  const allowed = permitted && order.can_request === true;
  const mutation = useRefundMutation({ context, identity: `order:${order.id || order.order_number}`,
    validate: value => value.action === 'create' && value.body.order_number === order.order_number,
    execute: value => api.requestRefund(value.body),
    validateSaved: (result, pending) => Boolean(result.item?.id) && result.item.order_number === pending.command.order_number
      && minorUnits(result.item.amount) === minorUnits(pending.command.amount) && result.item.method === pending.command.method
      && (pending.command.method !== 'bank' || result.item.source?.kind === 'bank' && result.item.source.id === pending.command.bank_id),
    onSaved, onRetired: onRecoveryRetired });
  const [mode, setMode] = useState('partial');
  const [form, setForm] = useState({ amount: '', method: '', bank_id: '', reason: '', reference: '' });
  const pendingBody = mutation.command?.body;
  useEffect(() => {
    if (!pendingBody) return;
    setMode('partial'); setForm({ amount: pendingBody.amount, method: pendingBody.method, bank_id: pendingBody.bank_id || '',
      reason: pendingBody.reason, reference: pendingBody.reference || '' });
  }, [pendingBody]);
  const disabled = mutation.busy || Boolean(mutation.command) || mutation.blocked || mutation.recovering || !allowed;
  const available = minorUnits(order.available_amount);
  const canRequestAmount = available !== null && available > 0n;
  const choose = patch => { if (!disabled) setForm(value => ({ ...value, ...patch })); };
  async function submit(event) {
    event.preventDefault();
    if (!permitted || !mutation.command && !allowed) return;
    await mutation.submit('create', () => {
      const amount = decimalAmount(mode === 'full' ? order.available_amount : form.amount);
      if (!amount || available === null || minorUnits(amount) > available) throw { localMessage: 'أدخل مبلغًا ضمن المتاح للاسترجاع من التحصيل المؤكد.' };
      if (!(order.methods || []).some(method => method.id === form.method)) throw { localMessage: 'اختر طريقة الاسترجاع.' };
      if (form.method === 'bank' && !(order.banks || []).some(bank => bank.id === form.bank_id)) throw { localMessage: 'اختر بنك المؤسسة الذي سيخرج منه المبلغ.' };
      if (!form.reason.trim()) throw { localMessage: 'اكتب سبب الاسترجاع.' };
      return { order_number: order.order_number, amount, method: form.method, reason: form.reason.trim(),
        ...(form.method === 'bank' ? { bank_id: form.bank_id } : {}), ...(form.reference.trim() ? { reference: form.reference.trim() } : {}) };
    });
  }
  if (approved) {
    const original = order.methods.find(item => item.id === 'original');
    return <><section className="panel rf-block"><div className="between rf-head"><h2>طلب <bdi>#{order.order_number}</bdi></h2>
      {original && <span className="rf-tag"><Icon name="credit-card"/>طريقة الدفع الأصلية · {original.label}</span>}</div>
      <div className="rf-metrics">{[['دفع العميل', order.paid_amount], ['سبق إرجاعه', order.refunded_amount], ['بانتظار الرد', order.reserved_amount], ['تقدر ترجع', order.available_amount]].map(([label, value], index) =>
        <div key={label} className={'metric' + (index === 3 ? ' accent' : '')}><small>{label}</small><strong><Amount value={value} currency={order.currency}/></strong></div>)}</div>
    </section>{showForm && <><MutationStatus mutation={mutation} />
      {(allowed && canRequestAmount || mutation.command) ? <form className="panel rf-block" onSubmit={submit}>
        <div className="between rf-head"><h2>تفاصيل الإرجاع</h2></div><div className="fields">
        <fieldset className="rf-mode"><legend>كم تبغى ترجع؟</legend><div className="tabset">
          <button className={mode === 'partial' ? 'on' : ''} type="button" aria-pressed={mode === 'partial'} disabled={disabled} onClick={() => setMode('partial')}>جزء من المبلغ</button>
          <button className={mode === 'full' ? 'on' : ''} type="button" aria-pressed={mode === 'full'} disabled={disabled} onClick={() => setMode('full')}>كامل المتاح</button></div></fieldset>
        <label className="field"><span>المبلغ ({unit(order.currency)})</span><input inputMode="decimal" placeholder="0.00" value={mode === 'full' ? order.available_amount : form.amount} readOnly={mode === 'full'} disabled={disabled} onChange={e => choose({ amount: e.target.value })} required /></label>
        <label className="field"><span>كيف بترجع المبلغ؟</span><select value={form.method} required disabled={disabled} onChange={e => choose({ method: e.target.value, bank_id: '' })}><option value="">اختر طريقة الإرجاع</option>{order.methods.map(item => <option key={item.id} value={item.id}>{item.id === 'bank' ? 'تحويل من بنك المؤسسة' : 'عن طريق ' + item.label}</option>)}</select></label>
        {form.method === 'bank' && <label className="field"><span>البنك الذي بيخرج منه المبلغ</span><select value={form.bank_id} required disabled={disabled} onChange={e => choose({ bank_id: e.target.value })}><option value="">اختر بنك المؤسسة</option>{order.banks.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select></label>}
        <label className="field"><span>سبب الإرجاع</span><input required minLength="3" maxLength="1000" value={form.reason} disabled={disabled} onChange={e => choose({ reason: e.target.value })} placeholder="مثلاً فرق سعر التغليف" /></label>
        <label className="field"><span>مرجع العملية (اختياري)</span><input maxLength="160" value={form.reference} disabled={disabled} onChange={e => choose({ reference: e.target.value })} /></label>
        </div><div className="rf-foot"><span className="rf-quiet"><Icon name="clock-3"/> الحفظ يحجز المبلغ حتى توثيق ردّه.</span>
          <button className="button primary" type="submit" disabled={mutation.busy || mutation.blocked || mutation.recovering || !(mutation.command ? permitted : allowed)}>{mutation.busy ? 'جارٍ الحفظ…' : mutation.command ? 'إعادة محاولة العملية نفسها' : 'حفظ طلب الإرجاع'}</button>
        </div></form> : <p className="note">{!canRequestAmount ? 'ما فيه مبلغ متاح لإرجاع جديد.' : 'إنشاء طلب إرجاع غير متاح ضمن صلاحياتك الحالية.'}</p>}</>}</>;
  }
  return <section className="sb-refund-create"><div className="sb-title"><h2>طلب {order.order_number}</h2><p>الاسترجاع من المبلغ المحصّل المؤكد</p></div>
    <dl className="sb-order-totals">{[['المدفوع المؤكد', order.paid_amount], ['سبق رده', order.refunded_amount], ['بانتظار الرد', order.reserved_amount], ['المتاح للاسترجاع', order.available_amount]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd><bdi>{money(value)}</bdi> {unit(order.currency)}</dd></div>)}</dl>
    {!canRequestAmount && <p className="sb-notice">لا يوجد مبلغ متاح لاسترجاع جديد من هذا الطلب.</p>}
    {!allowed && <p className="sb-small-note">يمكنك مراجعة الطلب. إنشاء طلب استرجاع غير متاح ضمن صلاحياتك الحالية.</p>}
    <MutationStatus mutation={mutation} />
    {(allowed && canRequestAmount || mutation.command) && <form onSubmit={submit} noValidate>
      <div className="sb-refund-modes"><button type="button" className="sb-secondary" aria-pressed={mode === 'partial'} disabled={disabled} onClick={() => setMode('partial')}>رد جزئي</button><button type="button" className="sb-secondary" aria-pressed={mode === 'full'} disabled={disabled} onClick={() => { setMode('full'); choose({ amount: order.available_amount }); }}>رد كامل المتاح</button></div>
      <Field label="المبلغ المسترجع"><input inputMode="decimal" dir="ltr" value={mode === 'full' ? order.available_amount : form.amount} disabled={disabled || mode === 'full'} onChange={event => choose({ amount: event.target.value })} required /></Field>
      <Field label="طريقة الاسترجاع"><select value={form.method} disabled={disabled} onChange={event => choose({ method: event.target.value })} required><option value="">اختر طريقة الاسترجاع</option>{(order.methods || []).map(method => <option key={method.id} value={method.id}>{method.label}</option>)}</select></Field>
      {form.method === 'bank' && <Field label="بنك المؤسسة الذي سيخرج منه المبلغ"><select value={form.bank_id} disabled={disabled} onChange={event => choose({ bank_id: event.target.value })} required><option value="">اختر البنك</option>{(order.banks || []).map(bank => <option key={bank.id} value={bank.id}>{bank.name}</option>)}</select></Field>}
      <Field label="سبب الاسترجاع"><textarea rows="2" maxLength="1000" value={form.reason} disabled={disabled} onChange={event => choose({ reason: event.target.value })} required /></Field>
      <Field label="مرجع العملية (اختياري)"><input value={form.reference} disabled={disabled} onChange={event => choose({ reference: event.target.value })} /></Field>
      <p className="sb-small-note">حفظ الطلب يحجز المبلغ. يُسجّل خروجه عند توثيق ردّه بالفعل.</p>
      <button className="sb-primary sb-submit" type="submit" disabled={mutation.busy || mutation.blocked || mutation.recovering || !(mutation.command ? permitted : allowed)}>{mutation.busy ? 'جارٍ حفظ الطلب…' : mutation.command ? 'إعادة محاولة العملية نفسها' : 'حفظ طلب الاسترجاع'}</button>
    </form>}
  </section>;
}

export function RefundDetails({ context, item, onSaved, onBack, onRecoveryRetired }) {
  const operations = permittedOperations(context);
  const mutation = useRefundMutation({ context, identity: `item:${item.id}`, targetId: item.id,
    validate: value => ['confirm', 'cancel', 'reconcile'].includes(value.action),
    execute: value => value.action === 'confirm' ? api.confirmRefund(item.id, value.body)
      : value.action === 'reconcile' ? api.reconcileRefund(item.id, value.body) : api.cancelRefund(item.id, value.body),
    validateSaved: (result, pending) => result.item?.id === pending.target_id
      && result.item.status === (pending.action === 'refund.cancel' ? 'cancelled' : 'refunded'),
    onSaved, onRetired: onRecoveryRetired,
    resolved: item.status !== 'pending' });
  const normalMode = item.method === 'original' ? 'reconcile' : 'confirm';
  const [mode, setMode] = useState(mutation.command?.action || normalMode);
  const [evidenceId, setEvidenceId] = useState(mutation.command?.body.evidence_id || '');
  const [reference, setReference] = useState(mutation.command?.body.reference || '');
  const [executedAt, setExecutedAt] = useState(localTime(mutation.command?.body.executed_at));
  const [confirmed, setConfirmed] = useState(mutation.command?.body.confirmed === true);
  const [reason, setReason] = useState(mutation.command?.action === 'cancel' ? mutation.command.body.reason || '' : '');
  const pendingBody = mutation.command?.body, pendingAction = mutation.command?.action;
  useEffect(() => {
    if (!pendingBody) return;
    setMode(pendingAction); setEvidenceId(pendingBody.evidence_id || ''); setReference(pendingBody.reference || '');
    setExecutedAt(localTime(pendingBody.executed_at)); setConfirmed(pendingBody.confirmed === true);
    setReason(pendingAction === 'cancel' ? pendingBody.reason || '' : '');
  }, [pendingBody, pendingAction]);
  const active = context.status === 'active';
  const canConfirm = active && item.method === 'bank' && item.status === 'pending' && item.can_confirm === true && operations.has('customer.refund_execute');
  const evidence = Array.isArray(item.provider_evidence) ? item.provider_evidence : [];
  const canReconcile = active && item.method === 'original' && item.status === 'pending' && item.can_reconcile === true
    && evidence.length > 0 && operations.has('customer.refund_execute');
  const canCancel = active && item.status === 'pending' && item.can_cancel === true;
  const disabled = mutation.busy || Boolean(mutation.command) || mutation.blocked || mutation.recovering;
  async function save(event) {
    event.preventDefault();
    const action = mutation.command?.action || mode;
    if (action === 'confirm' && !canConfirm || action === 'cancel' && !canCancel || action === 'reconcile' && !canReconcile) return;
    await mutation.submit(action, () => {
      if (action === 'cancel') {
        if (!reason.trim()) throw { localMessage: 'اكتب سبب إلغاء طلب الاسترجاع.' };
        return { reason: reason.trim() };
      }
      if (action === 'reconcile') {
        const selected = evidence.find(value => value.id === evidenceId);
        if (!selected?.fingerprint) throw { localMessage: 'اختر تأكيد المنصة المطابق لهذا الاسترجاع.' };
        return { evidence_id: selected.id, expected_evidence_hash: selected.fingerprint };
      }
      const date = new Date(executedAt);
      if (!confirmed) throw { localMessage: 'أكد أن المبلغ قد رُد فعليًا قبل تسجيل التنفيذ.' };
      if (!executedAt || Number.isNaN(date.getTime()) || date.getTime() > Date.now() + 60000) throw { localMessage: 'اختر وقت التنفيذ الفعلي، دون تاريخ مستقبلي.' };
      return { reference: reference.trim(), executed_at: date.toISOString(), confirmed: true };
    });
  }
  return <section className="sb-refund-detail"><button type="button" className="sb-text" onClick={onBack}>العودة إلى سجل الاسترجاع</button><h2>استرجاع طلب {item.order_number}</h2>
    <p className="sb-notice" role="status">{statusLabel(item.status)}</p><dl className="sb-order-totals">
      <div><dt>المبلغ</dt><dd>{money(item.amount)} {unit(item.currency)}</dd></div><div><dt>طريقة الاسترجاع</dt><dd>{methodLabel(item)}</dd></div>
      {item.method === 'bank' && <div><dt>بنك المؤسسة</dt><dd>{item.source?.name || 'غير متاح'}</dd></div>}
      <div><dt>السبب</dt><dd>{item.reason}</dd></div><div><dt>سجّلها</dt><dd>{item.recorded_by?.name || 'غير موثق'}</dd></div>
      <div><dt>التاريخ</dt><dd>{dateLabel(item.created_at)}</dd></div>{item.request_reference && <div><dt>مرجع الطلب</dt><dd>{item.request_reference}</dd></div>}
      {item.reference && <div><dt>مرجع التنفيذ</dt><dd>{item.reference}</dd></div>}
      {item.executed_at && <div><dt>تاريخ التنفيذ</dt><dd>{dateLabel(item.executed_at)}</dd></div>}
      {item.confirmed_by?.name && <div><dt>سجّل التأكيد</dt><dd>{item.confirmed_by.name}</dd></div>}
      {item.internal_confirmation_id && <div><dt>مرجع التسجيل الداخلي</dt><dd>{item.internal_confirmation_id}</dd></div>}
    </dl>
    {item.method === 'original' && item.status === 'pending' && <p className="sb-small-note">{canReconcile ? `وصل تأكيد ${item.source?.name || 'مزود الدفع'} ويمكن مطابقته مع الطلب.` : `بانتظار تأكيد ${item.source?.name || 'مزود الدفع'}. لا يخصم هذا الطلب بنك المؤسسة.`}</p>}
    <MutationStatus mutation={mutation} />
    {(canConfirm || canCancel || canReconcile) && <form onSubmit={save} noValidate>
      {mode === 'cancel' ? <><h3>إلغاء طلب الاسترجاع</h3><Field label="سبب الإلغاء"><textarea value={reason} rows="2" disabled={disabled} onChange={event => setReason(event.target.value)} required /></Field><p className="sb-small-note">يفك الحجز عن المبلغ دون حركة بنكية.</p></>
        : canReconcile ? <><h3>مطابقة تأكيد المنصة</h3><p className="sb-small-note">اربط الطلب بتأكيد الاسترجاع الموجود في سجل المنصة، دون تسجيل دفع جديد.</p><Field label="تأكيد المنصة"><select value={evidenceId} disabled={disabled} onChange={event => setEvidenceId(event.target.value)} required><option value="">اختر التأكيد المطابق</option>{evidence.map(value => <option key={value.id} value={value.id}>{value.reference || value.id} · {money(value.amount)} {unit(item.currency)}</option>)}</select></Field></>
          : canConfirm ? <><h3>تسجيل رد المبلغ</h3><p className="sb-small-note">يوثّق هذا الإجراء تحويلًا تم بالفعل من البنك المحدد للعميل.</p><Field label="وقت التنفيذ الفعلي (بتوقيت جهازك)"><input type="datetime-local" value={executedAt} disabled={disabled} onChange={event => setExecutedAt(event.target.value)} required /></Field><Field label="مرجع التحويل (اختياري)"><input value={reference} disabled={disabled} onChange={event => setReference(event.target.value)} /></Field><label className="sb-check"><input type="checkbox" checked={confirmed} disabled={disabled} onChange={event => setConfirmed(event.target.checked)} /><span>تم رد هذا المبلغ فعليًا للعميل</span></label></> : null}
      <div className="sb-actions">{(mode === 'cancel' && canCancel || mode === 'confirm' && canConfirm || mode === 'reconcile' && canReconcile) && <button type="submit" className="sb-primary" disabled={mutation.busy || mutation.blocked || mutation.recovering}>{mutation.busy ? 'جارٍ الحفظ…' : mutation.command ? 'إعادة محاولة العملية نفسها' : mode === 'cancel' ? 'حفظ إلغاء الطلب' : mode === 'reconcile' ? 'مطابقة تأكيد المنصة' : 'تسجيل رد المبلغ'}</button>}
        {canCancel && mode !== 'cancel' && !mutation.command && <button type="button" className="sb-secondary" disabled={disabled} onClick={() => setMode('cancel')}>إلغاء طلب الاسترجاع</button>}
        {mode === 'cancel' && !mutation.command && <button type="button" className="sb-text" disabled={disabled} onClick={() => setMode(normalMode)}>رجوع</button>}
      </div>
    </form>}
  </section>;
}

function RefundPages({ result, onPage }) {
  if (!result.total) return null;
  const { page, total_pages: totalPages } = result;
  const pages = [...new Set(totalPages <= 3 ? Array.from({ length: totalPages }, (_, index) => index + 1) : [1, page === 1 ? 2 : page === totalPages ? totalPages - 1 : page, totalPages])];
  return <div className="sb-pagination-wrap"><nav className="sb-pagination" aria-label="صفحات الاسترجاع"><button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>السابق</button><div>{pages.map(value => <button type="button" key={value} aria-current={value === page ? 'page' : undefined} onClick={() => onPage(value)}>{value}</button>)}</div><button type="button" disabled={page >= totalPages} onClick={() => onPage(page + 1)}>التالي</button></nav><p>{(page - 1) * BALANCE_PAGE_SIZE + 1}–{Math.min(page * BALANCE_PAGE_SIZE, result.total)} من {result.total} طلب استرجاع</p></div>;
}

export default function SimpleOrderRefunds({ context, identity, onBalancesChanged, onExit }) {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get('refund_id') || '';
  const orderNumber = params.get('order') || '';
  const page = Math.max(1, Number.parseInt(params.get('refund_page') || '1', 10) || 1);
  const [query, setQuery] = useState(orderNumber);
  const operations = permittedOperations(context);
  const allowed = operations.has('customer.refund_request') || operations.has('customer.refund_execute');
  const scopeKey = JSON.stringify([identity, context.session_scope, context.permission_version]);
  const go = values => setParams({ tab: 'parties', panel: 'refunds', ...Object.fromEntries(Object.entries(values).filter(([, value]) => value !== '' && value != null)) });
  const orderRead = useBalanceRead(async config => {
    const value = await api.refundOrder(orderNumber, config);
    if (!value?.order_number || !Array.isArray(value.methods) || !Array.isArray(value.banks)
      || ['paid_amount', 'refunded_amount', 'reserved_amount', 'available_amount'].some(key => minorUnits(value[key]) === null)) throw new Error('invalid refund order');
    return value;
  }, JSON.stringify([scopeKey, 'order', orderNumber]), allowed && Boolean(orderNumber) && !selectedId);
  const list = useBalanceRead(async config => {
    const value = await api.refunds({ page, page_size: BALANCE_PAGE_SIZE }, config);
    if (!isPage(value, Math.min(page, value?.total_pages))) throw new Error('invalid refund list');
    return value;
  }, JSON.stringify([scopeKey, 'list', page]), allowed && !selectedId);
  const detail = useBalanceRead(async config => {
    const value = await api.refund(selectedId, config);
    if (value?.id !== selectedId) throw new Error('wrong refund result');
    return value;
  }, JSON.stringify([scopeKey, 'detail', selectedId]), allowed && Boolean(selectedId));
  if (!allowed) return <p className="sb-notice sb-error" role="alert">عرض طلبات الاسترجاع غير متاح ضمن صلاحياتك.</p>;
  const saved = item => { go({ refund_id: item.id, order: item.order_number }); detail.reload(); list.reload(); orderRead.reload(); if (item.status === 'refunded') onBalancesChanged(); };
  const recoveryRetired = () => { detail.reload(); list.reload(); orderRead.reload(); onBalancesChanged(); };
  if (selectedId) return <>{detail.loading && <p role="status">جارٍ تحميل طلب الاسترجاع…</p>}{detail.error && <><p className="sb-notice sb-error" role="alert">{detail.error}</p><button type="button" className="sb-secondary" onClick={detail.reload}>إعادة التحميل</button></>}{detail.data && <RefundDetails key={JSON.stringify([scopeKey, detail.data.id, detail.data.status])} context={context} item={detail.data} onSaved={saved} onRecoveryRetired={recoveryRetired} onBack={() => go({ order: detail.data.order_number })} />}</>;
  return <section><button type="button" className="sb-text" onClick={onExit}>العودة إلى الأطراف</button><div className="sb-title"><h1>الطلبات ورد المبالغ</h1><p>ابحث برقم الطلب، دون الحاجة إلى معرفة اسم العميل.</p></div>
    <form className="sb-search" onSubmit={event => { event.preventDefault(); if (query.trim()) go({ order: query.trim() }); }}><Field label="رقم الطلب"><input value={query} onChange={event => setQuery(event.target.value)} inputMode="numeric" placeholder="اكتب رقم الطلب" required /></Field><button className="sb-primary" type="submit">عرض الطلب</button></form>
    {orderRead.loading && <p role="status">جارٍ البحث عن الطلب…</p>}{orderRead.error && <><p className="sb-notice sb-error" role="alert">{orderRead.error}</p><button type="button" className="sb-secondary" onClick={orderRead.reload}>إعادة البحث</button></>}
    {orderRead.data && <RefundRequestForm key={JSON.stringify([scopeKey, orderRead.data.id, orderRead.data.available_amount, orderRead.data.reserved_amount])} context={context} order={orderRead.data} onSaved={saved} onRecoveryRetired={recoveryRetired} />}
    <div className="sb-section-title"><h2>سجل الاسترجاع</h2><button type="button" className="sb-text" onClick={list.reload}>تحديث السجل</button></div>
    {list.loading && <p role="status">جارٍ تحميل السجل…</p>}{list.error && <p className="sb-notice sb-error" role="alert">{list.error}</p>}
    {list.data && <>{!list.data.items.length ? <p className="sb-empty">لا توجد طلبات استرجاع ضمن السجل المتاح لك.</p> : <div className="sb-table-wrap"><table className="sb-ledger"><thead><tr><th scope="col">الطلب</th><th scope="col">المبلغ</th><th scope="col">الحالة</th></tr></thead><tbody>{list.data.items.map(item => <tr key={item.id}><td><button type="button" className="sb-row-button" onClick={() => go({ refund_id: item.id, order: item.order_number })}><strong>طلب {item.order_number}</strong><span>{methodLabel(item)} · {dateLabel(item.created_at)}</span><small>سجّلها: {item.recorded_by?.name || 'غير موثق'}</small></button></td><td>{money(item.amount)}<small className="sb-currency">{item.currency || 'SAR'}</small></td><td>{statusLabel(item.status)}</td></tr>)}</tbody></table></div>}<RefundPages result={list.data} onPage={number => go({ order: orderNumber, refund_page: number })} /></>}
  </section>;
}
