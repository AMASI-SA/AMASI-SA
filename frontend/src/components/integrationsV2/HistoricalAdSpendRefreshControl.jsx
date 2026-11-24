import { useEffect, useRef, useState } from 'react';
import { addDaysISO, todaySA } from '../../lib/dates';
import { historicalSpendRange, readHistoricalAdSpend, refreshHistoricalAdSpend } from '../../services/adsHistoricalSpendRefresh';

const pendingAttempts = new Map();
const storageKey = owner => `mezan:historical-ad-spend:${owner}`;
const statusLabels = { complete: 'اكتملت مزامنة المصدر', partial: 'مزامنة جزئية', failed: 'فشلت المزامنة',
  skipped: 'لم تُنفذ المزامنة', incomplete: 'النتيجة غير مكتملة' };

function pending(owner) {
  if (!owner) return null;
  let value = pendingAttempts.get(owner);
  if (!value) {
    try { value = JSON.parse(sessionStorage.getItem(storageKey(owner))); } catch (_) { /* No session storage available. */ }
  }
  if (!value || value.owner !== owner || typeof value.id !== 'string') return null;
  try { historicalSpendRange(value.dateFrom, value.dateTo); } catch (_) { return null; }
  return value;
}

function remember(owner, value) {
  if (value) {
    if (!pendingAttempts.has(owner) && pendingAttempts.size >= 8) pendingAttempts.delete(pendingAttempts.keys().next().value);
    pendingAttempts.set(owner, value);
  } else pendingAttempts.delete(owner);
  try {
    if (value) sessionStorage.setItem(storageKey(owner), JSON.stringify(value));
    else sessionStorage.removeItem(storageKey(owner));
  } catch (_) { /* The in-memory marker still survives navigation in this tab. */ }
}

export default function HistoricalAdSpendRefreshControl({ user, sessionReady = false }) {
  const owner = sessionReady && user?.role === 'owner' && typeof user.id === 'string' && user.id ? user.id : null;
  const ownerRef = useRef(owner), active = useRef(null), sequence = useRef(0);
  ownerRef.current = owner;
  const [dateFrom, setDateFrom] = useState(() => addDaysISO(todaySA(), -6));
  const [dateTo, setDateTo] = useState(todaySA);
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');
  const [outcome, setOutcome] = useState(null), [saved, setSaved] = useState(null);
  const [unknown, setUnknown] = useState(() => pending(owner));

  useEffect(() => {
    setUnknown(pending(owner)); setOutcome(null); setSaved(null); setNotice(''); setBusy('');
    const stopRead = () => {
      if ((document.visibilityState === 'hidden' || navigator.onLine === false) && active.current?.kind === 'read') {
        active.current.controller.abort(); active.current = null; setBusy('');
      }
    };
    document.addEventListener('visibilitychange', stopRead);
    window.addEventListener('offline', stopRead);
    return () => {
      sequence.current += 1;
      active.current?.controller.abort(); active.current = null;
      document.removeEventListener('visibilitychange', stopRead);
      window.removeEventListener('offline', stopRead);
    };
  }, [owner]);

  let rangeValid = false;
  try { historicalSpendRange(dateFrom, dateTo); rangeValid = true; } catch (_) { /* Shown next to the date inputs. */ }

  async function perform(kind) {
    if (!owner || ownerRef.current !== owner || active.current || !rangeValid
        || document.visibilityState === 'hidden' || navigator.onLine === false) return;
    const controller = new AbortController(), version = ++sequence.current;
    const range = { dateFrom, dateTo }, admittedOwner = owner;
    const attempt = { owner, ...range, id: `${Date.now()}:${version}` };
    const current = () => ownerRef.current === admittedOwner && sequence.current === version && !controller.signal.aborted;
    active.current = { kind, controller };
    setBusy(kind); setNotice('');
    if (kind === 'sync') {
      remember(owner, attempt); setUnknown(attempt); setOutcome(null);
    }
    try {
      const result = await (kind === 'sync' ? refreshHistoricalAdSpend : readHistoricalAdSpend)({ ...range, signal: controller.signal });
      if (!current()) return;
      if (kind === 'sync') {
        if (pending(owner)?.id === attempt.id) remember(owner, null);
        setUnknown(null); setOutcome({ owner, ...result });
      } else setSaved({ owner, ...result });
    } catch (_) {
      if (!current()) return;
      setNotice(kind === 'sync'
        ? 'لم نتأكد من نتيجة طلب المزامنة. قد يستمر العمل في الخادم؛ اقرأ البيانات المحفوظة وراجع حالة المصادر قبل إعادة الطلب.'
        : 'تعذرت قراءة بيانات الإنفاق المحفوظة؛ لم تُرسل عملية مزامنة أو احتساب.');
    } finally {
      if (active.current?.controller === controller) active.current = null;
      if (current()) setBusy('');
    }
  }

  if (!owner) return null;
  const visibleOutcome = outcome?.owner === owner ? outcome : null;
  const visibleSaved = saved?.owner === owner ? saved : null;
  const visibleUnknown = unknown?.owner === owner ? unknown : null;
  return <section className="rounded-xl border border-blue-200 bg-blue-50/40 p-4" dir="rtl" data-testid="historical-ad-spend-control">
    <h2 className="text-sm font-extrabold text-slate-800">مزامنة إنفاق Meta وTikTok وGoogle لفترة سابقة</h2>
    <p className="mt-1 text-xs leading-6 text-slate-600">يجلب هذا الإجراء بيانات الإنفاق اليومية من الحسابات المعتمدة لهذه المنصات. أعداد الصفوف أدلة مصدر فقط؛ احتساب الأرصدة والتحقق من الحركات المالية إجراء مستقل في صفحة الأرصدة. مزامنة سناب لها زر مستقل.</p>
    <div className="mt-3 flex flex-wrap items-end gap-3">
      <label className="text-xs font-bold">من<input type="date" aria-label="بداية فترة مزامنة الإنفاق" value={dateFrom} max={dateTo || todaySA()} disabled={Boolean(busy)}
        onChange={event => setDateFrom(event.target.value)} className="mt-1 block rounded-lg border bg-white p-2" /></label>
      <label className="text-xs font-bold">إلى<input type="date" aria-label="نهاية فترة مزامنة الإنفاق" value={dateTo} min={dateFrom} max={todaySA()} disabled={Boolean(busy)}
        onChange={event => setDateTo(event.target.value)} className="mt-1 block rounded-lg border bg-white p-2" /></label>
      <button type="button" disabled={Boolean(busy) || !rangeValid} onClick={() => perform('sync')}
        className="min-h-10 rounded-lg bg-blue-700 px-4 text-xs font-extrabold text-white disabled:opacity-50" data-testid="historical-ad-spend-sync">
        {busy === 'sync' ? 'جارٍ طلب مزامنة المصادر…' : visibleUnknown ? 'إعادة طلب مزامنة الفترة' : 'مزامنة إنفاق المنصات الثلاث للفترة'}</button>
      <button type="button" disabled={Boolean(busy) || !rangeValid} onClick={() => perform('read')}
        className="min-h-10 rounded-lg border bg-white px-4 text-xs font-bold" data-testid="historical-ad-spend-read">
        {busy === 'read' ? 'جارٍ قراءة البيانات…' : 'قراءة الإنفاق المحفوظ للفترة'}</button>
    </div>
    {!rangeValid && <p role="alert" className="mt-2 text-xs text-amber-800">اختر فترة صحيحة من يوم واحد إلى 31 يومًا، حتى اليوم الحالي.</p>}
    {notice && <p role="alert" className="mt-3 text-xs leading-6 text-amber-800">{notice}</p>}
    {visibleUnknown && busy !== 'sync' && <p role="alert" className="mt-3 text-xs leading-6 text-amber-800" data-testid="historical-ad-spend-unknown">
      نتيجة طلب المزامنة للفترة {visibleUnknown.dateFrom} إلى {visibleUnknown.dateTo} غير مؤكدة. القراءة المحفوظة لا تثبت انتهاء الطلب السابق أو استعادة الحركات المالية؛ لا توجد إعادة محاولة تلقائية.</p>}
    {visibleOutcome && <div className="mt-3 overflow-x-auto" data-testid="historical-ad-spend-result">
      <p className="mb-2 text-xs font-bold">نتائج مزامنة المصدر: {visibleOutcome.dateFrom} إلى {visibleOutcome.dateTo}</p>
      <table className="w-full text-right text-xs"><thead><tr><th>المنصة</th><th>النتيجة</th><th>الحسابات المكتملة / المطلوبة</th><th>صفوف المصدر المحفوظة</th><th>الأخطاء</th></tr></thead>
        <tbody>{visibleOutcome.providers.map(row => <tr key={row.id}><td className="py-2">{row.label}</td><td>{row.disabled ? 'متوقفة أو غير مهيأة؛ لم تُنفذ' : statusLabels[row.status]}</td>
          <td>{row.accounts_complete ?? '—'} / {row.accounts_attempted ?? '—'}</td><td>{row.rows_saved ?? '—'}</td><td>{row.errors_count ?? '—'}</td></tr>)}</tbody></table>
    </div>}
    {visibleSaved && <div className="mt-3 text-xs leading-6" data-testid="historical-ad-spend-saved">
      <p className="font-bold">قراءة محفوظة للفترة {visibleSaved.dateFrom} إلى {visibleSaved.dateTo}</p>
      <ul>{visibleSaved.providers.map(row => <li key={row.id}>{row.label}: {row.availableDays} من {row.requestedDays} أيام لها مبلغ محفوظ.</li>)}</ul>
      <p>وجود مبلغ محفوظ لا يثبت إغلاق يوم المصدر أو إنشاء قيد مالي صحيح. قد يبقى اليوم الجاري غير مغلق.</p>
    </div>}
  </section>;
}
