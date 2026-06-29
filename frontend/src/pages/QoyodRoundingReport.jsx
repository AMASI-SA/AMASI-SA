/* Iter-290j-rounding-fix · Phase 1 — Operator-facing read-only
   diagnostic page. Surfaces ONLY rows where the money trail
   between Salla → Mezan → قيود diverges. No actions, no buttons
   that mutate state — pure inspection so we can decide which fix
   to apply.
*/
import { useEffect, useState } from "react";
import axios from "axios";
import { RefreshCw, AlertTriangle, Info } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Bucket → Arabic label + colour. Mirrors the classifier's bucket
// rules in /app/backend/integrations/qoyod/rounding_mismatch_report.py.
const BUCKET_META = {
  PAYMENT_MISMATCH_ONLY: {
    label: "السداد فقط مختلف",
    description: "إجمالي قيود = إجمالي سلة، لكن مبلغ السداد المُرسَل لا يطابق إجمالي قيود.",
    tone: "amber",
  },
  SHIPPING_ROUNDING_MISMATCH: {
    label: "تقريب سطر الشحن",
    description: "الفارق يأتي من سطر الشحن — Mezan-computed مختلف عن Salla-target.",
    tone: "orange",
  },
  DISCOUNT_ALLOCATION_MISMATCH: {
    label: "توزيع الخصم",
    description: "سطر منتج واحد يحمل كامل الفارق — غالباً توزيع الخصم مختلف بين Mezan وقيود.",
    tone: "orange",
  },
  MULTI_LINE_CUMULATIVE_ROUNDING: {
    label: "تراكم تقريب السطور",
    description: "الفارق ينتج من تراكم تقريب عدة سطور (النمط الكلاسيكي لـ 0.01 SAR drift).",
    tone: "rose",
  },
  INVOICE_TOTAL_ROUNDING_MISMATCH: {
    label: "اختلاف إجمالي قيود (عام)",
    description: "إجمالي قيود مختلف عن Salla لكن لا يمكن تحديد سطر بعينه — يحتاج فحص يدوي.",
    tone: "rose",
  },
  INSUFFICIENT_DATA: {
    label: "بيانات ناقصة",
    description: "الصف يفتقر لرد قيود — قد يكون قبل تفعيل response logging.",
    tone: "slate",
  },
  NO_MISMATCH: { label: "متطابق", description: "—", tone: "emerald" },
};

const TONE_BG = {
  amber:   "bg-amber-50 text-amber-900 border-amber-200",
  orange:  "bg-orange-50 text-orange-900 border-orange-200",
  rose:    "bg-rose-50 text-rose-900 border-rose-200",
  slate:   "bg-slate-50 text-slate-700 border-slate-200",
  emerald: "bg-emerald-50 text-emerald-900 border-emerald-200",
};

function Diff({ value }) {
  if (value === null || value === undefined) {
    return <span className="text-slate-300 font-mono text-xs">—</span>;
  }
  const abs = Math.abs(value);
  const tone = abs <= 0.005 ? "text-emerald-700"
              : abs <= 0.05 ? "text-amber-700"
              : "text-rose-700";
  const sign = value > 0 ? "+" : value < 0 ? "" : "";
  return (
    <span className={`font-mono text-xs font-bold ${tone}`}>
      {sign}{value.toFixed(4)}
    </span>
  );
}

function Money({ value }) {
  if (value === null || value === undefined) {
    return <span className="text-slate-300 font-mono text-xs">—</span>;
  }
  return (
    <span className="font-mono text-xs text-slate-800">
      {value.toFixed(2)} SAR
    </span>
  );
}

function BucketPill({ bucket }) {
  const meta = BUCKET_META[bucket] || { label: bucket, tone: "slate" };
  return (
    <span
      className={`inline-block text-[11px] font-bold px-2 py-0.5 rounded-full border ${TONE_BG[meta.tone] || TONE_BG.slate}`}
      data-testid={`bucket-${bucket}`}
    >
      {meta.label}
    </span>
  );
}

export default function QoyodRoundingReport() {
  const [loading, setLoading] = useState(false);
  const [report,  setReport]  = useState(null);
  const [expandedRowId, setExpandedRowId] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(
        `${API}/integrations/qoyod/admin/rounding-mismatch-report?limit=200`
      );
      setReport(data);
    } catch (e) {
      alert("تعذّر تحميل التقرير. تأكد من تسجيل الدخول.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-5" dir="rtl">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-extrabold text-slate-800">
            🔬 تقرير فروق التقريب
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Iter-290j-rounding-fix · المرحلة 1 — تشخيص قراءة فقط. لا
            يلمس قاعدة البيانات ولا قيود.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          data-testid="btn-refresh-rounding-report"
          className="flex items-center gap-2 px-4 py-2 text-sm font-bold rounded-md bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          {loading ? "جاري الفحص..." : "إعادة الفحص"}
        </button>
      </header>

      {/* Bucket histogram */}
      {report && (
        <section
          className="bg-white border border-slate-200 rounded-xl p-4"
          data-testid="bucket-histogram"
        >
          <div className="text-xs font-bold text-slate-600 mb-2">
            توزيع الفواتير حسب التصنيف ({report.scanned_count} فاتورة مفحوصة)
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(report.by_bucket || {}).map(([bucket, count]) => (
              <div
                key={bucket}
                className="flex items-center gap-2"
              >
                <BucketPill bucket={bucket} />
                <span className="text-xs font-mono text-slate-600">
                  × {count}
                </span>
              </div>
            ))}
          </div>
          {report.mismatch_count > 0 && (
            <div className="mt-3 text-xs text-rose-700 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <div>
                <strong>{report.mismatch_count}</strong> فاتورة فيها فرق
                تقريب. الفواتير المتطابقة لا تظهر في الجدول أدناه (للتقليل من
                الضوضاء).
              </div>
            </div>
          )}
          {report.mismatch_count === 0 && (
            <div className="mt-3 text-xs text-emerald-700 flex items-center gap-2">
              <Info className="w-4 h-4" />
              لا توجد فروق تقريب على الفواتير الأخيرة.
            </div>
          )}
        </section>
      )}

      {/* Mismatch rows */}
      {report && report.rows.length > 0 && (
        <section className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-slate-50 text-slate-700">
                <tr>
                  <th className="px-3 py-2 text-right font-bold">رقم الطلب</th>
                  <th className="px-3 py-2 text-right font-bold">التصنيف</th>
                  <th className="px-3 py-2 text-right font-bold">Salla</th>
                  <th className="px-3 py-2 text-right font-bold">قيود (إجمالي)</th>
                  <th className="px-3 py-2 text-right font-bold">السداد</th>
                  <th className="px-3 py-2 text-right font-bold">Δ قيود − سلة</th>
                  <th className="px-3 py-2 text-right font-bold">Δ سداد − قيود</th>
                  <th className="px-3 py-2 text-right font-bold"></th>
                </tr>
              </thead>
              <tbody>
                {report.rows.map((row) => {
                  const expanded = expandedRowId === row.row_id;
                  return (
                    <>
                      <tr
                        key={row.row_id}
                        className="border-t border-slate-100"
                        data-testid={`rounding-row-${row.order_id}`}
                      >
                        <td className="px-3 py-2 font-mono text-slate-700">
                          {row.order_number || row.order_id}
                          {row.qoyod_invoice_id && (
                            <div className="text-[10px] text-slate-400 font-mono">
                              Inv: {row.qoyod_invoice_id}
                              {row.qoyod_invoice_payment_id && (
                                <> · Pay: {row.qoyod_invoice_payment_id}</>
                              )}
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <BucketPill bucket={row.bucket} />
                        </td>
                        <td className="px-3 py-2"><Money value={row.salla_total} /></td>
                        <td className="px-3 py-2"><Money value={row.qoyod_invoice_total} /></td>
                        <td className="px-3 py-2"><Money value={row.payment_amount_sent} /></td>
                        <td className="px-3 py-2"><Diff value={row.invoice_diff} /></td>
                        <td className="px-3 py-2"><Diff value={row.payment_diff} /></td>
                        <td className="px-3 py-2">
                          <button
                            onClick={() => setExpandedRowId(
                              expanded ? null : row.row_id)}
                            className="text-xs text-sky-700 hover:underline"
                            data-testid={`rounding-row-toggle-${row.order_id}`}
                          >
                            {expanded ? "إخفاء" : "تفاصيل"}
                          </button>
                        </td>
                      </tr>
                      {expanded && (
                        <tr className="bg-slate-50">
                          <td colSpan={8} className="px-3 py-3">
                            <div className="text-[11px] text-slate-600 mb-2">
                              <strong>السبب:</strong> {row.rationale}
                            </div>
                            <div className="text-[11px] text-slate-600 mb-2">
                              <strong>تقدير ميزان للإجمالي قبل الإرسال:</strong>{" "}
                              <Money value={row.mezan_computed_total} />
                            </div>
                            {row.line_diffs && row.line_diffs.length > 0 && (
                              <div className="bg-white border border-slate-200 rounded p-2">
                                <div className="text-[11px] font-bold text-slate-700 mb-1">
                                  تحليل السطور:
                                </div>
                                <table className="w-full text-[10px] font-mono">
                                  <thead className="text-slate-500">
                                    <tr>
                                      <th className="text-right py-1">SKU</th>
                                      <th className="text-right py-1">Salla</th>
                                      <th className="text-right py-1">Mezan-Computed</th>
                                      <th className="text-right py-1">Δ</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {row.line_diffs.map((ld, i) => (
                                      <tr key={i}
                                          data-testid={`line-diff-${row.order_id}-${ld.sku}`}>
                                        <td className="py-0.5">
                                          {ld.is_shipping ? "🚚 " : ""}{ld.sku}
                                        </td>
                                        <td className="py-0.5">
                                          <Money value={ld.salla_total} />
                                        </td>
                                        <td className="py-0.5">
                                          <Money value={ld.computed_gross} />
                                        </td>
                                        <td className="py-0.5">
                                          <Diff value={ld.line_diff} />
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Empty state */}
      {report && report.rows.length === 0 && !loading && (
        <section className="bg-emerald-50 border border-emerald-200 rounded-xl p-6 text-center">
          <div className="text-emerald-800 font-bold mb-1">
            ✅ لا توجد فروق تقريب على الفواتير الأخيرة
          </div>
          <div className="text-xs text-emerald-700">
            تم فحص {report.scanned_count} فاتورة، كلها متطابقة بين سلة وقيود.
          </div>
        </section>
      )}
    </div>
  );
}
