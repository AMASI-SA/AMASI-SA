import { useState } from "react";
import api from "../lib/api";

const STATUS_STYLE = {
  "مطابق":        "bg-emerald-100 text-emerald-800 border-emerald-300",
  "فرق مبلغ":     "bg-amber-100 text-amber-800 border-amber-300",
  "في ميزان فقط": "bg-red-100 text-red-700 border-red-300",
  "في قيود فقط":  "bg-orange-100 text-orange-800 border-orange-300",
};
const TABS = ["الكل", "مطابق", "فرق مبلغ", "في ميزان فقط", "في قيود فقط"];

function CountCard({ label, value, active, onClick }) {
  return (
    <button onClick={onClick} data-testid={`recon-count-${label}`}
      className={`rounded-xl border px-4 py-3 text-right transition-colors ${
        active ? "border-slate-800 bg-slate-900 text-white"
               : "border-slate-200 bg-white hover:bg-slate-50"}`}>
      <div className="text-xs opacity-70">{label}</div>
      <div className="text-2xl font-bold">{value ?? 0}</div>
    </button>
  );
}

const fmt = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(2));

export default function QoyodReconciliation() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState("الكل");
  const [error, setError] = useState(null);

  const runReport = async () => {
    setLoading(true);
    try {
      const res = await api.get("/integrations/qoyod/reconciliation-report");
      if (res.data?.ok === false) {
        setError(res.data.error || "تعذر تشغيل تقرير المطابقة");
        setData(null);
      } else {
        setData(res.data);
        setError(null);
      }
    } catch (e) {
      setError(e?.response?.data?.detail || "تعذر تشغيل تقرير المطابقة");
    } finally {
      setLoading(false);
    }
  };

  const rows = (data?.rows || []).filter(
    (r) => tab === "الكل" || r.status === tab);
  const counts = data?.counts || {};

  return (
    <div className="space-y-6" dir="rtl" data-testid="qoyod-reconciliation-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">
            تقرير المطابقة — ميزان ↔ قيود
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            يقارن طلبات ميزان الناجحة مع فواتير قيود الفعلية (قراءة فقط — لا إرسال).
            {data?.sync_start_date && (
              <span className="mr-2 text-xs text-slate-400" data-testid="recon-sync-start-note">
                (النطاق: من {data.sync_start_date} حتى اليوم — تاريخ إنشاء الطلب في سلة)
              </span>
            )}
          </p>
        </div>
        <button onClick={runReport} disabled={loading}
          data-testid="recon-run-btn"
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-700 disabled:opacity-50">
          {loading ? "جارٍ المقارنة مع قيود..." : "تشغيل المطابقة الآن"}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700"
          data-testid="recon-error">{error}</div>
      )}

      {!data && !loading && !error && (
        <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500"
          data-testid="recon-empty-state">
          اضغط «تشغيل المطابقة الآن» لجلب فواتير قيود ومقارنتها بسجلات ميزان.
        </div>
      )}

      {data && (
        <>
          <div className={`rounded-xl border p-4 text-sm font-semibold ${
            data.all_matched
              ? "border-emerald-300 bg-emerald-50 text-emerald-800"
              : "border-amber-300 bg-amber-50 text-amber-800"}`}
            data-testid="recon-verdict">
            {data.all_matched
              ? `✓ مطابقة كاملة: ${counts["مطابق"] ?? 0} طلب في ميزان = ${data.qoyod_invoices_total} فاتورة في قيود — لا فروقات`
              : "⚠ توجد فروقات تحتاج مراجعة — راجع الجدول أدناه"}
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {TABS.slice(1).map((t) => (
              <CountCard key={t} label={t} value={counts[t]}
                active={tab === t}
                onClick={() => setTab(tab === t ? "الكل" : t)} />
            ))}
          </div>

          <div className="flex gap-2 flex-wrap">
            {TABS.map((t) => (
              <button key={t} onClick={() => setTab(t)}
                data-testid={`recon-tab-${t}`}
                className={`rounded-full border px-3 py-1 text-xs ${
                  tab === t ? "border-slate-800 bg-slate-900 text-white"
                            : "border-slate-300 bg-white text-slate-600"}`}>
                {t}
              </button>
            ))}
          </div>

          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm" data-testid="recon-table">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-right">رقم الطلب (سلة)</th>
                  <th className="px-3 py-2 text-right">تاريخ الإنشاء</th>
                  <th className="px-3 py-2 text-right">فاتورة قيود</th>
                  <th className="px-3 py-2 text-right">مبلغ ميزان</th>
                  <th className="px-3 py-2 text-right">مبلغ قيود</th>
                  <th className="px-3 py-2 text-right">الفرق</th>
                  <th className="px-3 py-2 text-right">الحالة</th>
                  <th className="px-3 py-2 text-right">ملاحظة</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr><td colSpan={8} className="px-3 py-6 text-center text-slate-400">
                    لا توجد سجلات في هذا التصنيف
                  </td></tr>
                )}
                {rows.map((r, i) => (
                  <tr key={`${r.qoyod_invoice_id}-${i}`}
                    className="border-t border-slate-100"
                    data-testid={`recon-row-${r.order_number || r.qoyod_invoice_id}`}>
                    <td className="px-3 py-2 font-mono">{r.order_number || "—"}</td>
                    <td className="px-3 py-2">{r.order_date || "—"}</td>
                    <td className="px-3 py-2 font-mono">
                      {r.qoyod_invoice_id}
                      {r.invoice_number ? ` (#${r.invoice_number})` : ""}
                    </td>
                    <td className="px-3 py-2">{fmt(r.mezan_total)}</td>
                    <td className="px-3 py-2">{fmt(r.qoyod_total)}</td>
                    <td className={`px-3 py-2 font-semibold ${
                      r.difference ? "text-red-600" : "text-slate-400"}`}>
                      {r.difference === null || r.difference === undefined
                        ? "—" : fmt(r.difference)}
                    </td>
                    <td className="px-3 py-2">
                      <span className={`rounded-full border px-2 py-0.5 text-xs ${
                        STATUS_STYLE[r.status] || ""}`}>{r.status}</span>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-500">{r.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="text-xs text-slate-400" data-testid="recon-meta">
            آخر تشغيل: {data.run_at} — طلبات ميزان المُرسلة: {data.mezan_sent_total} —
            فواتير قيود ضمن النطاق: {data.qoyod_invoices_total}
          </div>
        </>
      )}
    </div>
  );
}
