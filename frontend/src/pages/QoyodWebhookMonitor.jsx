/**
 * Iter-294 — Qoyod Webhook Monitor
 *
 * Live tail of every webhook arrival from Make.com (and future direct
 * Salla webhooks). Replaces the previous placeholder at
 * `/integrations/qoyod/sync-log`.
 *
 * Features:
 *   • Live refresh every 15 s (controllable).
 *   • Counts header: total / accepted / skipped / errors over selected
 *     time window (1h / 24h / 7d).
 *   • Filters: event_type · order_id · skipped-only.
 *   • Color-coded rows: 🟢 accepted · 🟡 skipped · 🔴 error.
 *   • Click row → drawer with full JSON event details.
 *
 * Backend endpoints (Iter-293):
 *   GET /api/integrations/qoyod/admin/webhook-activity
 *   GET /api/integrations/qoyod/admin/webhook-activity/counts
 */
import React, { useEffect, useMemo, useState } from "react";
import api from "../lib/api";
import { Term, TermPill } from "../components/Term";

const QOYOD_BASE = "/integrations/qoyod";

// ────────────────────────────────────────────────────────────────────
// Small UI primitives (kept inline — no external deps beyond what
// QoyodSettings already uses to stay consistent visually).
// ────────────────────────────────────────────────────────────────────
const StatCard = ({ label, value, tone = "default", testid }) => {
  const toneClasses = {
    default: "bg-zinc-50 dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100",
    green:   "bg-emerald-50 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-200",
    yellow:  "bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-200",
    red:     "bg-rose-50 dark:bg-rose-950 text-rose-700 dark:text-rose-200",
  }[tone] || "";
  return (
    <div data-testid={testid}
      className={`rounded-xl border border-zinc-200 dark:border-zinc-800 p-4 ${toneClasses}`}>
      <div className="text-xs uppercase tracking-wider opacity-70">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
    </div>
  );
};

const Pill = ({ tone = "default", children }) => {
  const cls = {
    default: "bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-200",
    green:   "bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-200",
    yellow:  "bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-200",
    red:     "bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-200",
  }[tone];
  return <span className={`inline-block px-2 py-0.5 rounded-full text-xs ${cls}`}>{children}</span>;
};

const rowTone = (r) => {
  if (r.http_response_status >= 400 || r.items_parsed_ok === false) return "red";
  if (r.skipped_reason) return "yellow";
  return "green";
};

const fmtTime = (iso) => {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("ar-SA", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hour12: false,
    });
  } catch { return iso; }
};

// ────────────────────────────────────────────────────────────────────
export default function QoyodWebhookMonitor() {
  const [rows, setRows] = useState([]);
  const [counts, setCounts] = useState({ total: 0, accepted: 0, skipped: 0, errors: 0, by_event: {} });
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshIntervalSec, setRefreshIntervalSec] = useState(15);
  const [windowHours, setWindowHours] = useState(24);

  // Filters
  const [filterEventType, setFilterEventType] = useState("");
  const [filterOrderId, setFilterOrderId] = useState("");
  const [filterSkippedOnly, setFilterSkippedOnly] = useState(false);

  // Drawer
  const [selectedRow, setSelectedRow] = useState(null);

  const fetchAll = async () => {
    setErr(null);
    try {
      const params = { limit: 100 };
      if (filterEventType)   params.event_type = filterEventType;
      if (filterOrderId)     params.order_id = filterOrderId;
      if (filterSkippedOnly) params.skipped_only = true;

      const [rowsRes, countsRes] = await Promise.all([
        api.get(`${QOYOD_BASE}/admin/webhook-activity`, { params }),
        api.get(`${QOYOD_BASE}/admin/webhook-activity/counts`,
                { params: { hours: windowHours } }),
      ]);
      setRows(Array.isArray(rowsRes.data?.rows) ? rowsRes.data.rows : []);
      setCounts(countsRes.data || { total: 0, accepted: 0, skipped: 0, errors: 0, by_event: {} });
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  // Initial + manual filter changes
  useEffect(() => { fetchAll(); }, [filterEventType, filterOrderId, filterSkippedOnly, windowHours]);

  // Live refresh
  useEffect(() => {
    if (!autoRefresh) return;
    const handle = setInterval(fetchAll, Math.max(5, refreshIntervalSec) * 1000);
    return () => clearInterval(handle);
  }, [autoRefresh, refreshIntervalSec, filterEventType, filterOrderId, filterSkippedOnly, windowHours]);

  const distinctEventTypes = useMemo(() => {
    const s = new Set();
    rows.forEach((r) => r.event_type && s.add(r.event_type));
    Object.keys(counts.by_event || {}).forEach((k) => s.add(k));
    return Array.from(s).sort();
  }, [rows, counts]);

  return (
    <div className="p-6 space-y-6" data-testid="qoyod-webhook-monitor">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-semibold" data-testid="webhook-monitor-title">
            📡 مراقب Webhooks — قيود
          </h1>
          <p className="text-sm text-zinc-500 mt-1 max-w-2xl">
            Tail حي لكل حدث webhook يصل من Make.com. يُحدَّث كل {refreshIntervalSec} ثانية.
            استخدم الفلاتر للتركيز على نوع حدث أو طلب محدد.
          </p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-2" data-testid="autorefresh-toggle">
            <input type="checkbox" checked={autoRefresh}
                   onChange={(e) => setAutoRefresh(e.target.checked)} />
            تحديث تلقائي
          </label>
          <select value={refreshIntervalSec}
                  onChange={(e) => setRefreshIntervalSec(Number(e.target.value))}
                  className="border rounded px-2 py-1 bg-white dark:bg-zinc-900 dark:border-zinc-700"
                  data-testid="autorefresh-interval">
            <option value={5}>5s</option>
            <option value={15}>15s</option>
            <option value={30}>30s</option>
            <option value={60}>60s</option>
          </select>
          <button onClick={fetchAll}
                  className="px-3 py-1 rounded-lg bg-zinc-900 text-white text-xs dark:bg-zinc-100 dark:text-zinc-900"
                  data-testid="refresh-now-btn">
            🔄 الآن
          </button>
        </div>
      </div>

      {/* Counts */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label={`الإجمالي (${windowHours} ساعة)`} value={counts.total} testid="stat-total" />
        <StatCard label="مقبول"  value={counts.accepted} tone="green"  testid="stat-accepted" />
        <StatCard label="تخطّي"  value={counts.skipped}  tone="yellow" testid="stat-skipped" />
        <StatCard label="خطأ"    value={counts.errors}   tone="red"    testid="stat-errors" />
      </div>

      {/* Filters */}
      <div className="flex items-end gap-3 flex-wrap" data-testid="filters-bar">
        <div>
          <div className="text-xs text-zinc-500 mb-1">نافذة الإحصاء</div>
          <select value={windowHours} onChange={(e) => setWindowHours(Number(e.target.value))}
                  className="border rounded px-2 py-1.5 bg-white dark:bg-zinc-900 dark:border-zinc-700"
                  data-testid="window-select">
            <option value={1}>آخر ساعة</option>
            <option value={24}>آخر 24 ساعة</option>
            <option value={168}>آخر 7 أيام</option>
          </select>
        </div>
        <div>
          <div className="text-xs text-zinc-500 mb-1">نوع الحدث</div>
          <select value={filterEventType} onChange={(e) => setFilterEventType(e.target.value)}
                  className="border rounded px-2 py-1.5 bg-white dark:bg-zinc-900 dark:border-zinc-700 min-w-40"
                  data-testid="filter-event-type">
            <option value="">— الكل —</option>
            {distinctEventTypes.map((et) => (<option key={et} value={et}>{et}</option>))}
          </select>
        </div>
        <div>
          <div className="text-xs text-zinc-500 mb-1">رقم الطلب</div>
          <input value={filterOrderId} onChange={(e) => setFilterOrderId(e.target.value)}
                 placeholder="مثل 268756329"
                 className="border rounded px-2 py-1.5 bg-white dark:bg-zinc-900 dark:border-zinc-700"
                 data-testid="filter-order-id" />
        </div>
        <label className="flex items-center gap-2 text-sm mb-1" data-testid="filter-skipped-only-wrap">
          <input type="checkbox" checked={filterSkippedOnly}
                 onChange={(e) => setFilterSkippedOnly(e.target.checked)}
                 data-testid="filter-skipped-only" />
          <span title="عرض الأحداث التي قرر النظام تجاهلها فقط (لم تُرسل إلى قيود)"
                className="cursor-help underline decoration-dotted decoration-zinc-400">
            الأحداث المتجاهلة فقط
          </span>
        </label>
        <button onClick={() => { setFilterEventType(""); setFilterOrderId(""); setFilterSkippedOnly(false); }}
                className="px-3 py-1.5 text-xs rounded-lg bg-zinc-100 dark:bg-zinc-800 dark:text-zinc-200"
                data-testid="filter-clear-btn">
          مسح الفلاتر
        </button>
      </div>

      {/* Error banner */}
      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 dark:bg-rose-950 dark:border-rose-800 px-4 py-2 text-sm text-rose-700 dark:text-rose-200"
             data-testid="error-banner">
          فشل التحميل: {err}
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto border border-zinc-200 dark:border-zinc-800 rounded-xl">
        <table className="min-w-full text-sm" data-testid="events-table">
          <thead className="bg-zinc-50 dark:bg-zinc-900 text-zinc-500">
            <tr>
              <th className="text-right px-3 py-2"></th>
              <th className="text-right px-3 py-2">الوقت</th>
              <th className="text-right px-3 py-2">الحدث</th>
              <th className="text-right px-3 py-2">رقم الطلب</th>
              <th className="text-right px-3 py-2">عناصر</th>
              <th className="text-right px-3 py-2">المرحلة</th>
              <th className="text-right px-3 py-2">HTTP</th>
              <th className="text-right px-3 py-2">السبب/الحالة</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={8} className="text-center py-8 text-zinc-500">يحمّل…</td></tr>
            )}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={8} className="text-center py-8 text-zinc-500" data-testid="empty-state">
                لا توجد أحداث مطابقة. إذا كانت Make يجب أن ترسل، تحقق من أن الـ scenario مفعّل والـ webhook URL يطابق Production.
              </td></tr>
            )}
            {rows.map((r) => {
              const tone = rowTone(r);
              const toneRow = {
                green:  "hover:bg-emerald-50/40 dark:hover:bg-emerald-950/30",
                yellow: "hover:bg-amber-50/40 dark:hover:bg-amber-950/30",
                red:    "hover:bg-rose-50/40 dark:hover:bg-rose-950/30",
              }[tone];
              return (
                <tr key={r.id}
                    onClick={() => setSelectedRow(r)}
                    className={`cursor-pointer border-t border-zinc-100 dark:border-zinc-800 ${toneRow}`}
                    data-testid={`event-row-${r.id}`}>
                  <td className="px-3 py-2">{tone === "green" ? "🟢" : tone === "yellow" ? "🟡" : "🔴"}</td>
                  <td className="px-3 py-2 whitespace-nowrap font-mono text-xs">{fmtTime(r.received_at)}</td>
                  <td className="px-3 py-2"><Pill tone={tone}>{r.event_type || "غير معروف"}</Pill></td>
                  <td className="px-3 py-2 font-mono">{r.salla_order_id || "—"}</td>
                  <td className="px-3 py-2">{r.items_count ?? "—"}</td>
                  <td className="px-3 py-2 text-xs">
                    {r.pipeline_stage_after
                      ? <Term code={r.pipeline_stage_after} kind="stage" />
                      : "—"}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{r.http_response_status}</td>
                  <td className="px-3 py-2 text-xs">
                    {r.skipped_reason
                      ? <TermPill code={r.skipped_reason} kind="reason" tone="yellow" />
                      : (r.items_parsed_ok
                          ? <TermPill code="Accepted" kind="general" tone="green" />
                          : <TermPill code="ParseFailed" kind="general" tone="red" />)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Detail drawer */}
      {selectedRow && (
        <div className="fixed inset-0 bg-black/40 z-40 flex justify-end" onClick={() => setSelectedRow(null)}>
          <div onClick={(e) => e.stopPropagation()}
               className="w-full max-w-xl bg-white dark:bg-zinc-950 h-full overflow-auto p-6 shadow-xl"
               data-testid="event-detail-drawer">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">تفاصيل الحدث</h2>
              <button onClick={() => setSelectedRow(null)}
                      className="text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
                      data-testid="drawer-close-btn">✕</button>
            </div>
            <dl className="grid grid-cols-3 gap-2 text-sm mb-4">
              <dt className="text-zinc-500"
                  title="معرّف فريد يربط كل خطوات معالجة الطلب من سلة إلى قيود">
                سجل التتبع
              </dt>
              <dd className="col-span-2 font-mono text-xs break-all">{selectedRow.trace_id || "—"}</dd>
              <dt className="text-zinc-500">وقت الاستلام</dt>
              <dd className="col-span-2 font-mono text-xs">{fmtTime(selectedRow.received_at)}</dd>
              <dt className="text-zinc-500">نوع الحدث</dt>
              <dd className="col-span-2">{selectedRow.event_type}</dd>
              <dt className="text-zinc-500">رقم الطلب</dt>
              <dd className="col-span-2 font-mono">{selectedRow.salla_order_id || "—"}</dd>
              <dt className="text-zinc-500">عدد العناصر</dt>
              <dd className="col-span-2">{selectedRow.items_count ?? "—"}</dd>
              <dt className="text-zinc-500"
                  title="هل البيانات الواردة بصيغة صالحة؟">
                البيانات صالحة
              </dt>
              <dd className="col-span-2">{selectedRow.items_parsed_ok ? "نعم" : "لا"}</dd>
              <dt className="text-zinc-500">سبب التجاهل</dt>
              <dd className="col-span-2">
                {selectedRow.skipped_reason
                  ? <TermPill code={selectedRow.skipped_reason} kind="reason" tone="yellow" showRaw />
                  : "—"}
              </dd>
              <dt className="text-zinc-500"
                  title="معرّف الصف المرتبط في صندوق الواردات الداخلي">
                معرّف صندوق الواردات
              </dt>
              <dd className="col-span-2 font-mono text-xs break-all">{selectedRow.target_inbox_row_id || "—"}</dd>
              <dt className="text-zinc-500">المرحلة بعد المعالجة</dt>
              <dd className="col-span-2">
                {selectedRow.pipeline_stage_after
                  ? <Term code={selectedRow.pipeline_stage_after} kind="stage" showRaw />
                  : "—"}
              </dd>
              <dt className="text-zinc-500">رد HTTP</dt>
              <dd className="col-span-2 font-mono">{selectedRow.http_response_status}</dd>
              <dt className="text-zinc-500">حجم البيانات الخام</dt>
              <dd className="col-span-2 font-mono">{selectedRow.raw_payload_size} بايت</dd>
            </dl>
            <details open>
              <summary className="cursor-pointer text-sm text-zinc-500 mb-2">جسم الطلب الكامل (JSON)</summary>
              <pre className="bg-zinc-50 dark:bg-zinc-900 p-3 rounded text-xs overflow-auto"
                   data-testid="event-detail-json">
                {JSON.stringify(selectedRow, null, 2)}
              </pre>
            </details>
          </div>
        </div>
      )}

      {/* by_event breakdown */}
      {Object.keys(counts.by_event || {}).length > 0 && (
        <div data-testid="by-event-breakdown">
          <div className="text-xs text-zinc-500 mb-2">توزيع الأحداث (آخر {windowHours} ساعة):</div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(counts.by_event).map(([et, n]) => (
              <Pill key={et} tone="default">{et}: <b className="font-mono">{n}</b></Pill>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
