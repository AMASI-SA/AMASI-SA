import React, { useEffect, useRef, useState } from 'react';
import { operationalApi as api, messageFor, requestId } from './api';
import { todaySA } from '../../lib/dates';

const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const day = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);
const money = value => typeof value === 'string' && /^-?\d+\.\d{2}$/.test(value);
const keys = (value, names) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join(',') === [...names].sort().join(',');
const reasons = {
  ad_correction_saved_closed_day_required: 'لا يوجد استحقاق مغلق محفوظ لهذا اليوم.',
  ad_correction_native_day_missing_or_duplicated: 'مصدر اليوم غير متاح أو يحمل سجلات متعارضة.',
  ad_correction_close_proof_incomplete: 'دليل إغلاق اليوم غير مكتمل.',
  ad_correction_native_observation_stale: 'المصدر أقدم من الاستحقاق المحفوظ.',
  ad_correction_saved_parts_incomplete: 'أجزاء الاستحقاق المحفوظ تحتاج إلى مراجعة.',
  ad_correction_saved_contract_changed: 'تغيرت هوية الاستحقاق أو العملة أو إعدادات التمويل.',
  ad_correction_fx_basis_changed: 'تغير دليل تحويل العملة؛ يلزم مراجعته منفصلًا.',
  ad_correction_fx_evidence_incomplete: 'دليل سعر الصرف أو قيمة الريال غير مكتمل؛ لا يمكن اعتماد التصحيح.',
};
const rejected = new Set(['ad_correction_preview_stale', 'ad_correction_source_changed',
  'ad_correction_review_required', 'ad_correction_selection_invalid', 'ad_correction_period_invalid']);

function validIntent(value, scope) {
  return keys(value, ['selection', 'days', 'expected_revision', 'expected_preview_hash', 'expected_session_scope',
    'request_id', 'reason', 'confirm_reviewed'])
    && keys(value.selection, ['account_id', 'date_from', 'date_to'])
    && typeof value.selection.account_id === 'string' && value.selection.account_id.length > 0
    && day(value.selection.date_from) && day(value.selection.date_to)
    && Array.isArray(value.days) && value.days.length > 0 && value.days.length <= 31
    && new Set(value.days).size === value.days.length && value.days.every(day)
    && Number.isInteger(value.expected_revision) && value.expected_revision > 0
    && hash(value.expected_preview_hash) && value.expected_session_scope === scope
    && typeof value.request_id === 'string' && /^[A-Za-z0-9_-]{8,100}$/.test(value.request_id)
    && typeof value.reason === 'string' && value.reason.trim().length >= 5 && value.reason.length <= 500
    && value.confirm_reviewed === true;
}

function checkedPreview(value, scope, selection) {
  if (!value || value.schema !== 'operational_ad_correction_preview_v1' || value.session_scope !== scope
      || value.read_only !== true || value.posting_allowed !== false || value.source_completeness_verified !== false
      || !hash(value.preview_hash) || !Number.isInteger(value.revision) || value.revision < 1
      || !value.selection || ['account_id', 'date_from', 'date_to'].some(key => value.selection[key] !== selection[key])
      || !Array.isArray(value.items) || value.items.length < 1 || value.items.length > 31
      || new Set(value.items.map(item => item.business_date)).size !== value.items.length
      || value.items.some(item => !item || !day(item.business_date) || !['blocked', 'unchanged', 'correction_required'].includes(item.status)
        || item.can_approve !== (item.status === 'correction_required') || !Array.isArray(item.parts)
        || !Array.isArray(item.issues) || item.status !== 'blocked' && (!money(item.before_amount)
          || !money(item.after_amount) || !money(item.difference) || item.parts.length < 1 || item.parts.length > 2
          || item.parts.some(part => typeof part.id !== 'string' || !money(part.before) || !money(part.after) || !money(part.difference)
            || part.sar_difference != null && (!money(part.before_sar) || !money(part.after_sar) || !money(part.sar_difference)))))) {
    throw new Error('invalid_ad_correction_preview');
  }
  return value;
}

function checkedReceipt(value, intent) {
  if (!value || value.schema !== 'operational_ad_correction_receipt_v1'
      || value.request_id !== intent.request_id || value.preview_hash !== intent.expected_preview_hash
      || value.account_id !== intent.selection.account_id || value.manual_history_preserved !== true
      || value.native_sources_changed !== false || !Array.isArray(value.days)
      || [...value.days.map(item => item.business_date)].sort().join(',') !== [...intent.days].sort().join(',')) {
    throw new Error('invalid_ad_correction_receipt');
  }
  return value;
}

export default function AdSpendCorrections({ context, onSaved }) {
  const scope = context?.session_scope;
  const owner = context?.actor?.is_owner === true && Boolean(context.actor.id && hash(scope));
  const identity = JSON.stringify([context?.actor?.id, scope, context?.permission_version]);
  const storageKey = JSON.stringify(['operational-ad-correction', context?.actor?.id, scope]);
  const currentIdentity = useRef(identity), active = useRef(null);
  currentIdentity.current = identity;
  const [stateIdentity, setStateIdentity] = useState(identity);
  const [expanded, setExpanded] = useState(false), [accounts, setAccounts] = useState([]);
  const [form, setForm] = useState({ account_id: '', date_from: todaySA(), date_to: todaySA() });
  const [preview, setPreview] = useState(null), [days, setDays] = useState([]), [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false), [pending, setPending] = useState(null);
  const [receipt, setReceipt] = useState(null), [busy, setBusy] = useState(false), [message, setMessage] = useState('');

  function storePending(value) {
    if (value) localStorage.setItem(storageKey, JSON.stringify(value));
    else localStorage.removeItem(storageKey);
    setPending(value);
  }
  function complete(value, intent) {
    const saved = checkedReceipt(value, intent);
    try { storePending(null); } catch (_) { setPending(null); }
    setReceipt(saved); setPreview(null); setConfirmed(false); setDays([]);
    setMessage('حُفظ التصحيح على الأيام الأصلية. راجع كشف الحساب وإيصال الاستعادة للتحقق من الأثر.');
    onSaved?.();
  }
  async function run(stage, operation) {
    if (!owner || stateIdentity !== identity || active.current || document.visibilityState === 'hidden' || navigator.onLine === false) return;
    const controller = new AbortController(), binding = identity;
    active.current = { controller, stage }; setBusy(true); setMessage('');
    const current = () => !controller.signal.aborted && currentIdentity.current === binding;
    try {
      await operation({ signal: controller.signal }, current);
    } catch (error) {
      if (current()) {
        const code = error?.response?.data?.detail?.code;
        if (stage === 'write' && rejected.has(code)) {
          try { storePending(null); } catch (_) { /* Keep a safe repeat reference if storage is unavailable. */ }
          setPreview(null); setConfirmed(false); setDays([]);
        }
        setMessage(stage === 'write' && !rejected.has(code)
          ? 'لم تتأكد نتيجة الاعتماد. تحقق من الإيصال، أو أعد إرسال نفس الاعتماد دون تغيير مرجعه.' : messageFor(error));
      }
    } finally {
      if (active.current?.controller === controller) active.current = null;
      if (current()) setBusy(false);
    }
  }
  function loadAccounts() {
    run('read', async (config, current) => {
      const response = await api.entities('ad_account', config);
      if (!current()) return;
      if (!Array.isArray(response?.items) || response.items.some(item => typeof item.id !== 'string' || typeof item.name !== 'string')) {
        throw new Error('invalid_ad_correction_accounts');
      }
      setAccounts(response.items);
    });
  }
  useEffect(() => {
    setAccounts([]); setPreview(null); setReceipt(null); setDays([]); setConfirmed(false); setReason(''); setBusy(false);
    setStateIdentity(identity);
    setForm({ account_id: '', date_from: todaySA(), date_to: todaySA() }); setMessage(''); setExpanded(false);
    if (owner) {
      try {
        const saved = localStorage.getItem(storageKey);
        const value = saved ? JSON.parse(saved) : null;
        if (value && !validIntent(value, scope)) throw new Error('invalid_saved_review');
        setPending(value);
      } catch (_) {
        setPending(null); setMessage('تعذر قراءة مرجع الاعتماد السابق. تحقق من إيصالات الأرصدة قبل إنشاء اعتماد جديد.');
      }
    } else setPending(null);
    const hide = () => {
      if (document.visibilityState === 'hidden' || navigator.onLine === false) {
        if (active.current?.stage === 'write') setMessage('لم تتأكد نتيجة الاعتماد. تحقق من الإيصال قبل إنشاء اعتماد آخر.');
        active.current?.controller.abort(); active.current = null; setBusy(false);
        setPreview(null); setConfirmed(false); setDays([]);
      }
    };
    document.addEventListener('visibilitychange', hide); window.addEventListener('offline', hide);
    return () => {
      active.current?.controller.abort(); active.current = null;
      document.removeEventListener('visibilitychange', hide); window.removeEventListener('offline', hide);
    };
    // A changed actor/session/grant cancels every old response and never posts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, owner]);

  function show() {
    if (expanded) { active.current?.controller.abort(); active.current = null; setBusy(false); setPreview(null); setConfirmed(false); }
    setExpanded(!expanded);
    if (!expanded && !accounts.length) loadAccounts();
  }
  function change(field, value) {
    setForm(current => ({ ...current, [field]: value })); setPreview(null); setConfirmed(false); setDays([]); setReceipt(null);
  }
  function previewDays(event) {
    event.preventDefault();
    if (pending || context.status !== 'active') return;
    const selection = { ...form };
    setPreview(null); setDays([]); setConfirmed(false); setReceipt(null);
    run('read', async (config, current) => {
      const value = await api.adCorrectionPreview({ ...selection, expected_session_scope: scope }, config);
      if (current()) setPreview(checkedPreview(value, scope, selection));
    });
  }
  function send(intent) {
    if (!validIntent(intent, scope) || context.status !== 'active') return;
    run('write', async (config, current) => {
      const value = await api.refreshBalances({ ...config, expectedSessionScope: scope, adCorrectionApproval: intent });
      if (current()) {
        if (value?.session_scope !== scope) throw new Error('invalid_ad_correction_scope');
        complete(value.ad_correction, intent);
      }
    });
  }
  function approve() {
    if (!preview || !confirmed || !days.length || reason.trim().length < 5 || pending) return;
    const intent = { selection: preview.selection, days: [...days].sort(), expected_revision: preview.revision,
      expected_preview_hash: preview.preview_hash, expected_session_scope: scope, request_id: requestId(),
      reason: reason.trim(), confirm_reviewed: true };
    try { storePending(intent); } catch (_) { setMessage('تعذر حفظ مرجع الاعتماد؛ لم يُرسل التصحيح.'); return; }
    send(intent);
  }
  function checkReceipt() {
    if (!pending) return;
    const intent = pending;
    run('read', async (config, current) => {
      const value = await api.adCorrectionReceipt(intent.request_id, {
        expected_session_scope: scope, expected_preview_hash: intent.expected_preview_hash,
      }, config);
      if (!current()) return;
      if (value?.schema !== 'operational_ad_correction_status_v1' || value.session_scope !== scope || value.request_id !== intent.request_id) {
        throw new Error('invalid_ad_correction_status');
      }
      if (value.status === 'applied') complete(value.receipt, intent);
      else if (value.status === 'not_recorded') setMessage('لا يظهر إيصال محفوظ لهذا الاعتماد حتى الآن. يمكنك إعادة إرسال نفس الاعتماد؛ يظل المرجع نفسه لمنع التكرار.');
      else throw new Error('invalid_ad_correction_status');
    });
  }
  if (!owner) return null;
  if (stateIdentity !== identity) return <section className="panel sheet"><p role="status">جارٍ التحقق من حساب المالك…</p></section>;
  const locked = busy || Boolean(pending) || context.status !== 'active';
  return <section className="panel sheet" aria-label="تصحيح إنفاق الإعلانات المغلق">
    <h2>تصحيح إنفاق الأيام الإعلانية المغلقة</h2>
    <p>راجع الفرق الذي أبلغت به المنصة بعد إغلاق اليوم، ثم اعتمد الأيام المحددة لتحديث استحقاقاتها الأصلية.</p>
    <button type="button" aria-expanded={expanded} onClick={show}>{expanded ? 'إغلاق مراجعة الإعلان' : 'مراجعة إنفاق الأيام المغلقة'}</button>
    {expanded && <>
      {pending && <div className="note warning" role="status"><p>يوجد اعتماد لم تتأكد نتيجته. المرجع: <bdi>{pending.request_id}</bdi></p>
        <button type="button" disabled={busy} onClick={checkReceipt}>التحقق من إيصال الاعتماد</button>
        <button type="button" disabled={busy || context.status !== 'active'} onClick={() => send(pending)}>إعادة إرسال نفس الاعتماد</button></div>}
      <form onSubmit={previewDays}><fieldset disabled={locked}>
        <label>الحساب الإعلاني<select value={form.account_id} onChange={event => change('account_id', event.target.value)} required>
          <option value="">اختر الحساب</option>{accounts.map(account => <option key={account.id} value={account.id}>{account.name} · {account.currency || '—'}</option>)}
        </select></label>
        <label>من تاريخ<input type="date" required value={form.date_from} onChange={event => change('date_from', event.target.value)}/></label>
        <label>إلى تاريخ<input type="date" required value={form.date_to} onChange={event => change('date_to', event.target.value)}/></label>
        <p>حد المعاينة ٣١ يومًا لكل حساب. اليوم المفتوح أو الدليل غير المكتمل لا يقبل التصحيح.</p>
        <button type="submit" disabled={!form.account_id}>معاينة الفروق من المصدر</button>
      </fieldset></form>
      {!accounts.length && !busy && <button type="button" onClick={loadAccounts}>إعادة قراءة الحسابات</button>}
      {preview && <><p>لقطة الأرصدة: <bdi>{preview.as_of || '—'}</bdi>. اختر الأيام التي راجعت فروقها:</p>
        <div className="table-wrap"><table><thead><tr><th>اعتماد</th><th>اليوم الأصلي</th><th>العملة</th><th>المحفوظ</th><th>المصدر</th><th>الفرق</th><th>المراجعة</th></tr></thead>
          <tbody>{preview.items.map(item => <tr key={item.business_date}>
            <td><input type="checkbox" aria-label={`اعتماد يوم ${item.business_date}`} disabled={locked || !item.can_approve}
              checked={days.includes(item.business_date)} onChange={event => setDays(current => event.target.checked
                ? [...current, item.business_date] : current.filter(value => value !== item.business_date))}/></td>
            <td><bdi>{item.business_date}</bdi></td><td>{item.currency}</td><td>{item.before_amount || '—'}</td><td>{item.after_amount || '—'}</td><td>{item.difference || '—'}</td>
            <td>{item.status === 'blocked' ? item.issues.map(code => reasons[code] || 'تعذر إثبات اكتمال دليل اليوم.').join(' ')
              : item.status === 'unchanged' ? 'الأجزاء المالية متطابقة' : <>{item.parts.map(part => <div key={part.id}>
                {part.funding_type === 'prepaid' ? 'المحفظة' : part.funding_type === 'postpaid' ? 'الائتمان' : 'إنفاق الحساب'}: {part.before} ← {part.after} ({part.difference})
                {item.currency !== 'SAR' && part.sar_difference != null && <small className="under">بالريال: {part.before_sar} ← {part.after_sar} (الفرق {part.sar_difference})</small>}
              </div>)}{item.fx_basis?.fx_rate && <small className="under">سعر الصرف المثبت: {item.fx_basis.fx_rate} ريال · بتاريخ <bdi>{item.fx_basis.fx_at || item.fx_basis.fx_date}</bdi>
                {typeof item.fx_basis.fx_evidence === 'string' && <> · المرجع: {item.fx_basis.fx_evidence}</>}</small>}</>}</td></tr>)}</tbody></table></div>
        <label>سبب الاعتماد<textarea maxLength={500} disabled={locked} value={reason} onChange={event => setReason(event.target.value)}/></label>
        <label><input type="checkbox" disabled={locked} checked={confirmed} onChange={event => setConfirmed(event.target.checked)}/>
          راجعت الحساب والأيام والمبالغ، وأعتمد تصحيح الاستحقاقات المعروضة في تواريخها الأصلية.</label>
        <button type="button" disabled={locked || !days.length || !confirmed || reason.trim().length < 5} onClick={approve}>اعتماد التصحيح وتحديث الأرصدة</button>
      </>}
    </>}
    {busy && <p role="status">{active.current?.stage === 'write' ? 'جارٍ التحقق من الاعتماد وتحديث الأرصدة…' : 'جارٍ قراءة الدليل…'}</p>}
    {message && <p role={receipt ? 'status' : 'alert'}>{message}</p>}
    {receipt && <p>إيصال اعتماد {receipt.days.length} يوم: <bdi>{receipt.request_id}</bdi> · مراجعة الاحتساب {receipt.calculation_revision}.</p>}
  </section>;
}
