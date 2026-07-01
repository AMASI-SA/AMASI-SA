/**
 * Iter-293.5 — Qoyod Pending Orders (read-only surface).
 *
 * Purpose
 * ───────
 * Categorised view of every inbox row that is not COMPLETED. The
 * operator uses this page to triage rows into the seven categories
 * emitted by GET /admin/qoyod/pending-orders. The page is 100%
 * read-only:
 *
 *   • No approve-and-send button (endpoint doesn't exist yet).
 *   • No one-shot-reprocess.
 *   • Only the safe Preview endpoint is reachable, from the row's
 *     detail drawer.
 *   • `production_writes_locked` and `selective_live_send_enabled`
 *     are surfaced but NEVER toggled from here.
 *
 * Server contract
 * ───────────────
 * See backend/integrations/qoyod/routes.py — admin_qoyod_pending_orders.
 */
import { useEffect, useState, useMemo } from "react";
import axios from "axios";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const CATEGORY_META = [
  // Iter-293.5 — Renamed from "Ready to Send" to "Candidate" —
  // a row here is BILLABLE but MUST pass Preview + sendability
  // before it can be sent. True readiness is proven per-row via
  // Preview, not by simple category membership.
  { key: "ready_to_send",         label: "مرشح للإرسال (Candidate)", color: "emerald" },
  { key: "needs_mapping",         label: "يحتاج ربط",         color: "amber"   },
  { key: "bank_transfer_hold",    label: "تحويل بنكي (سداد لاحق)", color: "sky" },
  { key: "cod",                   label: "COD",              color: "indigo"  },
  { key: "unsupported_method",    label: "طريقة دفع غير مدعومة", color: "rose" },
  { key: "total_rounding_review", label: "مراجعة الإجمالي",    color: "orange"  },
  { key: "stale_or_cancelled",    label: "ملغي/قديم",         color: "slate"   },
];

const STAGE_LABELS = {
  LOCKED_AWAITING_APPROVAL:              "بانتظار موافقة",
  UNRESOLVED_QOYOD_DEPENDENCY:           "تبعية غير محلولة",
  BANK_TRANSFER_PAYMENT_ROUTING_PENDING: "سداد بنكي مؤجل",
  HOLD_COD_PENDING_FIX:                  "COD يحتاج إصلاح",
  HOLD_UNSUPPORTED_PAYMENT_METHOD:       "طريقة دفع غير مدعومة",
  INVOICE_CREATED_TOTAL_MISMATCH:        "فرق إجمالي > 0.01",
  STALE_TRACE_NOT_CURRENT_ORDER_STATE:   "trace قديم",
  FAILED_INVOICE:                        "فشل الفاتورة",
  DEAD_LETTER:                           "مُهمَل (dead-letter)",
};

export default function QoyodPendingOrders() {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab,     setTab]     = useState("ready_to_send");
  const [selectedRow, setSelectedRow] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const { data } = await axios.get(
        `${API}/integrations/qoyod/admin/qoyod/pending-orders?limit=200`
      );
      setData(data);
    } catch (e) {
      toast.error(e.response?.data?.detail?.message || e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function runPreview(row) {
    setPreviewing(true);
    setPreview(null);
    try {
      // Iter-293.5 fix — a single order_number may have multiple
      // inbox traces (SKIPPED → COMPLETED → SKIPPED etc). We MUST
      // disambiguate by trace_id so preview-reprocess targets THIS
      // specific row, not "any" match.
      const { data } = await axios.post(
        `${API}/integrations/qoyod/admin/preview-reprocess`,
        {
          trace_id:     row.trace_id,
          order_number: row.salla_order_number || row.salla_order_id,
        }
      );
      setPreview(data);
    } catch (e) {
      toast.error(e.response?.data?.detail?.message
                  || e.response?.data?.detail?.code
                  || e.message);
    } finally {
      setPreviewing(false);
    }
  }

  const counts = data?.counts || {};
  const flagOn = Boolean(data?.selective_live_send_enabled);
  const lockOn = Boolean(data?.production_writes_locked);

  const activeRows = useMemo(
    () => (data?.categories?.[tab]) || [],
    [data, tab]
  );

  return (
    <div className="max-w-7xl mx-auto p-4 md:p-6" data-testid="qoyod-pending-orders-page">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold text-slate-900">
            🗂️ طلبات قيود المعلقة
          </h1>
          <p className="text-sm text-slate-600 mt-1">
            Qoyod Manual Processing Queue — عرض قراءة فقط.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          data-testid="btn-refresh"
          disabled={loading}
          className="px-3 py-1.5 text-xs font-bold rounded bg-slate-200 hover:bg-slate-300 disabled:opacity-40"
        >
          {loading ? "…" : "تحديث"}
        </button>
      </div>

      {/* Read-only status banner — never omitted, always accurate. */}
      <div
        data-testid="read-only-banner"
        className={`mb-4 rounded-lg border-2 p-3 ${
          flagOn ? "border-emerald-300 bg-emerald-50"
                 : "border-amber-300 bg-amber-50"}`}
      >
        <div className="flex items-center gap-3 flex-wrap text-[12px]">
          <span className="font-extrabold text-amber-900">
            الوضع الحالي: قراءة فقط
          </span>
          <span className="font-mono">
            production_writes_locked =
            <b className={lockOn ? "text-emerald-700" : "text-rose-700"}>
              {" "}{String(lockOn)}
            </b>
            {data?.lock_source && (
              <span className="text-slate-500 mr-1">
                {" "}<i>({data.lock_source})</i>
              </span>
            )}
          </span>
          <span className="font-mono">
            selective_live_send_enabled =
            <b className={flagOn ? "text-emerald-700" : "text-rose-700"}>
              {" "}{String(flagOn)}
            </b>
          </span>
          <span className="text-slate-600">
            — لا يوجد إرسال فعلي إلى قيود من هذه الصفحة.
          </span>
        </div>
        <div className="mt-2 text-[11px] text-slate-700">
          Preview فقط متاح لكل طلب. لا Approve & Send. لا one-shot. لا batch.
          لا backfill. لا فتح للـ global lock.
        </div>
      </div>

      {/* Tabs */}
      <div className="flex flex-wrap gap-1 border-b border-slate-200 mb-3"
           data-testid="pending-tabs">
        {CATEGORY_META.map(c => {
          const active = tab === c.key;
          const count  = counts[c.key] ?? 0;
          return (
            <button
              key={c.key}
              type="button"
              onClick={() => setTab(c.key)}
              data-testid={`tab-${c.key}`}
              className={`px-3 py-2 text-xs font-bold rounded-t transition-colors ${
                active
                  ? `bg-${c.color}-600 text-white`
                  : "bg-slate-100 hover:bg-slate-200 text-slate-700"
              }`}
            >
              {c.label} <span className="opacity-80">({count})</span>
            </button>
          );
        })}
      </div>

      {/* Rows table */}
      {loading ? (
        <div className="text-center text-slate-500 py-10">جارِ التحميل…</div>
      ) : activeRows.length === 0 ? (
        <div className="text-center text-slate-400 py-10 text-sm"
             data-testid="empty-category">
          لا يوجد طلبات في هذه الفئة.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px] border border-slate-200"
                 data-testid="pending-orders-table">
            <thead className="bg-slate-100">
              <tr className="text-right">
                <th className="p-2 border-b border-slate-200">رقم الطلب</th>
                <th className="p-2 border-b border-slate-200">trace_id</th>
                <th className="p-2 border-b border-slate-200">طريقة الدفع</th>
                <th className="p-2 border-b border-slate-200">حالة سلة</th>
                <th className="p-2 border-b border-slate-200">مبلغ سلة</th>
                <th className="p-2 border-b border-slate-200">pipeline_stage</th>
                <th className="p-2 border-b border-slate-200">reason</th>
                <th className="p-2 border-b border-slate-200">فاتورة قيود؟</th>
                <th className="p-2 border-b border-slate-200">sendable</th>
                <th className="p-2 border-b border-slate-200">إجراءات</th>
              </tr>
            </thead>
            <tbody>
              {activeRows.map(r => (
                <tr key={r.row_id}
                    className="hover:bg-slate-50 border-b border-slate-100"
                    data-testid={`row-${r.row_id}`}>
                  <td className="p-2 font-mono">
                    {r.salla_order_number || r.salla_order_id || "—"}
                  </td>
                  <td className="p-2 font-mono text-[10px] text-slate-500">
                    {r.trace_id?.slice(0, 12) || "—"}
                  </td>
                  <td className="p-2">{r.payment_method || "—"}</td>
                  <td className="p-2">{r.salla_order_status || "—"}</td>
                  <td className="p-2 font-mono">
                    {r.salla_total != null ? Number(r.salla_total).toFixed(2)
                                            : "—"}
                  </td>
                  <td className="p-2">
                    <span
                      className="inline-block px-1.5 py-0.5 rounded text-[10px] font-bold bg-slate-200"
                      title={r.pipeline_stage}>
                      {STAGE_LABELS[r.pipeline_stage] || r.pipeline_stage}
                    </span>
                  </td>
                  <td className="p-2 text-[10px] text-slate-600">
                    {r.reason || "—"}
                  </td>
                  <td className="p-2 text-center">
                    {r.has_existing_invoice ? (
                      <span
                        className="text-emerald-700 font-bold"
                        title={r.qoyod_invoice_id}>
                        ✓ {r.qoyod_invoice_number || "yes"}
                      </span>
                    ) : "—"}
                  </td>
                  <td className="p-2 text-center">
                    {r.dependency_status?.sendable === true
                      ? <span className="text-emerald-700 font-bold">✓</span>
                      : r.dependency_status?.sendable === false
                        ? <span className="text-rose-700 font-bold">✗</span>
                        : "—"}
                  </td>
                  <td className="p-2 flex flex-wrap gap-1">
                    {(r.actions_available || []).includes("preview") && (
                      <button
                        type="button"
                        onClick={() => { setSelectedRow(r); runPreview(r); }}
                        data-testid={`btn-preview-${r.row_id}`}
                        className="px-2 py-1 text-[11px] font-bold rounded bg-sky-600 text-white hover:bg-sky-700"
                      >
                        🔍 Preview
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setSelectedRow(r)}
                      data-testid={`btn-details-${r.row_id}`}
                      className="px-2 py-1 text-[11px] font-bold rounded bg-slate-200 hover:bg-slate-300"
                    >
                      تفاصيل
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Detail drawer */}
      {selectedRow && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
             data-testid="row-drawer-backdrop"
             onClick={() => { setSelectedRow(null); setPreview(null); }}>
          <div className="bg-white rounded-2xl shadow-2xl max-w-3xl w-full p-5 max-h-[90vh] overflow-y-auto"
               onClick={e => e.stopPropagation()}
               data-testid="row-drawer">
            <div className="flex items-start justify-between mb-3">
              <div>
                <h2 className="text-lg font-extrabold text-slate-900">
                  الطلب {selectedRow.salla_order_number || selectedRow.salla_order_id || "—"}
                </h2>
                <div className="text-[11px] font-mono text-slate-500">
                  trace_id: {selectedRow.trace_id}
                </div>
              </div>
              <button
                type="button"
                onClick={() => { setSelectedRow(null); setPreview(null); }}
                data-testid="btn-close-drawer"
                className="text-slate-400 hover:text-slate-700 text-xl">
                ✕
              </button>
            </div>

            <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[12px] mb-4">
              <dt className="text-slate-500">pipeline_stage</dt>
              <dd className="font-mono">{selectedRow.pipeline_stage}</dd>
              <dt className="text-slate-500">payment_method</dt>
              <dd>{selectedRow.payment_method || "—"}</dd>
              <dt className="text-slate-500">salla_order_status</dt>
              <dd>{selectedRow.salla_order_status || "—"}</dd>
              <dt className="text-slate-500">salla_total</dt>
              <dd className="font-mono">
                {selectedRow.salla_total != null
                  ? Number(selectedRow.salla_total).toFixed(2)
                  : "—"}
              </dd>
              <dt className="text-slate-500">qoyod_invoice_id</dt>
              <dd className="font-mono">{selectedRow.qoyod_invoice_id || "—"}</dd>
              {selectedRow.existing_invoice_source && (
                <>
                  <dt className="text-slate-500">existing_invoice_source</dt>
                  <dd className="font-mono text-amber-800" data-testid="drawer-existing-invoice-source">
                    {selectedRow.existing_invoice_source}
                    {selectedRow.existing_invoice_info?.qoyod_invoice_id && (
                      <span className="ms-1 text-slate-500">
                        ({selectedRow.existing_invoice_info.qoyod_invoice_id})
                      </span>
                    )}
                  </dd>
                </>
              )}
              <dt className="text-slate-500">qoyod_invoice_payment_id</dt>
              <dd className="font-mono">
                {selectedRow.qoyod_invoice_payment_id || "—"}
              </dd>
              <dt className="text-slate-500">qoyod_receipt_id</dt>
              <dd className="font-mono">
                {selectedRow.qoyod_receipt_id || "—"}
              </dd>
              <dt className="text-slate-500">reason</dt>
              <dd className="text-rose-700">{selectedRow.reason || "—"}</dd>
            </dl>

            {selectedRow.dependency_status && (
              <details className="mb-3 text-[11px]"
                       data-testid="drawer-dependency-status">
                <summary className="cursor-pointer font-bold text-slate-700">
                  dependency_status
                </summary>
                <pre dir="ltr"
                     className="mt-1 bg-slate-100 p-2 rounded font-mono text-[10px] whitespace-pre-wrap break-words max-h-60 overflow-auto">
{JSON.stringify(selectedRow.dependency_status, null, 2)}
                </pre>
              </details>
            )}

            {selectedRow.totals_comparison && (
              <details className="mb-3 text-[11px]"
                       data-testid="drawer-totals-comparison">
                <summary className="cursor-pointer font-bold text-slate-700">
                  totals_comparison
                </summary>
                <pre dir="ltr"
                     className="mt-1 bg-slate-100 p-2 rounded font-mono text-[10px] whitespace-pre-wrap break-words max-h-60 overflow-auto">
{JSON.stringify(selectedRow.totals_comparison, null, 2)}
                </pre>
              </details>
            )}

            <div className="mt-4 p-2 rounded bg-amber-50 border border-amber-300 text-[11px] text-amber-900"
                 data-testid="drawer-safety-note">
              الإجراءات المتاحة: <b>{(selectedRow.actions_available || []).join(", ") || "—"}</b>.
              زر Approve & Send مُعطَّل حالياً (يحتاج تفعيل selective_live_send_enabled).
            </div>

            {/* Preview panel */}
            {previewing && (
              <div className="mt-3 text-sm text-slate-500">
                جارِ بناء المعاينة…
              </div>
            )}
            {preview && (
              <div className="mt-3" data-testid="drawer-preview-result">
                <div className={`rounded border p-2 mb-2 text-[12px] font-bold ${
                  preview.ok ? "bg-emerald-50 border-emerald-300"
                             : "bg-rose-50 border-rose-300"}`}>
                  Preview: {preview.ok ? "✓ نجح" : "✗ فشل"} — mode={preview.mode || "—"},
                  qoyod_request_sent={String(preview.qoyod_request_sent ?? false)}
                </div>
                <details open>
                  <summary className="cursor-pointer text-[11px] font-bold text-slate-700">
                    Preview JSON كامل
                  </summary>
                  <pre dir="ltr"
                       className="mt-1 bg-slate-900 text-slate-100 p-2 rounded font-mono text-[10px] whitespace-pre-wrap break-words max-h-72 overflow-auto">
{JSON.stringify(preview, null, 2)}
                  </pre>
                </details>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
