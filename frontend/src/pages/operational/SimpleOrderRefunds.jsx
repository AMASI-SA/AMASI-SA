import React, { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { simpleBalancesApi as api, simpleBalanceError } from './simpleBalancesApi';
import { useBalanceRead } from './simpleBalancesHooks';
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

function useRefundMutation({ context, identity, validate, execute, onSaved, resolved = false }) {
  const storageKey = `mezan.simple-balances.refund.v1:${context.actor.id}:${context.session_scope}:${identity}`;
  const [restored] = useState(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return {};
      if (resolved) { localStorage.removeItem(storageKey); return {}; }
      const command = JSON.parse(raw);
      if (!command?.body?.request_id || command.body.expected_session_scope !== context.session_scope || !validate(command)) throw new Error('invalid refund command');
      return { command };
    } catch {
      return { error: 'تعذر قراءة العملية المحفوظة. راجع سجل الاسترجاع قبل إنشاء عملية جديدة.' };
    }
  });
  const [command, setCommand] = useState(restored.command || null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(restored.error || '');
  const lock = useRef(false), completed = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function submit(action, makeBody) {
    if (lock.current || completed.current || restored.error) return;
    lock.current = true; setBusy(true); setError('');
    try {
      let pending = command;
      if (!pending) {
        const body = { ...makeBody(), request_id: globalThis.crypto.randomUUID(), expected_session_scope: context.session_scope };
        pending = { action, body };
        try { localStorage.setItem(storageKey, JSON.stringify(pending)); }
        catch { throw { localMessage: 'تعذر حفظ مرجع العملية لإعادة المحاولة الآمنة. تحقق من إعدادات التخزين ثم أعد المحاولة.' }; }
        setCommand(pending);
      }
      const result = await execute(pending);
      if (result?.saved !== true || !result.item?.id) throw { localMessage: 'لم تتأكد نتيجة الحفظ. أعد محاولة العملية نفسها للتحقق دون تكرارها.' };
      completed.current = true;
      localStorage.removeItem(storageKey);
      if (mounted.current) { setCommand(null); onSaved(result.item); }
    } catch (failure) {
      if (failure?.response?.data?.detail?.not_applied === true || [400, 422].includes(failure?.response?.status)) {
        localStorage.removeItem(storageKey);
        if (mounted.current) setCommand(null);
      }
      if (mounted.current) setError(simpleBalanceError(failure));
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return { command, busy, error, blocked: Boolean(restored.error), submit };
}

function MutationStatus({ mutation }) {
  return <>{mutation.error && <p className="sb-notice sb-error" role="alert">{mutation.error}</p>}{mutation.command && <p className="sb-notice" role="status">هناك عملية بانتظار تأكيد النتيجة. إعادة المحاولة تستخدم المرجع نفسه.</p>}</>;
}

export function RefundRequestForm({ context, order, onSaved }) {
  const operations = permittedOperations(context);
  const permitted = context.status === 'active' && operations.has('customer.refund_request');
  const allowed = permitted && order.can_request === true;
  const mutation = useRefundMutation({ context, identity: `order:${order.id || order.order_number}`,
    validate: value => value.action === 'create' && value.body.order_number === order.order_number,
    execute: value => api.requestRefund(value.body), onSaved });
  const [mode, setMode] = useState('partial');
  const [form, setForm] = useState(() => ({ amount: mutation.command?.body.amount || '', method: mutation.command?.body.method || '',
    bank_id: mutation.command?.body.bank_id || '', reason: mutation.command?.body.reason || '', reference: mutation.command?.body.reference || '' }));
  const disabled = mutation.busy || Boolean(mutation.command) || mutation.blocked || !allowed;
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
  return <section className="sb-refund-create"><div className="sb-title"><h2>طلب {order.order_number}</h2><p>الاسترجاع من المبلغ المحصّل المؤكد</p></div>
    <dl className="sb-order-totals">{[['المدفوع المؤكد', order.paid_amount], ['سبق رده', order.refunded_amount], ['بانتظار الرد', order.reserved_amount], ['المتاح للاسترجاع', order.available_amount]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd><bdi>{money(value)}</bdi> {unit(order.currency)}</dd></div>)}</dl>
    {!canRequestAmount && <p className="sb-notice">لا يوجد مبلغ متاح لاسترجاع جديد من هذا الطلب.</p>}
    {!allowed && <p className="sb-small-note">يمكنك مراجعة الطلب. إنشاء طلب استرجاع غير متاح ضمن صلاحياتك الحالية.</p>}
    {(allowed && canRequestAmount || mutation.command) && <form onSubmit={submit} noValidate><MutationStatus mutation={mutation} />
      <div className="sb-refund-modes"><button type="button" className="sb-secondary" aria-pressed={mode === 'partial'} disabled={disabled} onClick={() => setMode('partial')}>رد جزئي</button><button type="button" className="sb-secondary" aria-pressed={mode === 'full'} disabled={disabled} onClick={() => { setMode('full'); choose({ amount: order.available_amount }); }}>رد كامل المتاح</button></div>
      <Field label="المبلغ المسترجع"><input inputMode="decimal" dir="ltr" value={mode === 'full' ? order.available_amount : form.amount} disabled={disabled || mode === 'full'} onChange={event => choose({ amount: event.target.value })} required /></Field>
      <Field label="طريقة الاسترجاع"><select value={form.method} disabled={disabled} onChange={event => choose({ method: event.target.value })} required><option value="">اختر طريقة الاسترجاع</option>{(order.methods || []).map(method => <option key={method.id} value={method.id}>{method.label}</option>)}</select></Field>
      {form.method === 'bank' && <Field label="بنك المؤسسة الذي سيخرج منه المبلغ"><select value={form.bank_id} disabled={disabled} onChange={event => choose({ bank_id: event.target.value })} required><option value="">اختر البنك</option>{(order.banks || []).map(bank => <option key={bank.id} value={bank.id}>{bank.name}</option>)}</select></Field>}
      <Field label="سبب الاسترجاع"><textarea rows="2" maxLength="1000" value={form.reason} disabled={disabled} onChange={event => choose({ reason: event.target.value })} required /></Field>
      <Field label="مرجع العملية (اختياري)"><input value={form.reference} disabled={disabled} onChange={event => choose({ reference: event.target.value })} /></Field>
      <p className="sb-small-note">حفظ الطلب يحجز المبلغ. يُسجّل خروجه عند توثيق ردّه بالفعل.</p>
      <button className="sb-primary sb-submit" type="submit" disabled={mutation.busy || mutation.blocked || !(mutation.command ? permitted : allowed)}>{mutation.busy ? 'جارٍ حفظ الطلب…' : mutation.command ? 'إعادة محاولة العملية نفسها' : 'حفظ طلب الاسترجاع'}</button>
    </form>}
  </section>;
}

export function RefundDetails({ context, item, onSaved, onBack }) {
  const operations = permittedOperations(context);
  const mutation = useRefundMutation({ context, identity: `item:${item.id}`,
    validate: value => ['confirm', 'cancel', 'reconcile'].includes(value.action),
    execute: value => value.action === 'confirm' ? api.confirmRefund(item.id, value.body)
      : value.action === 'reconcile' ? api.reconcileRefund(item.id, value.body) : api.cancelRefund(item.id, value.body), onSaved,
    resolved: item.status !== 'pending' });
  const normalMode = item.method === 'original' ? 'reconcile' : 'confirm';
  const [mode, setMode] = useState(mutation.command?.action || normalMode);
  const [evidenceId, setEvidenceId] = useState(mutation.command?.body.evidence_id || '');
  const [reference, setReference] = useState(mutation.command?.body.reference || '');
  const [executedAt, setExecutedAt] = useState(localTime(mutation.command?.body.executed_at));
  const [confirmed, setConfirmed] = useState(mutation.command?.body.confirmed === true);
  const [reason, setReason] = useState(mutation.command?.action === 'cancel' ? mutation.command.body.reason || '' : '');
  const active = context.status === 'active';
  const canConfirm = active && item.method === 'bank' && item.status === 'pending' && item.can_confirm === true && operations.has('customer.refund_execute');
  const evidence = Array.isArray(item.provider_evidence) ? item.provider_evidence : [];
  const canReconcile = active && item.method === 'original' && item.status === 'pending' && item.can_reconcile === true
    && evidence.length > 0 && operations.has('customer.refund_execute');
  const canCancel = active && item.status === 'pending' && item.can_cancel === true;
  const disabled = mutation.busy || Boolean(mutation.command) || mutation.blocked;
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
      <div className="sb-actions">{(mode === 'cancel' && canCancel || mode === 'confirm' && canConfirm || mode === 'reconcile' && canReconcile) && <button type="submit" className="sb-primary" disabled={mutation.busy || mutation.blocked}>{mutation.busy ? 'جارٍ الحفظ…' : mutation.command ? 'إعادة محاولة العملية نفسها' : mode === 'cancel' ? 'حفظ إلغاء الطلب' : mode === 'reconcile' ? 'مطابقة تأكيد المنصة' : 'تسجيل رد المبلغ'}</button>}
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
  if (selectedId) return <>{detail.loading && <p role="status">جارٍ تحميل طلب الاسترجاع…</p>}{detail.error && <><p className="sb-notice sb-error" role="alert">{detail.error}</p><button type="button" className="sb-secondary" onClick={detail.reload}>إعادة التحميل</button></>}{detail.data && <RefundDetails key={JSON.stringify([scopeKey, detail.data.id, detail.data.status])} context={context} item={detail.data} onSaved={saved} onBack={() => go({ order: detail.data.order_number })} />}</>;
  return <section><button type="button" className="sb-text" onClick={onExit}>العودة إلى الأطراف</button><div className="sb-title"><h1>الطلبات ورد المبالغ</h1><p>ابحث برقم الطلب، دون الحاجة إلى معرفة اسم العميل.</p></div>
    <form className="sb-search" onSubmit={event => { event.preventDefault(); if (query.trim()) go({ order: query.trim() }); }}><Field label="رقم الطلب"><input value={query} onChange={event => setQuery(event.target.value)} inputMode="numeric" placeholder="اكتب رقم الطلب" required /></Field><button className="sb-primary" type="submit">عرض الطلب</button></form>
    {orderRead.loading && <p role="status">جارٍ البحث عن الطلب…</p>}{orderRead.error && <><p className="sb-notice sb-error" role="alert">{orderRead.error}</p><button type="button" className="sb-secondary" onClick={orderRead.reload}>إعادة البحث</button></>}
    {orderRead.data && <RefundRequestForm key={JSON.stringify([scopeKey, orderRead.data.id, orderRead.data.available_amount, orderRead.data.reserved_amount])} context={context} order={orderRead.data} onSaved={saved} />}
    <div className="sb-section-title"><h2>سجل الاسترجاع</h2><button type="button" className="sb-text" onClick={list.reload}>تحديث السجل</button></div>
    {list.loading && <p role="status">جارٍ تحميل السجل…</p>}{list.error && <p className="sb-notice sb-error" role="alert">{list.error}</p>}
    {list.data && <>{!list.data.items.length ? <p className="sb-empty">لا توجد طلبات استرجاع ضمن السجل المتاح لك.</p> : <div className="sb-table-wrap"><table className="sb-ledger"><thead><tr><th scope="col">الطلب</th><th scope="col">المبلغ</th><th scope="col">الحالة</th></tr></thead><tbody>{list.data.items.map(item => <tr key={item.id}><td><button type="button" className="sb-row-button" onClick={() => go({ refund_id: item.id, order: item.order_number })}><strong>طلب {item.order_number}</strong><span>{methodLabel(item)} · {dateLabel(item.created_at)}</span><small>سجّلها: {item.recorded_by?.name || 'غير موثق'}</small></button></td><td>{money(item.amount)}<small className="sb-currency">{item.currency || 'SAR'}</small></td><td>{statusLabel(item.status)}</td></tr>)}</tbody></table></div>}<RefundPages result={list.data} onPage={number => go({ order: orderNumber, refund_page: number })} /></>}
  </section>;
}
