import React, { useEffect, useRef, useState } from 'react';
import http from '../../lib/api';
import { calculationTime } from './visibleBalanceFreshness';

const path = '/operational-balances/automation';
const timestamp = value => value === null || calculationTime(value) !== null;
function checked(value, scope) {
  if (!value || value.schema_version !== 1 || value.session_scope !== scope
      || typeof value.enabled !== 'boolean' || value.interval_seconds !== 7200
      || value.financial_coverage_verified !== false
      || !Number.isInteger(value.consecutive_failures) || value.consecutive_failures < 0
      || !['next_due_at', 'last_attempt_at', 'last_success_at'].every(key => timestamp(value[key]))) {
    throw new Error('invalid_balance_automation');
  }
  return value;
}
const showTime = value => value ? new Intl.DateTimeFormat('ar-SA', {
  timeZone: 'Asia/Riyadh', calendar: 'gregory', dateStyle: 'short', timeStyle: 'short',
}).format(new Date(value)) : 'لم تُسجّل بعد';
const elapsed = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 300000;
function AttemptDetails({ trace, error }) {
  const stages = Array.isArray(trace?.stage_timings) ? trace.stage_timings.slice(-64).filter(row =>
    row && typeof row.stage === 'string' && /^[a-z_]{1,50}$/.test(row.stage)
    && ['complete', 'cancelled', 'failed'].includes(row.outcome) && elapsed(row.duration_ms)) : [];
  const reads = Array.isArray(trace?.primary_orders_reads) ? trace.primary_orders_reads.slice(-12).filter(row =>
    row?.collection === 'unified_orders' && elapsed(row.queue_ms) && elapsed(row.budget_ms)
    && Number.isInteger(row.rows_retained) && row.rows_retained >= 0 && row.rows_retained <= 20001) : [];
  const code = typeof error === 'string' && /^operational_[a-z_]{1,100}$/.test(error) ? error : null;
  const outcome = { complete: 'اكتملت', cancelled: 'توقفت', failed: 'فشلت' };
  if (!stages.length && !reads.length && !code) return null;
  return <details><summary>تفاصيل متابعة آخر محاولة</summary>
    {code && <p>رمز المشكلة: <bdi>{code}</bdi></p>}
    {stages.length > 0 && <><p>مدد المراحل بالملي ثانية؛ تتداخل المراحل ولا تُجمع لتحديد المدة الكلية.</p>
      <div className="table-wrap"><table><thead><tr><th>المرحلة</th><th>النتيجة</th><th>المدة</th></tr></thead>
        <tbody>{stages.map((row, index) => <tr key={index}><td><bdi>{row.stage}</bdi></td><td>{outcome[row.outcome]}</td><td>{row.duration_ms}</td></tr>)}</tbody></table></div></>}
    {reads.map((row, index) => <p key={index}>قراءة الطلبات: انتظار بدء القراءة {row.queue_ms} مللي ثانية، المهلة المتاحة {row.budget_ms} مللي ثانية،
      {elapsed(row.first_row_ms) ? ` أول سجل بعد ${row.first_row_ms} مللي ثانية،` : ' لم يُثبت وصول أول سجل،'}
      {elapsed(row.read_elapsed_ms) ? ` مدة القراءة ${row.read_elapsed_ms} مللي ثانية،` : ''} السجلات المقروءة {row.rows_retained}.</p>)}
  </details>;
}

export default function BalanceAutomationSettings({ context }) {
  const scope = context?.session_scope;
  const owner = context?.actor?.is_owner === true && Boolean(context.actor.id && scope);
  const key = JSON.stringify([context?.actor?.id, scope, context?.permission_version]);
  const active = useRef(null), currentKey = useRef(key);
  const activeStage = useRef('read');
  currentKey.current = key;
  const [saved, setSaved] = useState(null), [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  const [verified, setVerified] = useState(false);
  const perform = async (enabled, identity = key) => {
    if (!owner || active.current || document.visibilityState === 'hidden' || navigator.onLine === false) return;
    const controller = new AbortController(); active.current = controller;
    activeStage.current = typeof enabled === 'boolean' ? 'write' : 'read';
    const current = () => !controller.signal.aborted && currentKey.current === identity;
    setBusy(true); setVerified(false); setMessage('');
    const config = { signal: controller.signal, timeout: 15000 };
    try {
      if (typeof enabled === 'boolean') {
        await http.post(path, { enabled, expected_session_scope: scope }, config);
        if (!current()) return;
        activeStage.current = 'confirm';
      }
      // A mutation receipt is not a saved setting or successful calculation.
      const response = await http.get(path, config);
      if (current()) {
        setSaved({ key: identity, value: checked(response.data, scope) }); setVerified(true);
      }
    } catch (_) {
      if (current()) setMessage(typeof enabled === 'boolean'
        ? 'لم تتأكد نتيجة تغيير الجدولة. تحقق من الحالة قبل إعادة المحاولة.'
        : 'تعذر قراءة حالة الجدولة. لا يُعد ذلك دليلاً على استمرار الاحتساب.');
    } finally {
      if (active.current === controller) active.current = null;
      if (current()) setBusy(false);
    }
  };
  useEffect(() => {
    if (!owner) return undefined;
    perform(undefined, key);
    const hide = () => {
      if (document.visibilityState === 'hidden' || navigator.onLine === false) {
        if (active.current && activeStage.current !== 'read') {
          setMessage('لم تتأكد نتيجة تغيير الجدولة. تحقق من الحالة قبل إعادة المحاولة.');
        }
        active.current?.abort(); active.current = null; setBusy(false);
      }
    };
    document.addEventListener('visibilitychange', hide); window.addEventListener('offline', hide);
    return () => {
      active.current?.abort(); active.current = null;
      document.removeEventListener('visibilitychange', hide); window.removeEventListener('offline', hide);
    };
    // A scope change replaces every request. Opening this panel never writes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, owner]);
  if (!owner) return null;
  const value = saved?.key === key ? saved.value : null;
  return <section className="panel sheet" aria-label="جدولة الأرصدة التلقائية">
    <h2>جدولة الأرصدة التلقائية</h2>
    <p>تعمل كل ساعتين بعد تفعيلها، حتى عند إغلاق المتصفح. فتح الكشوف يقرأ الأرصدة المحفوظة فقط.</p>
    {value && <>
      <p><strong>{value.enabled ? 'الجدولة مفعّلة' : 'الجدولة متوقفة'}</strong></p>
      <p>المحاولة التالية: {showTime(value.next_due_at)} · آخر محاولة: {showTime(value.last_attempt_at)}</p>
      <p>آخر احتساب انتهى بالحفظ: {showTime(value.last_success_at)}. راجع تنبيهات الجهات للتأكد من اكتمال البيانات والأيام.</p>
      {value.consecutive_failures > 0 && <p role="alert">تعذرت {value.consecutive_failures} محاولة متتالية؛ تظل آخر أرصدة محفوظة معروضة وتُعاد المحاولة بمهلة محدودة.</p>}
      <AttemptDetails trace={value.last_trace} error={value.last_error_code}/>
      <button type="button" disabled={busy || !verified || context.status !== 'active'} onClick={() => perform(!value.enabled)}>
        {value.enabled ? 'إيقاف الجدولة' : 'تفعيل الاحتساب التلقائي'}
      </button>
    </>}
    <button type="button" disabled={busy} onClick={() => perform()}>التحقق من حالة الجدولة</button>
    {busy && <p role="status">جارٍ التحقق من الجدولة…</p>}
    {message && <p role="alert">{message}</p>}
  </section>;
}
