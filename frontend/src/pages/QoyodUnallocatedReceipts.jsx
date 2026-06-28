/**
 * Iter-290h — Qoyod Unallocated Receipts Report
 *
 * Lists Qoyod receipts that appear unallocated (the "غير مستعمل" bin
 * in قيود) alongside a SUGGESTED matching invoice. The operator links
 * receipts manually inside قيود UI and clicks "تمت المعالجة يدوياً"
 * so each row disappears from this report.
 *
 * READ-ONLY view of Qoyod — Mezan never auto-allocates. The dismiss
 * button only writes a flag into ميزان's own DB.
 *
 * Backend endpoints (Iter-290h):
 *   GET    /api/integrations/qoyod/admin/unallocated-receipts-report
 *   POST   /api/integrations/qoyod/admin/unallocated-receipts/:id/dismiss
 *   DELETE /api/integrations/qoyod/admin/unallocated-receipts/:id/dismiss
 */
import React, { useEffect, useMemo, useState } from "react";
import api from "../lib/api";
import { Term, TermPill, TermHelpCard } from "../components/Term";
import { termFor } from "../lib/qoyodTerminology";

const QOYOD_BASE = "/integrations/qoyod";

// ─── UI primitives ─────────────────────────────────────────────────
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

const ConfidenceBadge = ({ confidence }) => {
  const tone = {
    high: "bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-200",
    medium: "bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-200",
    low: "bg-orange-100 dark:bg-orange-900/40 text-orange-700 dark:text-orange-200",
    none: "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300",
  }[confidence] || "bg-zinc-100 text-zinc-600";
  const { label, description } = termFor(confidence, "confidence");
  return (
    <span data-testid={`confidence-${confidence}`}
          title={description}
          className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium cursor-help ${tone}`}>
      {label}
    </span>
  );
};

const ReasonChip = ({ reason }) => (
  <TermPill code={reason} kind="match" tone="indigo" />
);

const fmtAmount = (v, ccy = "SAR") => {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return new Intl.NumberFormat("ar-SA",
    { style: "currency", currency: ccy, maximumFractionDigits: 2 })
    .format(n);
};

const fmtDate = (v) => {
  if (!v) return "—";
  try {
    return new Date(String(v)).toLocaleDateString("ar-SA",
      { year: "numeric", month: "2-digit", day: "2-digit" });
  } catch { return String(v); }
};

// ─── Page ─────────────────────────────────────────────────────────
export default function QoyodUnallocatedReceipts() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [dismissingId, setDismissingId] = useState(null);
  const [confirmId, setConfirmId] = useState(null);
  const [confirmNote, setConfirmNote] = useState("");

  const fetchReport = async () => {
    setLoading(true); setErr(null);
    try {
      const r = await api.get(
        `${QOYOD_BASE}/admin/unallocated-receipts-report`,
        { params: { max_receipts: 200, max_invoices: 500 } });
      setReport(r.data);
      if (r.data?.ok === false) {
        setErr(r.data?.error?.message
          || r.data?.error?.code
          || "تعذّر تحميل التقرير");
      }
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || String(e));
    } finally { setLoading(false); }
  };

  useEffect(() => { fetchReport(); }, []);

  const handleDismiss = async (receiptId) => {
    setDismissingId(receiptId);
    try {
      await api.post(
        `${QOYOD_BASE}/admin/unallocated-receipts/${encodeURIComponent(receiptId)}/dismiss`,
        confirmNote ? { note: confirmNote } : {});
      setConfirmId(null);
      setConfirmNote("");
      await fetchReport();
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || String(e));
    } finally { setDismissingId(null); }
  };

  const items = report?.items || [];
  const summary = report?.summary || {};

  return (
    <div className="p-6 space-y-6" data-testid="qoyod-unallocated-receipts">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-semibold"
              data-testid="unallocated-receipts-title">
            🧾 سندات قبض غير مربوطة بفواتير
          </h1>
          <p className="text-sm text-zinc-500 mt-1 max-w-2xl">
            هذه الصفحة تعرض سندات قبض موجودة في قيود لكنها غير مربوطة بفواتير.
            يتم ربطها يدوياً في قيود، ثم تعليمها كمُراجعة في ميزان.
          </p>
          <div className="mt-2 inline-flex items-center gap-2 text-xs">
            <span title="هذه الصفحة لا تنفذ أي تعديل على قيود — للعرض والمراجعة فقط."
                  className="px-2 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 cursor-help">
              👁️ قراءة فقط
            </span>
            <span className="text-zinc-400">
              الربط الفعلي يتم يدوياً في قيود.
            </span>
          </div>
        </div>
        <button onClick={fetchReport}
                disabled={loading}
                className="px-4 py-2 rounded-lg bg-zinc-900 text-white text-sm dark:bg-zinc-100 dark:text-zinc-900 disabled:opacity-50"
                data-testid="refresh-report-btn">
          {loading ? "...جارٍ التحميل" : "🔄 تحديث"}
        </button>
      </div>

      {/* Explanation cards */}
      <div className="grid md:grid-cols-2 gap-3" data-testid="page-help">
        <TermHelpCard code="UnallocatedReceipt" kind="general" />
        <TermHelpCard code="InvoicePayment" kind="general" />
      </div>

      {/* Error banner */}
      {err && (
        <div data-testid="report-error"
             className="rounded-lg border border-rose-200 dark:border-rose-900 bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-200 p-3 text-sm">
          {err}
        </div>
      )}

      {/* Stats */}
      {report?.ok && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3"
             data-testid="report-stats">
          <StatCard label="إجمالي السندات غير المربوطة"
                    value={summary.unallocated_count ?? 0}
                    tone="yellow"
                    testid="stat-total" />
          <StatCard label="باقتراح فاتورة"
                    value={summary.with_suggestion ?? 0}
                    tone="green"
                    testid="stat-with-suggestion" />
          <StatCard label="ثقة مرتفعة"
                    value={summary?.by_confidence?.high ?? 0}
                    tone="green"
                    testid="stat-confidence-high" />
          <StatCard label="ثقة متوسطة/منخفضة"
                    value={(summary?.by_confidence?.medium ?? 0)
                         + (summary?.by_confidence?.low ?? 0)}
                    tone="yellow"
                    testid="stat-confidence-medlow" />
          <StatCard label="بدون اقتراح فاتورة"
                    value={summary.without_suggestion ?? 0}
                    tone="red"
                    testid="stat-without-suggestion" />
        </div>
      )}

      {/* Empty state */}
      {report?.ok && items.length === 0 && (
        <div data-testid="empty-state"
             className="rounded-xl border border-emerald-200 dark:border-emerald-900 bg-emerald-50 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-200 p-6 text-center">
          <div className="text-3xl mb-2">✅</div>
          <div className="font-medium">لا توجد سندات قبض غير مربوطة</div>
          <div className="text-sm mt-1 opacity-80">
            كل السندات إما مربوطة بفواتير في قيود أو معلّمة كمراجعة يدوية في ميزان.
          </div>
        </div>
      )}

      {/* Table */}
      {report?.ok && items.length > 0 && (
        <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 overflow-hidden"
             data-testid="report-table-wrapper">
          <table className="w-full text-sm"
                 data-testid="unallocated-table">
            <thead className="bg-zinc-50 dark:bg-zinc-900 text-zinc-600 dark:text-zinc-400">
              <tr className="text-right">
                <th className="px-3 py-2 font-medium"
                    title="السند الموجود في قيود — اضغط الرابط لفتحه">
                  سند القبض
                </th>
                <th className="px-3 py-2 font-medium">العميل</th>
                <th className="px-3 py-2 font-medium">المبلغ</th>
                <th className="px-3 py-2 font-medium">التاريخ</th>
                <th className="px-3 py-2 font-medium"
                    title="الفاتورة التي يرجّح أن السند يخصها بناءً على المطابقة">
                  الفاتورة المقترحة
                </th>
                <th className="px-3 py-2 font-medium"
                    title="مدى قوة المطابقة بين السند والفاتورة المقترحة">
                  درجة المطابقة
                </th>
                <th className="px-3 py-2 font-medium"
                    title="على أي أساس تم اقتراح هذه الفاتورة (المرجع/المبلغ/العميل/التاريخ)">
                  سبب المطابقة
                </th>
                <th className="px-3 py-2 font-medium">إجراء</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {items.map((it) => {
                const r = it.receipt || {};
                const s = it.suggestion || {};
                const reasons = it.match_reasons || [];
                const rid = String(r.id);
                return (
                  <tr key={rid}
                      data-testid={`row-${rid}`}
                      className="hover:bg-zinc-50 dark:hover:bg-zinc-900/40">
                    <td className="px-3 py-2 align-top">
                      <div className="font-medium">{r.number || `#${r.id}`}</div>
                      {r.external_reference && (
                        <div className="text-xs text-zinc-500 mt-0.5">
                          مرجع: {r.external_reference}
                        </div>
                      )}
                      {it.qoyod_receipt_url && (
                        <a href={it.qoyod_receipt_url}
                           target="_blank" rel="noreferrer"
                           data-testid={`open-receipt-${rid}`}
                           className="text-xs text-indigo-600 dark:text-indigo-300 hover:underline mt-1 inline-block">
                          فتح في قيود ↗
                        </a>
                      )}
                    </td>
                    <td className="px-3 py-2 align-top text-zinc-600 dark:text-zinc-300">
                      {r.contact_id || "—"}
                    </td>
                    <td className="px-3 py-2 align-top font-mono">
                      {fmtAmount(r.amount)}
                    </td>
                    <td className="px-3 py-2 align-top text-zinc-600 dark:text-zinc-300">
                      {fmtDate(r.date)}
                    </td>
                    <td className="px-3 py-2 align-top">
                      {it.suggestion ? (
                        <>
                          <div className="font-medium">
                            {s.reference || `#${s.id}`}
                          </div>
                          <div className="text-xs text-zinc-500 mt-0.5">
                            {fmtAmount(s.total)} · {fmtDate(s.issue_date)}
                          </div>
                          {it.qoyod_invoice_url && (
                            <a href={it.qoyod_invoice_url}
                               target="_blank" rel="noreferrer"
                               data-testid={`open-invoice-${rid}`}
                               className="text-xs text-indigo-600 dark:text-indigo-300 hover:underline mt-1 inline-block">
                              فتح الفاتورة في قيود ↗
                            </a>
                          )}
                        </>
                      ) : (
                        <span className="text-zinc-400 italic">لا يوجد اقتراح</span>
                      )}
                    </td>
                    <td className="px-3 py-2 align-top">
                      <ConfidenceBadge confidence={it.confidence} />
                    </td>
                    <td className="px-3 py-2 align-top">
                      <div className="flex flex-wrap gap-1">
                        {reasons.length === 0
                          ? <span className="text-zinc-400 text-xs">—</span>
                          : reasons.map((rs) => (
                              <ReasonChip key={rs} reason={rs} />
                            ))}
                      </div>
                    </td>
                    <td className="px-3 py-2 align-top">
                      {confirmId === rid ? (
                        <div className="space-y-2 min-w-[180px]"
                             data-testid={`confirm-dismiss-${rid}`}>
                          <input type="text"
                                 value={confirmNote}
                                 onChange={(e) => setConfirmNote(e.target.value)}
                                 placeholder="ملاحظة اختيارية"
                                 maxLength={200}
                                 data-testid={`dismiss-note-${rid}`}
                                 className="w-full text-xs border rounded px-2 py-1 bg-white dark:bg-zinc-900 dark:border-zinc-700" />
                          <div className="flex gap-1">
                            <button onClick={() => handleDismiss(rid)}
                                    disabled={dismissingId === rid}
                                    data-testid={`confirm-dismiss-btn-${rid}`}
                                    className="px-2 py-1 text-xs rounded bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50">
                              تأكيد
                            </button>
                            <button onClick={() => { setConfirmId(null); setConfirmNote(""); }}
                                    data-testid={`cancel-dismiss-btn-${rid}`}
                                    className="px-2 py-1 text-xs rounded bg-zinc-200 dark:bg-zinc-700 hover:bg-zinc-300 dark:hover:bg-zinc-600">
                              إلغاء
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button onClick={() => { setConfirmId(rid); setConfirmNote(""); }}
                                data-testid={`dismiss-btn-${rid}`}
                                className="px-3 py-1 text-xs rounded-lg border border-zinc-300 dark:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-800">
                          ✓ تمت المعالجة يدوياً
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Footer meta */}
      {report?.ok && (
        <div className="text-xs text-zinc-500"
             data-testid="report-meta">
          مسحت {report.scanned_receipts} سند قبض و {report.scanned_invoices} فاتورة.
          {report.dismissed_count > 0 && (
            <> · {report.dismissed_count} سند معلّم سابقاً كمراجعة يدوية (مخفي من القائمة).</>
          )}
        </div>
      )}
    </div>
  );
}
