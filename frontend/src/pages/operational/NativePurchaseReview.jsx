import {useEffect, useRef, useState} from 'react';
import {operationalApi as api, requestId, messageFor} from './api';
import {formatPositionMoney} from './financialPositionPresentation';
import './nativePurchaseReview.css';

const messages = {
  native_link_source_unverified: 'قيد الفاتورة أو السداد غير مكتمل الإثبات؛ لا يمكن اعتماده.',
  native_link_prestart_invoice_requires_separate_opening_review: 'هذه الفاتورة أقدم من بدء التشغيل؛ تحتاج مطابقة مستقلة مع رصيد البداية، ولا تُضاف من هنا.',
  native_link_existing_invoice_must_be_selected: 'يوجد مستند يحتمل أنه الفاتورة نفسها؛ اختر المستحق المسجل الصحيح بدل إضافة دين جديد.',
  native_link_target_amount_mismatch: 'إجمالي المستحقات المختارة لا يطابق إجمالي فاتورة المصدر.',
  native_link_existing_payment_allocation_required: 'السداد المختار غير مخصص بالكامل لهذه الفاتورة؛ لن نعيد كتابة الحركة القديمة.',
  native_link_unmatched_operational_payment: 'توجد حركة سداد مرتبطة بالمستحق ولم تُطابق مع إثبات المصدر.',
  native_link_existing_payment_must_be_selected: 'مرجع السداد موجود بالفعل؛ اختر الحركة المسجلة ولا تضفها ثانية.',
  native_link_new_or_changed_payments: 'تغيرت أدلة السداد منذ المطابقة السابقة؛ أعد مراجعة المصدر.',
  native_link_target_scope_invalid: 'هوية المستحق أو المورد أو عملته لا تطابق الفاتورة.',
};
const textFor = code => messages[code] || `تحتاج المطابقة إلى مراجعة: ${code}`;
const safeKey = scope => `mezan-native-purchase-link:${scope}`;
function pendingRequest(scope) {
  try {
    const raw = sessionStorage.getItem(safeKey(scope));
    if (raw === null) return null;
    const p = JSON.parse(raw);
    if (p?.expected_session_scope !== scope || typeof p.request_id !== 'string' ||
        !p.selection?.invoice_id || !/^[a-f0-9]{64}$/.test(p.expected_preview_hash)) throw Error();
    return p;
  } catch {
    // A corrupt/unreadable persisted intent may already have been committed.
    // Do not silently replace it with another request or an empty queue.
    return {storage_error: true};
  }
}
const money = value => typeof value === 'string' && /^-?\d+\.\d{2}$/.test(value);
function validPreview(p) {
  if (!/^[a-f0-9]{64}$/.test(p.preview_hash) || !p.before?.banks || !p.after?.banks || !p.bank_differences_sar ||
      typeof p.unrecorded_money_confirmation_required !== 'boolean' ||
      ![p.before.supplier_outstanding_sar, p.after.supplier_outstanding_sar, p.supplier_difference_sar].every(money)) return false;
  const keys = Object.keys(p.before.banks).sort();
  if (JSON.stringify(keys) !== JSON.stringify(Object.keys(p.after.banks).sort()) ||
      JSON.stringify(keys) !== JSON.stringify(Object.keys(p.bank_differences_sar).sort())) return false;
  return keys.every(k => [p.before.banks[k], p.after.banks[k], p.bank_differences_sar[k]].every(money));
}
const invalid = () => new Error('تعذر التحقق من استجابة المطابقة؛ لم نعتبرها موافقة.');
const noApply = new Set(['native_link_preview_stale', 'native_link_evidence_incomplete',
  'native_link_identity_confirmation_required', 'native_link_unrecorded_confirmation_required']);

export default function NativePurchaseReview({sessionScope, onRefresh}) {
  const [opened, setOpened] = useState(false), [list, setList] = useState(null);
  const [candidate, setCandidate] = useState(null), [selection, setSelection] = useState(null);
  const [mode, setMode] = useState(''), [preview, setPreview] = useState(null);
  const [confirmed, setConfirmed] = useState(false), [moneyConfirmed, setMoneyConfirmed] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [saved, setSaved] = useState(null);
  const [pending, setPending] = useState(() => pendingRequest(sessionScope));
  const alive = useRef(true), lock = useRef(false), scopeRef = useRef(sessionScope);
  // Parent keys this component by authenticated actor AND server session scope.
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const current = key => alive.current && key === scopeRef.current && key === sessionScope;
  const run = async fn => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    const key = sessionScope;
    try { await fn(key); }
    catch (e) {if (current(key)) setError(e.response ? messageFor(e) : e.message || 'تعذر تحميل المطابقة.');}
    finally {if (current(key)) {lock.current = false; setBusy(false);}}
  };
  async function open() {
    setOpened(true);
    if (pending) return;
    await run(async key => {
      const r = await api.supplierInvoiceReview();
      if (r.session_scope !== key || r.read_only !== true || !Array.isArray(r.native_purchase_review)) throw invalid();
      if (current(key)) setList(r.native_purchase_review);
    });
  }
  async function choose(invoiceId) {
    if (pending || busy) return;
    setCandidate(null); setPreview(null); setSaved(null); setConfirmed(false); setMoneyConfirmed(false);
    await run(async key => {
      const c = await api.nativePurchaseCandidates(invoiceId);
      if (c.session_scope !== key || c.read_only !== true || c.invoice_id !== invoiceId || !Array.isArray(c.source?.payment_journals) || !Array.isArray(c.existing_obligations) || !Array.isArray(c.existing_movements)) throw invalid();
      if (!current(key)) return;
      const link = c.saved_link;
      setCandidate(c); setMode(link ? 'existing' : '');
      setSelection({invoice_id: invoiceId, existing_obligation_ids: link?.obligation_ids || [],
        payment_links: c.source.payment_journals.map(p => ({payment_operation_id: p.payment_operation_id,
          movement_id: link?.payments?.[p.payment_operation_id]?.movement_id || ''})),
        evidence_ref: link?.evidence_ref || ''});
    });
  }
  function edit(patch) {
    if (busy || pending) return;
    setSelection(s => ({...s, ...patch})); setPreview(null); setConfirmed(false); setMoneyConfirmed(false);
  }
  const ready = candidate?.source?.source_verified === true && selection && mode &&
    (mode === 'new' || selection.existing_obligation_ids.length > 0) && selection.evidence_ref.trim().length >= 3 &&
    selection.payment_links.every(p => p.movement_id !== '');
  async function inspect() {
    if (!ready || pending) return;
    await run(async key => {
      setPreview(null); setConfirmed(false); setMoneyConfirmed(false);
      const p = await api.previewNativePurchaseLink(selection);
      if (p.session_scope !== key || p.read_only !== true || typeof p.can_approve !== 'boolean' || p.invoice_id !== selection.invoice_id) throw invalid();
      if (p.can_approve && !validPreview(p)) throw invalid();
      if (current(key)) setPreview(p);
    });
  }
  async function approve() {
    if (pending?.storage_error) return;
    if (!pending && (!preview?.can_approve || !confirmed || (preview.unrecorded_money_confirmation_required && !moneyConfirmed))) return;
    await run(async key => {
      const body = pending || {request_id: requestId(), selection: JSON.parse(JSON.stringify(selection)),
        expected_preview_hash: preview.preview_hash, expected_session_scope: key,
        confirm_reviewed_identities: true, confirm_unrecorded_money: moneyConfirmed};
      // Persist BEFORE sending. Ambiguous responses/reloads retry this same intent.
      sessionStorage.setItem(safeKey(key), JSON.stringify(body));
      if (current(key)) setPending(body);
      try {
        const r = await api.approveNativePurchaseLink(body);
        if (r.ok !== true || r.invoice_id !== body.selection.invoice_id || r.request_id !== body.request_id || !money(r.supplier_difference_sar) || r.actual_bank_transfer_executed !== false || r.native_ledger_changed !== false) throw invalid();
        if (current(key)) {
          sessionStorage.removeItem(safeKey(key)); setPending(null); setSaved(r); setPreview(null); setCandidate(null);
        }
      } catch (e) {
        if (current(key) && noApply.has(e?.response?.data?.detail?.code)) {
          sessionStorage.removeItem(safeKey(key)); setPending(null); setPreview(null); setConfirmed(false); setMoneyConfirmed(false);
        }
        throw e;
      }
    });
  }
  return <section className="ofp-table-card ofp-no-print native-purchase-review" dir="rtl" aria-label="مطابقة فواتير الشراء والسداد">
    <button className="ofp-button" type="button" disabled={busy} onClick={open}>مطابقة فواتير الشراء والسداد</button>
    {opened && <>
      <p>المعاينة لا تغيّر الأرصدة. الاعتماد يسجل الفرق التشغيلي المحدد فقط؛ لا ينشئ تحويلًا بنكيًا أو فاتورة شراء أخرى، ولا يغيّر رصيد البداية.</p>
      {error && <p role="alert">{error}</p>}
      {busy && <p role="status">جارٍ التحقق من المطابقة…</p>}
      {pending?.storage_error && <p role="alert">تعذر قراءة طلب اعتماد محفوظ. توقف الاعتماد الجديد لحين مراجعة سجل العملية؛ لا تمسح الطلب أو تعِد إدخاله.</p>}
      {pending && !pending.storage_error && <div role="alert"><p>يوجد طلب اعتماد لم يصل تأكيده. لا نفترض نجاحه أو فشله، ولا ننشئ طلبًا جديدًا. الفاتورة: {pending.selection.invoice_id}</p>
        <button type="button" disabled={busy} onClick={approve}>إعادة إرسال طلب المطابقة نفسه</button></div>}
      {saved && <div role="status"><p>تم اعتماد المطابقة التشغيلية. لم يُنفذ تحويل بنكي جديد، ولم تتغير الفاتورة الأصلية.</p>
        <p>فرق المستحق علينا: <bdi>{formatPositionMoney(saved.supplier_difference_sar)}</bdi> SAR</p>
        <button type="button" onClick={onRefresh}>تحديث التقرير بعد المطابقة</button></div>}
      {!pending && !saved && list?.length === 0 && <p>لا توجد فواتير شراء في نطاق المطابقة الحالي.</p>}
      {!pending && !saved && list && list.length > 0 && <label>الفاتورة
        <select aria-label="فاتورة الشراء للمطابقة" disabled={busy} value={candidate?.invoice_id || ''} onChange={e => e.target.value && choose(e.target.value)}>
          <option value="">اختر فاتورة — لا مطابقة تلقائية</option>
          {list.map(i => <option key={i.invoice_id} value={i.invoice_id}>{i.invoice_number || i.invoice_id} — {i.supplier_id} — {formatPositionMoney(i.source_declared_gross_sar)} SAR</option>)}
        </select></label>}
      {!pending && !saved && candidate && selection && <>
        <h3>{candidate.supplier_name}</h3>
        <p>الفاتورة المثبتة: <bdi>{formatPositionMoney(candidate.source.native_invoice_gross_sar)}</bdi> · المسدد بالمصدر: <bdi>{formatPositionMoney(candidate.source.native_settled_sar)}</bdi> · المتبقي بالمصدر: <bdi>{formatPositionMoney(candidate.source.native_outstanding_sar)}</bdi> SAR</p>
        {!candidate.source.source_verified && <p role="alert">المصدر غير مكتمل الإثبات؛ الاعتماد متوقف.</p>}
        <fieldset disabled={busy}>
          <legend>أين سُجل مستحق الفاتورة؟</legend>
          <select aria-label="طريقة ربط مستحق الفاتورة" value={mode} disabled={!!candidate.saved_link} onChange={e => {setMode(e.target.value); edit({existing_obligation_ids: []});}}>
            <option value="">اختر بعد المراجعة</option><option value="new">غير مسجل — معاينة إضافته فقط</option><option value="existing">مسجل — ربط المستحق الموجود دون إضافته</option>
          </select>
          {mode === 'existing' && candidate.existing_obligations.map(o => <label key={o.id}>
            <input type="checkbox" checked={selection.existing_obligation_ids.includes(o.id)} disabled={!!candidate.saved_link} onChange={e => edit({existing_obligation_ids: e.target.checked ? [...selection.existing_obligation_ids, o.id] : selection.existing_obligation_ids.filter(k => k !== o.id)})}/>
            {o.invoice_number || o.label || o.id} · {formatPositionMoney(o.confirmed)} SAR
          </label>)}
        </fieldset>
        <fieldset disabled={busy}>
          <legend>ربط السداد البنكي المنفذ</legend>
          {candidate.source.payment_journals.map(p => <label key={p.payment_operation_id}>
            {p.payment_operation_id} · {formatPositionMoney(p.amount_sar)} SAR · {p.bank_account_id}
            <select aria-label={`سداد ${p.payment_operation_id}`} value={selection.payment_links.find(x => x.payment_operation_id === p.payment_operation_id)?.movement_id === null ? '__new__' : selection.payment_links.find(x => x.payment_operation_id === p.payment_operation_id)?.movement_id || ''}
              onChange={e => edit({payment_links: selection.payment_links.map(x => x.payment_operation_id === p.payment_operation_id ? {...x, movement_id: e.target.value === '__new__' ? null : e.target.value} : x)})}>
              <option value="">اختر حركة السداد بعد المراجعة</option><option value="__new__">غير مسجل تشغيليًا — معاينة أثره مرة واحدة</option>
              {candidate.existing_movements.map(m => <option key={m.id} value={m.id}>{m.reference || m.id} — {formatPositionMoney(m.amount)} SAR — {m.bank_name || m.bank_id}</option>)}
            </select>
          </label>)}
          <label>مرجع مراجعتك للمستندات<input aria-label="مرجع مراجعة المطابقة" maxLength={300} value={selection.evidence_ref} onChange={e => edit({evidence_ref: e.target.value})}/></label>
        </fieldset>
        <button className="ofp-button" type="button" disabled={busy || !ready} onClick={inspect}>معاينة الفرق قبل الاعتماد</button>
      </>}
      {!pending && preview && <div aria-label="فروق المطابقة">
        {!preview.can_approve ? (preview.issues || []).map(c => <p role="alert" key={c}>{textFor(c)}</p>) : <>
          <div className="ofp-table-scroll"><table><caption>الأرصدة التشغيلية قبل الاعتماد وبعده — SAR</caption>
            <thead><tr><th>الجهة</th><th>قبل</th><th>بعد</th><th>الفرق</th></tr></thead><tbody>
              <tr><th>المستحق للمورد علينا</th><td>{formatPositionMoney(preview.before.supplier_outstanding_sar)}</td><td>{formatPositionMoney(preview.after.supplier_outstanding_sar)}</td><td>{formatPositionMoney(preview.supplier_difference_sar)}</td></tr>
              {Object.keys(preview.before.banks).map(k => <tr key={k}><th>البنك {k}</th><td>{formatPositionMoney(preview.before.banks[k])}</td><td>{formatPositionMoney(preview.after.banks[k])}</td><td>{formatPositionMoney(preview.bank_differences_sar[k])}</td></tr>)}
            </tbody></table></div>
          <label><input type="checkbox" aria-label="تأكيد هويات المطابقة" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)}/> أؤكد أن المستحقات وحركات السداد المختارة تخص فاتورة المصدر نفسها.</label>
          {preview.unrecorded_money_confirmation_required && <label><input type="checkbox" aria-label="تأكيد المبالغ غير المسجلة" checked={moneyConfirmed} disabled={busy} onChange={e => setMoneyConfirmed(e.target.checked)}/> أؤكد أن المبالغ المقترح إضافتها غير مسجلة سابقًا ولا مشمولة في رصيد البداية.</label>}
          <button className="ofp-button" type="button" disabled={busy || !confirmed || (preview.unrecorded_money_confirmation_required && !moneyConfirmed)} onClick={approve}>اعتماد الفرق التشغيلي المعروض</button>
        </>}
      </div>}
    </>}
  </section>;
}
