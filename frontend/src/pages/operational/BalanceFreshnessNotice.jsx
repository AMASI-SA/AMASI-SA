import React from 'react';
import { useBalanceFreshnessExpiry } from './useBalanceFreshnessExpiry';
import './balanceFreshness.css';

const dateLabel = value => value ? new Intl.DateTimeFormat('ar-SA', {
  timeZone: 'Asia/Riyadh', calendar: 'gregory', dateStyle: 'short', timeStyle: 'short',
}).format(new Date(value)) : 'غير متاح';
const healthLabels = { current: 'محدّث', stale: 'متأخر', failed: 'فشل التحديث',
  updating: 'تحديث جارٍ', unknown: 'غير متحقق', inactive: 'غير نشط' };

export function hasBalanceFreshnessWarning(state) {
  const value = state.snapshot;
  return Boolean(state.message && state.phase !== 'inactive' || ['error', 'unknown', 'stale'].includes(state.phase)
    || ['failed', 'unknown', 'running'].includes(value?.last_attempt?.outcome)
    || value && (value.stale || !['current', 'inactive'].includes(value.freshness.state)
      || value.sources.some(source => !['current', 'inactive'].includes(source.state))
      || value.issues.some(issue => issue.severity !== 'info')));
}

export default function BalanceFreshnessNotice({ state: sourceState, compact = false, globalNotice = false, onCheck, canUpdate = false, href }) {
  const state = useBalanceFreshnessExpiry(sourceState);
  const value = state.snapshot;
  const warning = hasBalanceFreshnessWarning(state);
  const busy = ['checking', 'refreshing'].includes(state.phase);
  if (compact && !warning && !value?.issues?.length) return null;
  if (!value && !state.message && !busy) return null;
  const sources = value?.sources?.filter(source => !['current', 'inactive'].includes(source.state)) || [];
  return <section className={'balance-freshness' + (warning ? ' balance-freshness-warning' : '') + (globalNotice ? ' balance-freshness-global' : '')}
    aria-label="حالة تحديث الأرصدة" role={warning ? 'alert' : 'status'} dir="rtl" aria-live="polite">
    <div className="balance-freshness-heading"><strong>{warning ? 'تحديث الأرصدة يحتاج إلى متابعة'
      : state.phase === 'refreshing' ? 'جارٍ تحديث مصادر الأرصدة' : 'حالة احتساب الأرصدة'}</strong>
      {onCheck && <button type="button" disabled={busy} onClick={onCheck}>
        {busy ? 'جارٍ التحقق…' : canUpdate ? state.phase === 'unknown' ? 'التحقق وإعادة المحاولة' : 'تحديث المصادر' : 'التحقق من الحالة'}</button>}
      {href && <a href={href}>فتح الأرصدة</a>}
    </div>
    {value && <p>آخر احتساب محفوظ: <time dateTime={value.as_of || undefined}>{dateLabel(value.as_of)}</time> بتوقيت الرياض.
      {state.phase === 'refreshing' && ' تبقى الأرصدة المحفوظة ظاهرة حتى تأكيد نتيجة التحديث.'}</p>}
    {value && warning && <p>حالة المصادر: {healthLabels[value.freshness.state]}.</p>}
    {state.message && <p>{state.message}</p>}
    {value?.last_attempt && <p>آخر محاولة: {dateLabel(value.last_attempt.started_at)} · {
      { running: 'جارٍ التحديث', success: 'اكتملت محاولة الاحتساب', failed: 'فشلت المحاولة', unknown: 'النتيجة غير مؤكدة' }[value.last_attempt.outcome] || 'غير متحققة'}
      {value.last_attempt.error_code && <> · <bdi>{value.last_attempt.error_code}</bdi></>}</p>}
    {sources.length > 0 && <ul>{sources.map(source => <li key={source.key}>
      <strong>{source.label}: {healthLabels[source.state]}</strong> · آخر نجاح: {dateLabel(source.last_success_at)}
      {source.data_through && <> · البيانات حتى <bdi>{source.data_through}</bdi></>}
      {source.expected_through && <> · المطلوب حتى <bdi>{source.expected_through}</bdi></>}
      {source.message && <span> — {source.message}</span>}
    </li>)}</ul>}
    {value?.issues?.length > 0 && <ul>{value.issues.map((issue, index) => <li key={`${issue.source}:${issue.code}:${index}`}>
      {issue.message}<small><bdi>{issue.code}</bdi>{issue.last_success_at && <> · آخر نجاح: {dateLabel(issue.last_success_at)}</>}</small>
    </li>)}</ul>}
  </section>;
}
