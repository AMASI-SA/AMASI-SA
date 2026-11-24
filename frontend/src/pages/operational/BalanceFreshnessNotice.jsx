import React from 'react';
import { useBalanceFreshnessExpiry } from './useBalanceFreshnessExpiry';
import './balanceFreshness.css';

const dateLabel = value => value ? new Intl.DateTimeFormat('ar-SA', {
  timeZone: 'Asia/Riyadh', calendar: 'gregory', dateStyle: 'short', timeStyle: 'short',
}).format(new Date(value)) : 'غير متاح';
const healthLabels = { current: 'محدّث', stale: 'متأخر', failed: 'فشل التحديث',
  updating: 'تحديث جارٍ', unknown: 'غير متحقق', inactive: 'غير نشط' };
const attemptLabels = { running: 'جارٍ التحديث', success: 'اكتملت محاولة الاحتساب',
  failed: 'فشلت المحاولة', unknown: 'النتيجة غير مؤكدة' };
const familyLabels = { calculation: 'احتساب الأرصدة', health: 'التحقق من التحديث',
  payroll: 'الرواتب', recurring: 'المصروفات الدورية', salla: 'سلة والشحن ومنصات الدفع',
  snapchat: 'إعلانات Snapchat', meta: 'إعلانات Meta', tiktok: 'إعلانات TikTok', google_ads: 'إعلانات Google',
  bank: 'التحصيلات البنكية', shipping: 'شركات الشحن', payments: 'منصات الدفع', parties: 'الجهات التلقائية', sources: 'مصادر الأرصدة' };
const messageKey = message => (message || '').trim().replace(/\s+/g, ' ');
const durationLabel = seconds => {
  if (!Number.isFinite(seconds) || seconds < 0) return 'غير متاحة؛ لا يوجد نجاح مالي مثبت سابق';
  const minutes = Math.floor(seconds / 60);
  const days = Math.floor(minutes / 1440), hours = Math.floor(minutes % 1440 / 60);
  return `${days} يوم، ${hours} ساعة، ${minutes % 60} دقيقة`;
};
const entityRows = value => Array.isArray(value?.entity_coverage?.rows) ? value.entity_coverage.rows : [];

function briefStatus(state) {
  if (state.phase === 'refreshing') return 'يجري تحديث المصادر؛ الأرقام الظاهرة من آخر احتساب محفوظ.';
  if (state.phase === 'checking') return 'جارٍ التحقق من الحالة؛ تبقى آخر نتيجة محفوظة ظاهرة.';
  if (state.phase === 'error') return 'تعذر التحقق من الحالة؛ لم يثبت تعافي التحديث بعد.';
  const attempt = state.snapshot?.last_attempt?.outcome;
  if (attempt === 'running') return 'تحديث المصادر جارٍ؛ لم تصل نتيجة نهائية بعد.';
  if (attempt === 'failed') return 'فشلت آخر محاولة؛ لم يثبت نجاح تحديث لاحق.';
  if (state.phase === 'unknown' || attempt === 'unknown') return 'نتيجة التحديث غير مؤكدة؛ يلزم التحقق من الحالة.';
  const health = state.snapshot?.freshness?.state;
  if (state.snapshot?.stale || health === 'stale') return 'الاحتساب أو بعض المصادر متأخرة عن موعد التحديث.';
  if (health === 'failed') return 'بعض المصادر لم تتحدث بنجاح وتحتاج إلى متابعة.';
  if (health === 'unknown') return 'لم تكتمل أدلة حداثة المصادر.';
  if (health === 'updating') return 'يجري تحديث بعض المصادر؛ لم يثبت اكتمالها بعد.';
  if (health === 'inactive') return 'الاحتساب التلقائي غير نشط حالياً.';
  if (state.snapshot?.entity_coverage?.state === 'unknown') return 'لم يثبت اكتمال المطابقة المالية لجميع الجهات؛ راجع تفاصيل الحسابات.';
  if (state.snapshot?.entity_coverage?.state === 'stale') return 'توجد جهات متأخرة أو أيام لم يثبت تسجيل حركاتها المالية كاملة.';
  return 'توجد ملاحظات على حالة المصادر تحتاج إلى متابعة.';
}

function detailGroups(value) {
  const sources = value?.sources || [];
  const grouped = new Map();
  for (const issue of value?.issues || []) {
    const key = JSON.stringify([issue.source, messageKey(issue.message), issue.last_success_at || null]);
    if (!grouped.has(key)) grouped.set(key, { ...issue, codes: [] });
    const group = grouped.get(key);
    if (!group.codes.includes(issue.code)) group.codes.push(issue.code);
  }
  const issues = [...grouped.values()];
  const repeatsSource = (issue, source) => issue.source === source.key
    && messageKey(issue.message) === messageKey(source.message)
    && (!issue.last_success_at || issue.last_success_at === source.last_success_at);
  const duplicates = source => issues.filter(issue => repeatsSource(issue, source));
  return { sources: sources.map(source => ({ ...source, codes: duplicates(source).flatMap(issue => issue.codes) })),
    issues: issues.filter(issue => !sources.some(source => repeatsSource(issue, source))) };
}

export function hasBalanceFreshnessWarning(state) {
  const value = state.snapshot;
  return Boolean(state.message && state.phase !== 'inactive' || ['error', 'unknown', 'stale'].includes(state.phase)
    || ['failed', 'unknown', 'running'].includes(value?.last_attempt?.outcome)
    || value && (value.stale || !['current', 'inactive'].includes(value.freshness.state)
      || value.sources.some(source => !['current', 'inactive'].includes(source.state))
      || value.issues.some(issue => issue.severity !== 'info')
      || value.entity_coverage && !['current', 'inactive'].includes(value.entity_coverage.state)));
}

export default function BalanceFreshnessNotice({ state: sourceState, compact = false, globalNotice = false, legacyControls = false, onCheck, canUpdate = false, href }) {
  const state = useBalanceFreshnessExpiry(sourceState);
  const value = state.snapshot;
  const warning = hasBalanceFreshnessWarning(state);
  const busy = ['checking', 'refreshing'].includes(state.phase);
  if (compact && !warning && !value?.issues?.length) return null;
  if (!value && !state.message && !busy) return null;
  const allSources = value?.sources || [];
  const entities = entityRows(value);
  const label = key => allSources.find(source => source.key === key)?.label || familyLabels[key] || 'مصادر أخرى';
  const affected = [...new Set([
    ...allSources.filter(source => !['current', 'inactive'].includes(source.state)).map(source => source.label),
    ...(value?.issues || []).map(issue => label(issue.source)),
    ...entities.filter(row => !['current', 'inactive'].includes(row.state)).map(row => `${row.label} (${row.currency})`),
  ])];
  const details = detailGroups(value);
  const detailMessage = compact && state.message && !allSources.some(source => messageKey(source.message) === messageKey(state.message))
    && !details.issues.some(issue => messageKey(issue.message) === messageKey(state.message)) ? state.message : '';
  const hasDetails = Boolean(value?.last_attempt || details.sources.length || details.issues.length || detailMessage
    || value?.entity_coverage);
  return <section className={'balance-freshness' + (warning ? ' balance-freshness-warning' : '')
    + (compact ? ' balance-freshness-compact' : '') + (globalNotice ? ' balance-freshness-global' : '')
    + (globalNotice && legacyControls ? ' balance-freshness-legacy-controls' : '')}
    aria-label="حالة تحديث الأرصدة" role={warning ? 'alert' : 'status'} dir="rtl" aria-live="polite">
    <div className="balance-freshness-heading"><strong>{warning ? 'تحديث الأرصدة يحتاج إلى متابعة'
      : state.phase === 'refreshing' ? 'جارٍ تحديث مصادر الأرصدة' : 'حالة احتساب الأرصدة'}</strong>
      {onCheck && <button type="button" disabled={busy} onClick={onCheck}>
        {busy ? 'جارٍ التحقق…' : canUpdate ? state.phase === 'unknown' ? 'التحقق وإعادة المحاولة' : 'تحديث المصادر' : 'التحقق من الحالة'}</button>}
      {href && <a href={href}>فتح الأرصدة</a>}
    </div>
    {value && <p>آخر احتساب محفوظ: <time dateTime={value.as_of || undefined}>{dateLabel(value.as_of)}</time> بتوقيت الرياض.
      {state.phase === 'refreshing' && ' تبقى الأرصدة المحفوظة ظاهرة حتى تأكيد نتيجة التحديث.'}</p>}
    {compact ? <p>{briefStatus(state)}</p> : <>
      {value && warning && <p>حالة المصادر: {healthLabels[value.freshness.state]}.</p>}
      {state.message && <p>{state.message}</p>}
    </>}
    {affected.length > 0 && <p className="balance-freshness-families"><strong>{warning ? 'المصادر التي تحتاج متابعة: ' : 'المصادر المعنية: '}</strong>{affected.slice(0, 8).join('، ')}
      {affected.length > 8 && <>، و{affected.length - 8} جهات أخرى في التفاصيل</>}.</p>}
    {hasDetails && <details className="balance-freshness-details">
      <summary>تفاصيل المصادر والمحاولات</summary>
      <div className="balance-freshness-details-body" role="region" aria-label="تفاصيل حالة تحديث الأرصدة" tabIndex={0}>
        {detailMessage && <p>{detailMessage}</p>}
        {value?.last_attempt && <p>آخر محاولة: {dateLabel(value.last_attempt.started_at)} · {attemptLabels[value.last_attempt.outcome] || 'غير متحققة'}
          {value.last_attempt.finished_at && <> · انتهت: {dateLabel(value.last_attempt.finished_at)}</>}
          {value.last_attempt.error_code && <small>رمز المتابعة للدعم: <bdi>{value.last_attempt.error_code}</bdi></small>}</p>}
        {details.sources.length > 0 && <ul>{details.sources.map(source => <li key={source.key}>
          <strong>{source.label}: {healthLabels[source.state]}</strong> · آخر نجاح: {dateLabel(source.last_success_at)}
          {source.data_through && <> · البيانات حتى <bdi>{source.data_through}</bdi></>}
          {source.expected_through && <> · المطلوب حتى <bdi>{source.expected_through}</bdi></>}
          {source.message && <span> — {source.message}</span>}
          {source.codes.length > 0 && <small>رموز المتابعة للدعم: <bdi>{source.codes.join('، ')}</bdi></small>}
        </li>)}</ul>}
        {details.issues.length > 0 && <ul>{details.issues.map((issue, index) => <li key={`${issue.source}:${index}`}>
          <strong>{label(issue.source)}: </strong>{issue.message}
          <small>{issue.last_success_at && <>آخر نجاح: {dateLabel(issue.last_success_at)} · </>}رموز المتابعة للدعم: <bdi>{issue.codes.join('، ')}</bdi></small>
        </li>)}</ul>}
        {value?.entity_coverage && <section aria-label="المطابقة المالية لكل جهة">
          <h3>المطابقة المالية لكل جهة</h3>
          {value.entity_coverage.message && <p>{value.entity_coverage.message}</p>}
          {value.entity_coverage.omitted_entities > 0 && <p>لم تُعرض {value.entity_coverage.omitted_entities} جهات بسبب حد العرض؛ اكتمالها غير متحقق.</p>}
          {entities.length > 0 && <ul>{entities.map(row => <li key={row.key}>
            <strong>{row.label} (<bdi>{row.currency}</bdi>): {healthLabels[row.state] || 'غير متحقق'}</strong>
            <div>آخر مطابقة مالية مكتملة: {dateLabel(row.last_success_at)}
              {row.last_financial_day && <> · آخر يوم مثبت ماليًا: <bdi>{row.last_financial_day}</bdi></>}
              {row.expected_through && <> · المطلوب حتى: <bdi>{row.expected_through}</bdi></>}</div>
            {!['current', 'inactive'].includes(row.state) && <div>المدة منذ آخر نجاح: {durationLabel(row.stale_for_seconds)}.</div>}
            {row.calendar !== 'events' && <div>{row.missing_days_count === null
              ? 'الأيام الناقصة غير متحققة؛ لا يعني ذلك عدم وجود أيام ناقصة.'
              : <>أيام لم يثبت اكتمالها: {row.missing_days_count}.
                {Array.isArray(row.missing_days) && row.missing_days.length > 0 && <> <bdi>{row.missing_days.join('، ')}</bdi>.</>}
                {row.missing_days_truncated && <> عُرض أول {row.missing_days.length} يومًا؛ آخر يوم ناقص: <bdi>{row.last_missing_day}</bdi>.</>}</>}</div>}
            {row.calendar === 'events' && <div>هذا المصدر يعتمد على الحركات؛ لا يُفترض وجود حركة يومية أو مبلغ صفري دون دليل.</div>}
            <span>{row.message}</span>
          </li>)}</ul>}
        </section>}
      </div>
    </details>}
  </section>;
}
