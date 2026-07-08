/**
 * Qoyod Reconciliation v2 — the SINGLE source of truth for
 * Mezan ↔ قيود parity.
 *
 * Flow (user directive 2026-07-09):
 *   1. Operator clicks "تشغيل المطابقة".
 *   2. Backend syncs invoices from Qoyod → local `qoyod_invoices`.
 *   3. Backend compares `unified_orders` (Salla side) vs the local
 *      `qoyod_invoices` (كسوة قيود المحلية).
 *   4. Five outcome labels: matched / needs Plan-B send / qoyod
 *      only / needs Repair Marker / amount mismatch.
 *
 * NO send button. NO edit. NO write-back to قيود. Read + sync + compare.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import api from "../lib/api";

const OUTCOMES = [
  "مطابق",
  "يحتاج إرسال Plan B",
  "موجود في قيود فقط",
  "يحتاج Repair Marker",
  "فرق مبلغ",
];

const STATUS_STYLE = {
  "مطابق":                "bg-emerald-100 text-emerald-800 border-emerald-300",
  "يحتاج إرسال Plan B":   "bg-red-100 text-red-800 border-red-300",
  "موجود في قيود فقط":     "bg-orange-100 text-orange-800 border-orange-300",
  "يحتاج Repair Marker":  "bg-indigo-100 text-indigo-800 border-indigo-300",
  "فرق مبلغ":              "bg-amber-100 text-amber-900 border-amber-300",
};

const TABS = ["الكل", ...OUTCOMES];

function CountCard({ label, value, active, onClick }) {
  return (
    <button
      onClick={onClick}
      data-testid={`recon-count-${label}`}
      className={`rounded-xl border px-4 py-3 text-right transition-colors ${
        active
          ? "border-slate-800 bg-slate-900 text-white"
          : "border-slate-200 bg-white hover:bg-slate-50"
      }`}
    >
      <div className="text-xs opacity-70">{label}</div>
      <div className="text-2xl font-bold">{value ?? 0}</div>
    </button>
  );
}

const fmt = (v) =>
  v === null || v === undefined || v === "" ? "—" : Number(v).toFixed(2);

function extractDetail(err) {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  return d?.message || err?.message || "خطأ غير معروف";
}

export default function QoyodReconciliation() {
  const PAGE_SIZE = 15;
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState("الكل");
  const [error, setError] = useState(null);
  const [syncFirst, setSyncFirst] = useState(true);
  const [page, setPage] = useState(1);

  const runReport = useCallback(
    async ({ withSync = true } = {}) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.get(
          "/integrations/qoyod/reconciliation-report",
          { params: { sync_first: withSync } },
        );
        if (res.data?.ok === false) {
          setError(res.data.error || "تعذر تشغيل تقرير المطابقة");
          setData({
            ok: false,
            sync_summary: res.data.sync_summary,
            counts: {}, rows: [],
          });
        } else {
          setData(res.data);
          setPage(1);
        }
      } catch (e) {
        setError(extractDetail(e));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  // On mount — auto-load the LATEST locally-saved comparison (no
  // sync). User directive 2026-07-09: the page must never appear
  // empty after refresh; the local `qoyod_invoices` table is
  // persistent and reconciliation must reflect it on every open.
  useEffect(() => {
    runReport({ withSync: false });
  }, [runReport]);

  const rows = useMemo(
    () =>
      (data?.rows || []).filter(
        (r) => tab === "الكل" || r.match === tab,
      ),
    [data, tab],
  );
  const counts = data?.counts || {};
  const sync = data?.sync_summary;

  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const startIdx = (currentPage - 1) * PAGE_SIZE;
  const pageRows = rows.slice(startIdx, startIdx + PAGE_SIZE);
  // Reset to first page when switching tab.
  const switchTab = (t) => {
    setTab(t);
    setPage(1);
  };

  return (
    <div
      className="space-y-6"
      dir="rtl"
      data-testid="qoyod-reconciliation-page"
    >
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="max-w-3xl">
          <h1 className="text-2xl font-bold text-slate-900">
            تقرير المطابقة — ميزان ↔ قيود
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            مصدر الحقيقة الوحيد للمقارنة. عند الضغط على &quot;تشغيل
            المطابقة&quot; النظام يقوم أولاً بجلب فواتير قيود إلى
            الجدول المحلي <code className="font-mono">qoyod_invoices</code>{" "}
            ثم يقارن مع طلبات سلة في{" "}
            <code className="font-mono">unified_orders</code> (نفس مصدر
            صفحة الطلبات). النطاق: من{" "}
            {data?.sync_start_date || "2026-07-01"} حتى اليوم.
          </p>
          <p className="text-[11px] text-slate-400 mt-1">
            🔒 قراءة فقط — لا زر إرسال، لا تعديل، ولا كتابة على قيود.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs"
            data-testid="recon-sync-first-toggle"
          >
            <input
              type="checkbox"
              checked={syncFirst}
              onChange={(e) => setSyncFirst(e.target.checked)}
            />
            مزامنة قيود قبل المطابقة
          </label>
          <button
            onClick={() => runReport({ withSync: syncFirst })}
            disabled={loading}
            data-testid="recon-run-btn"
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {loading
              ? syncFirst
                ? "جاري المزامنة والمقارنة…"
                : "جاري التحميل…"
              : "تشغيل المطابقة الآن"}
          </button>
        </div>
      </div>

      {error && (
        <div
          className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700"
          data-testid="recon-error"
        >
          {String(error)}
        </div>
      )}

      {!data && !loading && !error && (
        <div
          className="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500"
          data-testid="recon-empty-state"
        >
          لا توجد بيانات محلية بعد. اضغط &quot;تشغيل المطابقة الآن&quot;
          لجلب فواتير قيود وحفظها.
        </div>
      )}

      {data && (
        <>
          {/* Sync summary strip */}
          {sync?.ran && (
            <div
              className={`rounded-xl border p-3 text-sm ${
                sync.ok
                  ? "border-sky-200 bg-sky-50 text-sky-900"
                  : "border-red-200 bg-red-50 text-red-800"
              }`}
              data-testid="recon-sync-summary"
            >
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="font-semibold">
                  {sync.ok ? "✅ اكتمل جلب فواتير قيود:" : "⚠️ فشل الجلب:"}
                </span>
                {sync.ok ? (
                  <>
                    <span dir="ltr" className="font-mono text-xs">
                      fetched=
                      <b>{sync.fetched ?? 0}</b> · in_scope=
                      <b>{sync.in_scope ?? 0}</b> · created=
                      <b>{sync.created ?? 0}</b> · updated=
                      <b>{sync.updated ?? 0}</b> · skipped=
                      <b>{sync.skipped ?? 0}</b>
                    </span>
                    <span className="text-xs opacity-70">
                      في{" "}
                      <span dir="ltr" className="font-mono">
                        {sync.duration_ms ?? 0}ms
                      </span>
                    </span>
                  </>
                ) : (
                  <span className="text-xs">{sync.error}</span>
                )}
              </div>
            </div>
          )}

          {/* Overall verdict */}
          <div
            className={`rounded-xl border p-4 text-sm font-semibold ${
              data.all_matched
                ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                : "border-amber-300 bg-amber-50 text-amber-800"
            }`}
            data-testid="recon-verdict"
          >
            {data.all_matched
              ? `✓ مطابقة كاملة: ${counts["مطابق"] ?? 0} طلب في سلة = ${data.qoyod_invoices_total} فاتورة في قيود المحلية — لا فروقات`
              : "⚠ توجد فروقات تحتاج مراجعة — راجع الجدول أدناه"}
          </div>

          {/* Source totals — 2 informational cards */}
          <div className="grid grid-cols-2 gap-3">
            <div
              className="rounded-xl border border-sky-200 bg-sky-50 p-4"
              data-testid="recon-count-فواتير-قيود"
            >
              <div className="text-xs text-sky-800 opacity-80">
                فواتير قيود (محفوظة محلياً)
              </div>
              <div className="text-3xl font-bold text-sky-900">
                {data.qoyod_invoices_total ?? 0}
              </div>
              <div className="text-[10px] text-sky-700 opacity-70">
                مصدر المقارنة من جهة قيود
              </div>
            </div>
            <div
              className="rounded-xl border border-indigo-200 bg-indigo-50 p-4"
              data-testid="recon-count-طلبات-سلة-المؤهلة"
            >
              <div className="text-xs text-indigo-800 opacity-80">
                طلبات سلة المؤهلة
              </div>
              <div className="text-3xl font-bold text-indigo-900">
                {data.salla_orders_total ?? 0}
              </div>
              <div className="text-[10px] text-indigo-700 opacity-70">
                تم التنفيذ / جاري التوصيل / تم التوصيل — منذ{" "}
                {data.sync_start_date || "2026-07-01"}
              </div>
            </div>
          </div>

          {/* 5 outcome counts */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            {OUTCOMES.map((t) => (
              <CountCard
                key={t}
                label={t}
                value={counts[t]}
                active={tab === t}
                onClick={() => switchTab(tab === t ? "الكل" : t)}
              />
            ))}
          </div>

          {/* Tabs */}
          <div className="flex gap-2 flex-wrap">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => switchTab(t)}
                data-testid={`recon-tab-${t}`}
                className={`rounded-full border px-3 py-1 text-xs ${
                  tab === t
                    ? "border-slate-800 bg-slate-900 text-white"
                    : "border-slate-300 bg-white text-slate-600"
                }`}
              >
                {t}
              </button>
            ))}
          </div>

          {/* Reconciliation table — user directive columns */}
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table
              className="w-full text-sm"
              data-testid="recon-table"
            >
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-right">رقم الطلب</th>
                  <th className="px-3 py-2 text-right">فاتورة قيود</th>
                  <th className="px-3 py-2 text-right">تاريخ سلة</th>
                  <th className="px-3 py-2 text-right">تاريخ قيود</th>
                  <th className="px-3 py-2 text-right">العميل</th>
                  <th className="px-3 py-2 text-right">إجمالي سلة</th>
                  <th className="px-3 py-2 text-right">إجمالي قيود</th>
                  <th className="px-3 py-2 text-right">المدفوع</th>
                  <th className="px-3 py-2 text-right">المتبقي</th>
                  <th className="px-3 py-2 text-right">حالة قيود</th>
                  <th className="px-3 py-2 text-right">نتيجة المطابقة</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr>
                    <td
                      colSpan={11}
                      className="px-3 py-6 text-center text-slate-400"
                      data-testid="recon-empty-rows"
                    >
                      لا توجد سجلات في هذا التصنيف
                    </td>
                  </tr>
                )}
                {pageRows.map((r, i) => (
                  <tr
                    key={`${r.qoyod_invoice_id || r.order_number || i}-${i}`}
                    className="border-t border-slate-100 hover:bg-slate-50"
                    data-testid={`recon-row-${r.order_number || r.qoyod_invoice_id}`}
                  >
                    <td className="px-3 py-2 font-mono">
                      {r.order_number || "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {r.qoyod_invoice_id || "—"}
                      {r.invoice_number &&
                        r.invoice_number !== r.qoyod_invoice_id && (
                          <div className="text-[10px] text-slate-500">
                            #{r.invoice_number}
                          </div>
                        )}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs" dir="ltr">
                      {r.salla_date || "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs" dir="ltr">
                      {r.qoyod_date || "—"}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {r.customer_name || "—"}
                    </td>
                    <td className="px-3 py-2 font-mono" dir="ltr">
                      {fmt(r.salla_total)}
                    </td>
                    <td className="px-3 py-2 font-mono" dir="ltr">
                      {fmt(r.qoyod_total)}
                    </td>
                    <td className="px-3 py-2 font-mono" dir="ltr">
                      {fmt(r.paid_amount)}
                    </td>
                    <td
                      className={`px-3 py-2 font-mono ${
                        r.remaining && Math.abs(r.remaining) > 0.01
                          ? "text-amber-700 font-bold"
                          : "text-slate-500"
                      }`}
                      dir="ltr"
                    >
                      {fmt(r.remaining)}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <span className="rounded-full bg-slate-100 px-2 py-0.5">
                        {r.qoyod_status || "—"}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`inline-block rounded-full border px-2 py-0.5 text-xs ${
                          STATUS_STYLE[r.match] || ""
                        }`}
                        data-testid={`recon-match-${r.order_number || r.qoyod_invoice_id}`}
                      >
                        {r.match}
                      </span>
                      {r.difference !== null &&
                        r.difference !== undefined &&
                        Math.abs(r.difference) > 0.005 && (
                          <div
                            dir="ltr"
                            className="mt-0.5 font-mono text-[10px] text-amber-700"
                          >
                            Δ {fmt(r.difference)}
                          </div>
                        )}
                      {r.note && (
                        <div className="mt-0.5 text-[10px] text-slate-500">
                          {r.note}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {rows.length > 0 && (
              <div
                className="flex items-center justify-between gap-3 border-t border-slate-100 bg-slate-50 px-3 py-2 text-sm"
                data-testid="recon-pagination"
              >
                <div className="text-slate-500">
                  عرض{" "}
                  <span dir="ltr" className="font-mono">
                    {startIdx + 1}–
                    {Math.min(startIdx + PAGE_SIZE, rows.length)}
                  </span>{" "}
                  من{" "}
                  <span dir="ltr" className="font-mono">
                    {rows.length}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={currentPage <= 1}
                    data-testid="recon-prev-page"
                    className={`rounded-lg border px-3 py-1.5 text-xs ${
                      currentPage <= 1
                        ? "border-slate-200 bg-slate-100 text-slate-400"
                        : "border-slate-300 bg-white text-slate-700 hover:bg-slate-100"
                    }`}
                  >
                    → السابق
                  </button>
                  <span
                    dir="ltr"
                    className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-mono"
                    data-testid="recon-page-indicator"
                  >
                    {currentPage} / {totalPages}
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      setPage((p) => Math.min(totalPages, p + 1))
                    }
                    disabled={currentPage >= totalPages}
                    data-testid="recon-next-page"
                    className={`rounded-lg border px-3 py-1.5 text-xs ${
                      currentPage >= totalPages
                        ? "border-slate-200 bg-slate-100 text-slate-400"
                        : "border-slate-300 bg-white text-slate-700 hover:bg-slate-100"
                    }`}
                  >
                    التالي ←
                  </button>
                </div>
              </div>
            )}
          </div>

          <div
            className="text-xs text-slate-400"
            data-testid="recon-meta"
          >
            آخر تشغيل:{" "}
            <span dir="ltr" className="font-mono">
              {data.run_at}
            </span>{" "}
            — طلبات سلة المؤهلة: {data.salla_orders_total ?? 0} — فواتير قيود
            المحلية: {data.qoyod_invoices_total ?? 0}
          </div>
        </>
      )}
    </div>
  );
}
