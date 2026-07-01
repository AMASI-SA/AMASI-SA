/**
 * Eligible Orders — Read-Only Audit (Iter-001 Phase B).
 *
 * Purpose
 * ───────
 * Surface every Salla order that COULD be invoiced to قيود but is
 * currently blocked, missing, or already sent. Uses the read-only
 * endpoint `GET /api/integrations/qoyod/admin/eligible-orders`.
 *
 * READ-ONLY CONTRACT (STRICT)
 * ───────────────────────────
 *   • NO Send / Approve / Bypass / Repair buttons.
 *   • NO write endpoints called.
 *   • Only actions: Refresh, Open Preview (existing safe endpoint),
 *     Copy trace_id, Copy order_number.
 *   • Gates (production_writes_locked / selective_live_send_enabled)
 *     are displayed but NEVER toggled here.
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const CLASSIFICATION_META = [
  { key: "ready_for_preview",             label: "مرشح للـ Preview",         color: "emerald" },
  { key: "ready_for_manual_approval",     label: "بانتظار اعتماد يدوي",       color: "amber"   },
  { key: "already_sent",                  label: "أُرسلت للفوترة",           color: "sky"     },
  { key: "blocked_customer",              label: "عميل غير مربوط",           color: "rose"    },
  { key: "blocked_product",               label: "منتج غير مربوط",           color: "rose"    },
  { key: "blocked_bank_transfer_routing", label: "تحويل بنكي (Iter-294)",   color: "orange"  },
  { key: "blocked_status",                label: "طريقة دفع غير مدعومة",    color: "rose"    },
  { key: "totals_mismatch",               label: "إجمالي غير مطابق",         color: "orange"  },
  { key: "missing_from_pipeline",         label: "مفقود من الـ Pipeline",    color: "purple"  },
  { key: "unclassified_needs_review",     label: "يحتاج مراجعة",             color: "slate"   },
];

const COLOR_STYLE = {
  emerald: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  amber:   "bg-amber-500/10   text-amber-300   border-amber-500/30",
  sky:     "bg-sky-500/10     text-sky-300     border-sky-500/30",
  rose:    "bg-rose-500/10    text-rose-300    border-rose-500/30",
  orange:  "bg-orange-500/10  text-orange-300  border-orange-500/30",
  purple:  "bg-purple-500/10  text-purple-300  border-purple-500/30",
  slate:   "bg-slate-500/10   text-slate-300   border-slate-500/30",
};

async function copyToClipboard(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`تم نسخ ${label}`);
  } catch {
    toast.error("فشل النسخ");
  }
}

export default function EligibleOrders() {
  const [report, setReport]     = useState(null);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  const [filterClass, setFilterClass]     = useState("all");
  const [filterPayment, setFilterPayment] = useState("all");
  const [filterStatus, setFilterStatus]   = useState("all");
  const [showAlreadySent, setShowAlreadySent] = useState(false);
  const [showExcludedPanel, setShowExcludedPanel] = useState(false);
  const [sinceDays] = useState(90);

  const fetchReport = async () => {
    setLoading(true); setError(null);
    try {
      const token = localStorage.getItem("token");
      const { data } = await axios.get(
        `${API}/integrations/qoyod/admin/eligible-orders`,
        {
          params: { since_days: sinceDays, limit: 200,
                    show_already_sent: showAlreadySent },
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      setReport(data);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || "خطأ");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchReport(); }, [showAlreadySent]);

  const uniquePayments = useMemo(() => {
    if (!report?.items) return [];
    return Array.from(new Set(report.items.map(i => i.payment_method
      ).filter(Boolean))).sort();
  }, [report]);
  const uniqueStatuses = useMemo(() => {
    if (!report?.items) return [];
    return Array.from(new Set(report.items.map(i => i.status).filter(
      Boolean))).sort();
  }, [report]);

  const filteredItems = useMemo(() => {
    if (!report?.items) return [];
    return report.items.filter(it => {
      if (filterClass !== "all" && it.classification !== filterClass)
        return false;
      if (filterPayment !== "all" && it.payment_method !== filterPayment)
        return false;
      if (filterStatus !== "all" && it.status !== filterStatus)
        return false;
      return true;
    });
  }, [report, filterClass, filterPayment, filterStatus]);

  const openPreview = (traceId) => {
    if (!traceId) { toast.error("لا يوجد trace_id لهذا الطلب"); return; }
    const url = `/integrations/qoyod/pending-orders?preview=${
      encodeURIComponent(traceId)}`;
    window.open(url, "_blank");
  };

  return (
    <div dir="rtl" className="p-6 space-y-6"
         data-testid="eligible-orders-page">
      {/* Read-Only Banner */}
      <div className="rounded-lg border border-amber-500/40 bg-amber-500/10
                      p-4 flex items-center gap-3"
           data-testid="eligible-orders-readonly-banner">
        <span className="text-2xl">🕰️</span>
        <div>
          <div className="text-amber-200 font-semibold">
            READ-ONLY — لا يوجد إرسال إلى قيود
          </div>
          <div className="text-amber-100/80 text-sm mt-1">
            هذه الصفحة تشخيصية فقط. الأزرار المسموحة: Refresh /
            Open Preview / Copy. لا Approve / Send / Bypass.
          </div>
        </div>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">
            📋 الطلبات المؤهلة (Audit)
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            آخر {sinceDays} يوم — كل الطلبات في سلة/الـ pipeline
            التي يمكن فوترتها في قيود.
          </p>
        </div>
        <button
          onClick={fetchReport}
          disabled={loading}
          className="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-700
                     text-white font-medium transition disabled:opacity-50"
          data-testid="btn-refresh"
        >
          {loading ? "جارٍ التحميل…" : "🔄 تحديث"}
        </button>
      </div>

      {/* Gates */}
      {report?.gates && (
        <div className="flex flex-wrap gap-2 text-sm"
             data-testid="eligible-orders-gates">
          <GateBadge
            testid="gate-production-writes-locked"
            active={report.gates.production_writes_locked}
            labelOn="🔒 Production Writes Locked"
            labelOff="🔓 Production Writes UNLOCKED (خطر)"
          />
          <GateBadge
            testid="gate-selective-live-send-enabled"
            active={!report.gates.selective_live_send_enabled}
            labelOn="⛔ Selective Live Send Disabled"
            labelOff="✅ Selective Live Send Enabled"
          />
          <span className="px-3 py-1 rounded-full border border-slate-600
                           text-slate-300 bg-slate-800/50">
            Source: {report.source_mode}
          </span>
        </div>
      )}

      {error && (
        <div className="rounded-lg bg-rose-500/10 border border-rose-500/40
                        text-rose-200 p-4"
             data-testid="eligible-orders-error">
          خطأ: {String(error)}
        </div>
      )}

      {/* Counts */}
      {report && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3"
             data-testid="eligible-orders-counts">
          {CLASSIFICATION_META.map(m => (
            <button
              key={m.key}
              onClick={() => setFilterClass(m.key === filterClass ?
                "all" : m.key)}
              className={`rounded-lg border p-3 text-right transition
                          ${COLOR_STYLE[m.color]}
                          ${filterClass === m.key ?
                            "ring-2 ring-white/40" : ""}`}
              data-testid={`count-${m.key}`}
            >
              <div className="text-xs opacity-80">{m.label}</div>
              <div className="text-2xl font-bold mt-1">
                {report.counts?.[m.key] ?? 0}
              </div>
            </button>
          ))}
        </div>
      )}

      {/* Invariant */}
      {report && (
        <div className="text-xs text-slate-400 flex flex-wrap gap-4"
             data-testid="eligible-orders-invariant">
          <span>total_scanned = <b>{report.total_scanned}</b></span>
          <span>classified = <b>{report.total_classified}</b></span>
          <span>excluded = <b>{report.excluded_status_count}</b></span>
          <span>hidden(already_sent) = <b>
            {report.total_hidden_already_sent}</b></span>
          <span>returned = <b>{report.total_returned_items}</b></span>
          <span className={report.invariant_holds ?
            "text-emerald-400" : "text-rose-400"}>
            invariant = {String(report.invariant_holds)}
          </span>
        </div>
      )}

      {/* Excluded Reasons Panel (Iter-001e) — collapsible, read-only.
          Shows exactly why rows were excluded so operators can verify
          the underscore/space normalization worked. */}
      {report && (
        <div className="rounded-lg border border-slate-700 bg-slate-900/40"
             data-testid="excluded-reasons-panel">
          <button
            onClick={() => setShowExcludedPanel(v => !v)}
            className="w-full flex items-center justify-between p-3
                       text-right hover:bg-slate-800/50 transition"
            data-testid="excluded-reasons-toggle"
          >
            <span className="text-slate-200 font-medium">
              🔍 تفاصيل الاستبعاد ({report.excluded_status_count || 0}
              &nbsp;طلب مستبعد)
            </span>
            <span className="text-slate-400 text-xs">
              {showExcludedPanel ? "▲ إخفاء" : "▼ عرض"}
            </span>
          </button>
          {showExcludedPanel && (
            <div className="p-4 border-t border-slate-700 space-y-4"
                 data-testid="excluded-reasons-body">
              {/* excluded_reason_counts */}
              <div>
                <div className="text-slate-300 text-sm font-semibold mb-2">
                  أسباب الاستبعاد (excluded_reason_counts)
                </div>
                {Object.keys(report.excluded_reason_counts || {}).length
                  === 0 ? (
                  <div className="text-slate-500 text-xs"
                       data-testid="excluded-reasons-empty">
                    لا يوجد أسباب استبعاد — كل الطلبات المقروءة مؤهلة.
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(report.excluded_reason_counts)
                      .sort((a, b) => b[1] - a[1])
                      .map(([key, count]) => (
                        <span
                          key={key}
                          className="px-2 py-1 rounded border
                                     border-rose-500/30 bg-rose-500/10
                                     text-rose-200 text-xs font-mono"
                          data-testid={`excluded-reason-${key}`}
                        >
                          {key} <b className="text-rose-100">{count}</b>
                        </span>
                      ))}
                  </div>
                )}
              </div>

              {/* total_eligible_by_status */}
              {report.total_eligible_by_status &&
               Object.keys(report.total_eligible_by_status).length > 0 && (
                <div>
                  <div className="text-slate-300 text-sm font-semibold mb-2">
                    ✅ حالات مقبولة (بعد التطبيع)
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(report.total_eligible_by_status)
                      .sort((a, b) => b[1] - a[1])
                      .map(([key, count]) => (
                        <span
                          key={key}
                          className="px-2 py-1 rounded border
                                     border-emerald-500/30
                                     bg-emerald-500/10 text-emerald-200
                                     text-xs font-mono"
                          data-testid={`eligible-status-${key}`}
                        >
                          {key} <b className="text-emerald-100">{count}</b>
                        </span>
                      ))}
                  </div>
                </div>
              )}

              {/* total_ineligible_by_status */}
              {report.total_ineligible_by_status &&
               Object.keys(report.total_ineligible_by_status).length > 0 && (
                <div>
                  <div className="text-slate-300 text-sm font-semibold mb-2">
                    ⛔ حالات مستبعدة
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(report.total_ineligible_by_status)
                      .sort((a, b) => b[1] - a[1])
                      .map(([key, count]) => (
                        <span
                          key={key}
                          className="px-2 py-1 rounded border
                                     border-slate-500/40 bg-slate-500/10
                                     text-slate-200 text-xs font-mono"
                          data-testid={`ineligible-status-${key}`}
                        >
                          {key} <b className="text-slate-100">{count}</b>
                        </span>
                      ))}
                  </div>
                </div>
              )}

              <div className="text-xs text-slate-500 pt-2 border-t
                              border-slate-800">
                ملاحظة: التطبيع يعامل الشرطة السفلية والمسافة على أنهما
                متساويان (مثال: <code>جاري_التوصيل</code> ={" "}
                <code>جاري التوصيل</code>).
              </div>
            </div>
          )}
        </div>
      )}

      {/* Filters */}
      {report && (
        <div className="flex flex-wrap gap-3 items-center"
             data-testid="eligible-orders-filters">
          <select value={filterClass}
                  onChange={e => setFilterClass(e.target.value)}
                  className="bg-slate-800 border border-slate-600
                             rounded px-3 py-1 text-white text-sm"
                  data-testid="filter-classification">
            <option value="all">كل التصنيفات</option>
            {CLASSIFICATION_META.map(m => (
              <option key={m.key} value={m.key}>{m.label}</option>
            ))}
          </select>
          <select value={filterPayment}
                  onChange={e => setFilterPayment(e.target.value)}
                  className="bg-slate-800 border border-slate-600
                             rounded px-3 py-1 text-white text-sm"
                  data-testid="filter-payment">
            <option value="all">كل طرق الدفع</option>
            {uniquePayments.map(p => (<option key={p} value={p}>{p}</option>))}
          </select>
          <select value={filterStatus}
                  onChange={e => setFilterStatus(e.target.value)}
                  className="bg-slate-800 border border-slate-600
                             rounded px-3 py-1 text-white text-sm"
                  data-testid="filter-status">
            <option value="all">كل الحالات</option>
            {uniqueStatuses.map(s => (<option key={s} value={s}>{s}</option>))}
          </select>
          <label className="text-sm text-slate-300 flex items-center gap-2">
            <input type="checkbox" checked={showAlreadySent}
                   onChange={e => setShowAlreadySent(e.target.checked)}
                   data-testid="toggle-show-already-sent" />
            أظهر المُرسَلة (already_sent)
          </label>
          <span className="text-xs text-slate-500 ms-auto">
            {filteredItems.length} من {report.total_returned_items}
          </span>
        </div>
      )}

      {/* Table */}
      {report && (
        <div className="overflow-x-auto rounded-lg border border-slate-700"
             data-testid="eligible-orders-table-wrap">
          <table className="w-full text-sm text-slate-200"
                 data-testid="eligible-orders-table">
            <thead className="bg-slate-800 text-slate-300">
              <tr>
                <th className="p-2 text-right">رقم الطلب</th>
                <th className="p-2 text-right">التاريخ</th>
                <th className="p-2 text-right">الحالة</th>
                <th className="p-2 text-right">طريقة الدفع</th>
                <th className="p-2 text-right">الإجمالي</th>
                <th className="p-2 text-right">التصنيف</th>
                <th className="p-2 text-right">السبب</th>
                <th className="p-2 text-right">إجراءات</th>
              </tr>
            </thead>
            <tbody>
              {filteredItems.length === 0 && (
                <tr><td colSpan="8" className="p-6 text-center
                        text-slate-500" data-testid="empty-row">
                  لا توجد نتائج تطابق الفلاتر
                </td></tr>
              )}
              {filteredItems.map((it, idx) => {
                const meta = CLASSIFICATION_META.find(
                  m => m.key === it.classification);
                return (
                  <tr key={idx} className="border-t border-slate-700
                          hover:bg-slate-800/50"
                      data-testid={`row-${it.order_number}`}>
                    <td className="p-2 font-mono">{it.order_number}</td>
                    <td className="p-2 text-xs">
                      {it.created_at?.slice(0, 10) || "-"}</td>
                    <td className="p-2">{it.status || "-"}</td>
                    <td className="p-2">{it.payment_method || "-"}</td>
                    <td className="p-2 text-left"
                        dir="ltr">{it.total_amount?.toFixed(2)}</td>
                    <td className="p-2">
                      <span className={`inline-block px-2 py-0.5
                              rounded border text-xs
                              ${COLOR_STYLE[meta?.color || "slate"]}`}>
                        {meta?.label || it.classification}
                      </span>
                    </td>
                    <td className="p-2 text-xs text-slate-400 max-w-xs
                        truncate" title={it.blocker_reason || ""}>
                      {it.blocker_reason || "-"}</td>
                    <td className="p-2">
                      <div className="flex gap-1 flex-wrap">
                        <button
                          onClick={() => copyToClipboard(
                            it.order_number, "رقم الطلب")}
                          className="text-xs px-2 py-0.5 rounded
                                     bg-slate-700 hover:bg-slate-600"
                          data-testid={
                            `copy-order-${it.order_number}`}>
                          📋 رقم
                        </button>
                        {it.latest_trace_id && (
                          <button
                            onClick={() => copyToClipboard(
                              it.latest_trace_id, "trace_id")}
                            className="text-xs px-2 py-0.5 rounded
                                       bg-slate-700 hover:bg-slate-600"
                            data-testid={
                              `copy-trace-${it.order_number}`}>
                            📋 trace
                          </button>
                        )}
                        {it.latest_trace_id && (
                          <button
                            onClick={() => openPreview(it.latest_trace_id)}
                            className="text-xs px-2 py-0.5 rounded
                                       bg-sky-700 hover:bg-sky-600
                                       text-white"
                            data-testid={
                              `open-preview-${it.order_number}`}>
                            🔍 Preview
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function GateBadge({ active, labelOn, labelOff, testid }) {
  return (
    <span
      data-testid={testid}
      className={`px-3 py-1 rounded-full border text-sm ${
        active ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-300"
               : "border-rose-500/50 bg-rose-500/10 text-rose-300"}`}>
      {active ? labelOn : labelOff}
    </span>
  );
}
