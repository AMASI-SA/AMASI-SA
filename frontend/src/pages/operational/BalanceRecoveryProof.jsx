import React, { useEffect, useRef, useState } from 'react';
import http from '../../lib/api';
import { calculationTime } from './visibleBalanceFreshness';

const PAGE_SIZE = 12;
const labels = { employee: 'الموظفون', ad_account: 'الحسابات الإعلانية', courier: 'شركات الشحن',
  store_driver: 'الموصلون', provider: 'منصات الدفع', bank: 'الحسابات البنكية', supplier: 'الموردون',
  operating_expense: 'المصروفات التشغيلية', recurring: 'المصروفات الدورية', external_person: 'الجهات الخارجية' };
const directions = { payable: 'علينا', receivable: 'لنا', for_party: 'للجهة', for_us: 'لنا', incoming: 'وارد', outgoing: 'صادر' };
const count = value => Number.isSafeInteger(value) && value >= 0;
const money = value => typeof value === 'string' && value.length <= 40 && /^\d+(?:\.\d{1,2})?$/.test(value);
const text = (value, limit = 200) => typeof value === 'string' && value.length > 0 && value.length <= limit;
const keyOf = row => JSON.stringify([row.entity_type, row.entity_id, row.currency]);
const timeLabel = value => new Intl.DateTimeFormat('ar-SA', { timeZone: 'Asia/Riyadh', calendar: 'gregory',
  dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));

function checked(value, scope) {
  if (!value || value.schema_version !== 1 || value.session_scope !== scope
      || !['available', 'unknown'].includes(value.state)) throw new Error('invalid_recovery_receipt');
  if (value.state === 'unknown') return { ...value, proof: null, entities: [] };
  const proof = value.proof;
  if (!proof || proof.schema_version !== 1 || calculationTime(proof.as_of) === null
      || !count(proof.calculation_revision) || typeof proof.complete !== 'boolean'
      || proof.source_completeness_verified !== false) throw new Error('invalid_recovery_receipt');
  const groups = [proof.new_original_dated, proof.new_date_unverified,
    proof.changed_existing?.before, proof.changed_existing?.after].filter(Boolean);
  let size = 0;
  for (const group of groups) for (const type of ['obligations', 'automatic_movements']) {
    if (!Array.isArray(group[type])) throw new Error('invalid_recovery_groups');
    size += group[type].length;
    for (const row of group[type]) {
      if (!row || !text(row.entity_type, 40) || !text(row.entity_id) || !/^[A-Z]{3}$/.test(row.currency)
          || !/^\d{4}-\d{2}-\d{2}$/.test(row.business_date) || !count(row.count)
          || !Object.hasOwn(directions, row.direction)
          || (type === 'obligations' ? !money(row.confirmed) || !money(row.expected) : !money(row.amount))) {
        throw new Error('invalid_recovery_row');
      }
    }
  }
  if (size > 1024 || (proof.complete && groups.length !== 4)) throw new Error('invalid_recovery_groups');
  for (const counters of [proof.new_duplicate_keys, proof.existing_duplicate_keys]) {
    if (counters && (typeof counters !== 'object' || Object.values(counters).some(value => !count(value)))) {
      throw new Error('invalid_recovery_duplicates');
    }
  }
  const entities = value.entities || [];
  if (!Array.isArray(entities) || entities.length > 256 || entities.some(row => !row
      || !text(row.entity_type, 40) || !text(row.entity_id) || !text(row.label, 120) || !/^[A-Z]{3}$/.test(row.currency))) {
    throw new Error('invalid_recovery_entities');
  }
  return { ...value, entities };
}

function rowsFor(proof, view) {
  const section = (value, phase) => [
    ...(value?.obligations || []).map(row => ({ ...row, amount: row.confirmed, record: `${phase}استحقاق`, expected: row.expected })),
    ...(value?.automatic_movements || []).map(row => ({ ...row, record: `${phase}حركة تلقائية`, expected: null })),
  ];
  if (view === 'changed') return [...section(proof?.changed_existing?.before, 'قبل: '), ...section(proof?.changed_existing?.after, 'بعد: ')];
  return section(view === 'verified' ? proof?.new_original_dated : proof?.new_date_unverified, '');
}

export default function BalanceRecoveryProof({ context }) {
  const owner = context?.actor?.is_owner === true && Boolean(context.actor.id && context.session_scope);
  const key = JSON.stringify([context?.actor?.id, context?.session_scope, context?.permission_version]);
  const currentKey = useRef(key), active = useRef(null);
  currentKey.current = key;
  const [saved, setSaved] = useState(null), [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  const [view, setView] = useState('verified'), [page, setPage] = useState(0);
  useEffect(() => {
    setBusy(false); setMessage('');
    const hide = () => {
      if (document.visibilityState === 'hidden' || navigator.onLine === false) {
        active.current?.abort(); active.current = null; setBusy(false);
      }
    };
    document.addEventListener('visibilitychange', hide); window.addEventListener('offline', hide);
    return () => { active.current?.abort(); active.current = null;
      document.removeEventListener('visibilitychange', hide); window.removeEventListener('offline', hide); };
  }, [key, owner]);
  const read = async () => {
    if (!owner || active.current || document.visibilityState === 'hidden' || navigator.onLine === false) return;
    const controller = new AbortController(), identity = key; active.current = controller;
    const current = () => !controller.signal.aborted && identity === currentKey.current;
    setBusy(true); setMessage('');
    try {
      const response = await http.get('/operational-balances/recovery-proof', { signal: controller.signal, timeout: 8000 });
      if (current()) { setSaved({ key: identity, value: checked(response.data, context.session_scope) }); setPage(0); }
    } catch (_) {
      if (current()) setMessage('تعذر التحقق من تقرير الاستعادة؛ التقرير السابق إن ظهر يعود إلى آخر قراءة محفوظة.');
    } finally {
      if (active.current === controller) active.current = null;
      if (current()) setBusy(false);
    }
  };
  if (!owner) return null;
  const result = saved?.key === key ? saved.value : null, proof = result?.proof;
  const rows = rowsFor(proof, view), pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const names = new Map((result?.entities || []).map(row => [keyOf(row), row.label]));
  const total = value => value ? Object.values(value).reduce((sum, number) => sum + number, 0) : null;
  const duplicateCount = total(proof?.new_duplicate_keys), existingDuplicates = total(proof?.existing_duplicate_keys);
  const preservation = (field, label) => <li key={field}>{label}: {proof?.preservation?.[field]?.preserved === true
    ? 'محفوظة دون تغيير' : proof?.preservation?.[field]?.preserved === false ? 'توجد تغييرات أو عناصر مفقودة؛ يلزم المراجعة' : 'غير متحققة'}</li>;
  return <section className="panel sheet" aria-label="تقرير استعادة الأرصدة" dir="rtl">
    <h2>تقرير استعادة الأرصدة</h2>
    <p>يعرض أثر آخر احتساب محفوظ. اكتمال هذه المقارنة لا يثبت اكتمال جميع المصادر أو الأيام؛ راجع تنبيهات الجهات.</p>
    <button type="button" disabled={busy} onClick={read}>{busy ? 'جارٍ قراءة التقرير…' : 'قراءة تقرير آخر احتساب'}</button>
    {message && <p role="alert">{message}</p>}
    {result?.state === 'unknown' && <p role="alert">لا يتوفر تقرير استعادة محفوظ يمكن التحقق منه؛ لا يعني ذلك أن عدد الحركات المستعادة صفر.</p>}
    {proof && <>
      <p>وقت الاحتساب: {timeLabel(proof.as_of)} بتوقيت الرياض · نسخة الاحتساب: <bdi>{proof.calculation_revision}</bdi>.</p>
      {!proof.complete && <p role="alert">المقارنة غير مكتملة أو تجاوزت حدود التقرير أو تتضمن اختلافات؛ لا يمكن إعلان اكتمال الاستعادة.</p>}
      {proof.original_dates_verified === false && <p role="alert">توجد إضافات أو تغييرات لم يثبت تاريخها الأصلي؛ تظهر منفصلة عن الحركات ذات التاريخ المثبت.</p>}
      <ul>{preservation('manual_movements', 'القيود اليدوية')}{preservation('automatic_movements', 'الحركات التلقائية السابقة')}
        {preservation('openings', 'الأرصدة الافتتاحية')}{preservation('requests', 'هويات العمليات السابقة')}</ul>
      <p>زيادة المعرفات المكررة: {duplicateCount === null ? 'غير متحققة' : duplicateCount}.
        {existingDuplicates > 0 && <> توجد {existingDuplicates} حالات تكرار سابقة تحتاج إلى مراجعة.</>}</p>
      {proof.changed_existing?.count > 0 && <p role="alert">تغيّر {proof.changed_existing.count} استحقاقًا سابقًا؛ هذه التغييرات معروضة منفصلة عن الإضافات الجديدة.</p>}
      {proof.removed_obligation_count > 0 && <p role="alert">غاب {proof.removed_obligation_count} استحقاقًا مقارنة بالسجل السابق؛ يلزم فحص سبب الغياب.</p>}
      <label>قسم التقرير <select value={view} onChange={event => { setView(event.target.value); setPage(0); }}>
        <option value="verified">إضافات جديدة بتاريخ أصلي مثبت</option>
        <option value="unverified">إضافات جديدة بتاريخ غير متحقق</option>
        <option value="changed">التغير في الاستحقاقات السابقة</option>
      </select></label>
      {rows.length ? <>
        <div style={{ overflowX: 'auto' }}><table><thead><tr><th>الجهة</th><th>تاريخ السجل</th><th>العملة</th><th>السجل</th><th>الاتجاه</th><th>العدد</th><th>المبلغ المثبت</th><th>المبلغ المتوقع</th></tr></thead>
          <tbody>{rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((row, index) => <tr key={index}>
            <td>{names.get(keyOf(row)) || `${labels[row.entity_type] || 'جهة مالية'} — الاسم غير متاح`}</td>
            <td><bdi>{row.business_date}</bdi></td><td><bdi>{row.currency}</bdi></td><td>{row.record}</td><td>{directions[row.direction]}</td><td>{row.count}</td>
            <td><bdi>{row.amount}</bdi></td><td><bdi>{row.expected ?? '—'}</bdi></td>
          </tr>)}</tbody></table></div>
        <nav aria-label="صفحات تقرير الاستعادة"><button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>السابق</button>
          <span> صفحة {page + 1} من {pages} · {rows.length} مجموعة </span>
          <button type="button" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>التالي</button></nav>
      </> : <p>{proof.complete ? 'لا توجد سجلات في هذا القسم ضمن مقارنة المحاولة المعروضة.' : 'لا تتوفر سجلات متحققة لهذا القسم؛ لا يُفترض أن عددها صفر.'}</p>}
    </>}
  </section>;
}
