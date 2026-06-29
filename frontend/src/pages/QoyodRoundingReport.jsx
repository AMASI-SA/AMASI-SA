/* Iter-290j-rounding-fix · Phase 1.5 — Operator-facing read-only
   diagnostic page. Surfaces ONLY rows where the money trail
   between Salla → Mezan → قيود diverges. No actions, no buttons
   that mutate state — pure inspection so we can decide which fix
   to apply.

   Phase 1.5 adds on top of Phase 1:
     • Severity pill (minor / moderate / material) alongside bucket
     • Per-invoice summary card (primary cause + contributions)
     • Richer line table (qty / unit_price / discount / tax /
       Salla-target / Mezan-computed / Qoyod-line)
     • Severity & data-gap filters + histograms
*/
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { RefreshCw, AlertTriangle, Info, Copy, Check } from "lucide-react";

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
  QOYOD_SERVER_SIDE_ROUNDING: {
    label: "تقريب قيود الداخلي",
    description: "ميزان حسبت الإجمالي مطابقاً لسلة، لكن قيود أعاد حسابه بقيمة مختلفة — منطق تقريب قيود مختلف عنّا.",
    tone: "rose",
  },
  INVOICE_TOTAL_ROUNDING_MISMATCH: {
    label: "اختلاف إجمالي قيود (عام)",
    description: "إجمالي قيود مختلف عن Salla لكن لا يمكن تحديد سطر بعينه — يحتاج فحص يدوي.",
    tone: "rose",
  },
  INSUFFICIENT_DATA: {
    label: "بيانات ناقصة",
    description: "الصف يفتقر لرد قيود — راجع تفاصيل البيانات الناقصة.",
    tone: "slate",
  },
  NO_MISMATCH: { label: "متطابق", description: "—", tone: "emerald" },
};

const SEVERITY_META = {
  MINOR_ROUNDING:    { label: "هللة (≤ 0.02)",  tone: "emerald" },
  MODERATE_DRIFT:    { label: "متوسط (0.02–0.05)", tone: "amber" },
  MATERIAL_MISMATCH: { label: "مادي (> 0.05)",  tone: "rose" },
  UNKNOWN:           { label: "غير معروف",       tone: "slate" },
};

const GAP_META = {
  no_invoice_response:  "لا يوجد رد فاتورة من قيود",
  no_payment_response:  "لا يوجد رد سداد من قيود",
  no_line_diagnostics:  "لا توجد diagnostics للسطور",
  no_canonical_items:   "لا توجد بنود في الـ payload",
  no_qoyod_invoice_id:  "لا يوجد ربط مع invoice_id",
  pre_logging_row:      "طلب قديم قبل تفعيل تسجيل الـ payload",
};

const CAUSE_META = {
  payment_only:           "السداد فقط — إجمالي الفاتورة سليم",
  shipping_line:          "سطر الشحن وحده يحمل الفارق",
  single_product_line:    "سطر منتج واحد يحمل كامل الفارق (غالباً توزيع خصم)",
  multi_line_cumulative:  "الفارق ينتج من تراكم تقريب عدة سطور",
  qoyod_server_rounding:  "قيود أعاد حساب الإجمالي بطريقته الخاصة",
  insufficient_data:      "بيانات ناقصة — لا يمكن تحديد السبب",
  unclassified:           "غير مصنّف — فحص يدوي مطلوب",
  none:                   "—",
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

function Money({ value, dp = 2 }) {
  if (value === null || value === undefined) {
    return <span className="text-slate-300 font-mono text-xs">—</span>;
  }
  return (
    <span className="font-mono text-xs text-slate-800">
      {Number(value).toFixed(dp)}
    </span>
  );
}

function Pill({ tone, label, testid }) {
  return (
    <span
      className={`inline-block text-[11px] font-bold px-2 py-0.5 rounded-full border ${TONE_BG[tone] || TONE_BG.slate}`}
      data-testid={testid}
    >
      {label}
    </span>
  );
}

function BucketPill({ bucket }) {
  const meta = BUCKET_META[bucket] || { label: bucket, tone: "slate" };
  return <Pill tone={meta.tone} label={meta.label}
               testid={`bucket-${bucket}`} />;
}

function SeverityPill({ severity }) {
  const meta = SEVERITY_META[severity] || SEVERITY_META.UNKNOWN;
  return <Pill tone={meta.tone} label={meta.label}
               testid={`severity-${severity}`} />;
}

function CopyChip({ value, testid }) {
  const [copied, setCopied] = useState(false);
  if (!value) return <span className="text-slate-300 font-mono text-[10px]">—</span>;
  const onClick = async (e) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(String(value));
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch (_) { /* fail silently */ }
  };
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testid}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-slate-100 hover:bg-slate-200 text-slate-700 font-mono text-[10px] transition"
      title="نسخ"
    >
      <span>{value}</span>
      {copied ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3" />}
    </button>
  );
}

/* Per-invoice summary block (Phase 1.5).
   The user explicitly asked for a "what's the likely cause" callout
   inside each expanded row so they don't have to mentally re-derive
   it from the line table. */
function InvoiceSummary({ row }) {
  const s = row.summary || {};
  const cause = CAUSE_META[s.primary_cause] || "—";
  return (
    <div className="bg-white border border-slate-200 rounded p-3 mb-2 space-y-1.5"
         data-testid={`summary-${row.order_id}`}>
      <div className="text-[11px] text-slate-700">
        <strong>السبب الأرجح:</strong> {cause}
      </div>
      {s.offender_count > 0 && (
        <div className="text-[11px] text-slate-600">
          <strong>عدد السطور المتسببة:</strong> {s.offender_count}
        </div>
      )}
      {s.shipping_contribution !== null && s.shipping_contribution !== undefined && (
        <div className="text-[11px] text-slate-600">
          <strong>مساهمة سطر الشحن:</strong>{" "}
          <Diff value={s.shipping_contribution} />
        </div>
      )}
      {s.non_shipping_contribution !== null && s.non_shipping_contribution !== undefined && (
        <div className="text-[11px] text-slate-600">
          <strong>مساهمة سطور المنتجات:</strong>{" "}
          <Diff value={s.non_shipping_contribution} />
        </div>
      )}
      {s.largest_offender && (
        <div className="text-[11px] text-slate-600">
          <strong>أكبر سطر متسبب:</strong>{" "}
          <span className="font-mono">
            {s.largest_offender.kind === "shipping" ? "🚚 " : ""}
            {s.largest_offender.sku}
          </span>{" "}
          <Diff value={s.largest_offender.line_diff} />
        </div>
      )}
      {row.data_gaps && row.data_gaps.length > 0 && (
        <div className="text-[11px] text-slate-600">
          <strong>البيانات الناقصة:</strong>{" "}
          <ul className="inline-flex flex-wrap gap-1 mt-1">
            {row.data_gaps.map((g) => (
              <li key={g}
                  data-testid={`gap-${row.order_id}-${g}`}
                  className="text-[10px] bg-slate-100 border border-slate-200 rounded px-1.5 py-0.5">
                {GAP_META[g] || g}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function LinesTable({ row }) {
  if (!row.lines || row.lines.length === 0) {
    return (
      <div className="text-[11px] text-slate-400 italic">
        لا توجد بيانات سطور — راجع البيانات الناقصة أعلاه.
      </div>
    );
  }
  return (
    <div className="bg-white border border-slate-200 rounded p-2 overflow-x-auto">
      <div className="text-[11px] font-bold text-slate-700 mb-1">
        تحليل السطور:
      </div>
      <table className="w-full text-[10px] font-mono whitespace-nowrap">
        <thead className="text-slate-500 border-b border-slate-200">
          <tr>
            <th className="text-right py-1 px-1">النوع</th>
            <th className="text-right py-1 px-1">SKU</th>
            <th className="text-right py-1 px-1">الكمية</th>
            <th className="text-right py-1 px-1">السعر</th>
            <th className="text-right py-1 px-1">الخصم</th>
            <th className="text-right py-1 px-1">الضريبة %</th>
            <th className="text-right py-1 px-1">Salla-target</th>
            <th className="text-right py-1 px-1">Mezan-computed</th>
            <th className="text-right py-1 px-1">Qoyod-line</th>
            <th className="text-right py-1 px-1">Δ</th>
          </tr>
        </thead>
        <tbody>
          {row.lines.map((ld, i) => (
            <tr key={i}
                className="border-t border-slate-100"
                data-testid={`line-${row.order_id}-${ld.sku}`}>
              <td className="py-0.5 px-1">
                {ld.kind === "shipping" ? "🚚 شحن" : "📦 منتج"}
              </td>
              <td className="py-0.5 px-1">{ld.sku || "—"}</td>
              <td className="py-0.5 px-1">
                <Money value={ld.quantity} dp={0} />
              </td>
              <td className="py-0.5 px-1">
                <Money value={ld.unit_price} />
              </td>
              <td className="py-0.5 px-1">
                <Money value={ld.discount_amount} />
              </td>
              <td className="py-0.5 px-1">
                {ld.tax_percent !== null && ld.tax_percent !== undefined
                  ? `${Number(ld.tax_percent).toFixed(2)}%`
                  : "—"}
              </td>
              <td className="py-0.5 px-1">
                <Money value={ld.salla_target_gross} />
              </td>
              <td className="py-0.5 px-1">
                <Money value={ld.mezan_computed_gross} />
              </td>
              <td className="py-0.5 px-1">
                <Money value={ld.qoyod_line_gross} />
              </td>
              <td className="py-0.5 px-1">
                <Diff value={ld.line_diff} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function QoyodRoundingReport() {
  const [loading, setLoading] = useState(false);
  const [report,  setReport]  = useState(null);
  const [expandedRowId, setExpandedRowId] = useState(null);

  const [bucketFilter,     setBucketFilter]     = useState("ALL");
  const [severityFilter,   setSeverityFilter]   = useState("ALL");
  const [gapFilter,        setGapFilter]        = useState("ALL");
  const [onlyNonZeroDiff,  setOnlyNonZeroDiff]  = useState(false);
  const [onlyRemainingBal, setOnlyRemainingBal] = useState(false);

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

  const visibleRows = useMemo(() => {
    if (!report?.rows) return [];
    return report.rows.filter((row) => {
      if (bucketFilter !== "ALL" && row.bucket !== bucketFilter) return false;
      if (severityFilter !== "ALL" && row.severity !== severityFilter) return false;
      if (gapFilter !== "ALL") {
        if (!row.data_gaps || !row.data_gaps.includes(gapFilter)) return false;
      }
      if (onlyNonZeroDiff) {
        const hasDiff = (row.invoice_diff && Math.abs(row.invoice_diff) > 0.005)
                     || (row.payment_diff && Math.abs(row.payment_diff) > 0.005);
        if (!hasDiff) return false;
      }
      if (onlyRemainingBal) {
        if (!(row.payment_diff !== null && row.payment_diff < -0.005)) {
          return false;
        }
      }
      return true;
    });
  }, [report, bucketFilter, severityFilter, gapFilter,
      onlyNonZeroDiff, onlyRemainingBal]);

  const bucketKeys = report
    ? Object.keys(report.by_bucket || {}).filter((k) => k !== "NO_MISMATCH")
    : [];
  const severityKeys = report
    ? Object.keys(report.by_severity || {})
    : [];
  const gapKeys = report
    ? Object.keys(report.by_gap_reason || {})
    : [];

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-5" dir="rtl">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-extrabold text-slate-800">
            🔬 تقرير فروق التقريب
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Iter-290j-rounding-fix · المرحلة 1.5 — تشخيص قراءة فقط بتصنيف
            بالـ bucket والشدة وأسباب نقص البيانات.
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

      {/* Histograms */}
      {report && (
        <section className="grid md:grid-cols-3 gap-3">
          {/* Bucket histogram */}
          <div className="bg-white border border-slate-200 rounded-xl p-4"
               data-testid="bucket-histogram">
            <div className="text-xs font-bold text-slate-600 mb-2">
              التصنيف ({report.scanned_count} فاتورة)
            </div>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(report.by_bucket || {}).map(([bucket, count]) => (
                <div key={bucket} className="flex items-center gap-1">
                  <BucketPill bucket={bucket} />
                  <span className="text-[10px] font-mono text-slate-600">× {count}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Severity histogram (NEW) */}
          <div className="bg-white border border-slate-200 rounded-xl p-4"
               data-testid="severity-histogram">
            <div className="text-xs font-bold text-slate-600 mb-2">
              الشدة (فقط الفواتير فيها فرق)
            </div>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(report.by_severity || {}).map(([sev, count]) => (
                <div key={sev} className="flex items-center gap-1">
                  <SeverityPill severity={sev} />
                  <span className="text-[10px] font-mono text-slate-600">× {count}</span>
                </div>
              ))}
              {Object.keys(report.by_severity || {}).length === 0 && (
                <span className="text-[11px] text-slate-400">لا فروق</span>
              )}
            </div>
          </div>

          {/* Gap-reason histogram (NEW) */}
          <div className="bg-white border border-slate-200 rounded-xl p-4"
               data-testid="gap-histogram">
            <div className="text-xs font-bold text-slate-600 mb-2">
              أسباب نقص البيانات
            </div>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(report.by_gap_reason || {}).map(([gap, count]) => (
                <div key={gap} className="flex items-center gap-1"
                     data-testid={`gap-count-${gap}`}>
                  <span className="text-[10px] bg-slate-100 border border-slate-200 rounded px-1.5 py-0.5">
                    {GAP_META[gap] || gap}
                  </span>
                  <span className="text-[10px] font-mono text-slate-600">× {count}</span>
                </div>
              ))}
              {Object.keys(report.by_gap_reason || {}).length === 0 && (
                <span className="text-[11px] text-slate-400">لا توجد</span>
              )}
            </div>
          </div>
        </section>
      )}

      {report && report.mismatch_count > 0 && (
        <div className="text-xs text-rose-700 flex items-start gap-2 bg-rose-50 border border-rose-200 rounded-xl p-3">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <strong>{report.mismatch_count}</strong> فاتورة فيها فرق
            (المتطابقة غير معروضة لتقليل الضوضاء).
          </div>
        </div>
      )}

      {/* Filters */}
      {report && (
        <section
          className="bg-white border border-slate-200 rounded-xl p-3 flex flex-wrap items-center gap-3"
          data-testid="rounding-report-filters"
        >
          <div className="text-[11px] font-bold text-slate-600">فلاتر:</div>

          <select
            value={bucketFilter}
            onChange={(e) => setBucketFilter(e.target.value)}
            data-testid="filter-bucket"
            className="text-xs border border-slate-300 rounded px-2 py-1 bg-white"
          >
            <option value="ALL">كل التصنيفات</option>
            {bucketKeys.map((k) => (
              <option key={k} value={k}>{BUCKET_META[k]?.label || k}</option>
            ))}
          </select>

          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            data-testid="filter-severity"
            className="text-xs border border-slate-300 rounded px-2 py-1 bg-white"
          >
            <option value="ALL">كل الشدّات</option>
            {severityKeys.map((k) => (
              <option key={k} value={k}>{SEVERITY_META[k]?.label || k}</option>
            ))}
          </select>

          {gapKeys.length > 0 && (
            <select
              value={gapFilter}
              onChange={(e) => setGapFilter(e.target.value)}
              data-testid="filter-gap-reason"
              className="text-xs border border-slate-300 rounded px-2 py-1 bg-white"
            >
              <option value="ALL">كل أسباب نقص البيانات</option>
              {gapKeys.map((k) => (
                <option key={k} value={k}>{GAP_META[k] || k}</option>
              ))}
            </select>
          )}

          <label className="flex items-center gap-1.5 text-xs text-slate-700 cursor-pointer">
            <input
              type="checkbox"
              checked={onlyNonZeroDiff}
              onChange={(e) => setOnlyNonZeroDiff(e.target.checked)}
              data-testid="filter-only-nonzero-diff"
            />
            فرق أكبر من 0
          </label>
          <label className="flex items-center gap-1.5 text-xs text-slate-700 cursor-pointer">
            <input
              type="checkbox"
              checked={onlyRemainingBal}
              onChange={(e) => setOnlyRemainingBal(e.target.checked)}
              data-testid="filter-only-remaining-balance"
            />
            فيه رصيد متبقّي فقط
          </label>
          <div className="ms-auto text-[11px] text-slate-500">
            <strong>{visibleRows.length}</strong> فاتورة معروضة
            من <strong>{report.rows.length}</strong> فيها فرق
          </div>
        </section>
      )}

      {/* Mismatch rows */}
      {report && visibleRows.length > 0 && (
        <section className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-slate-50 text-slate-700">
                <tr>
                  <th className="px-3 py-2 text-right font-bold">رقم الطلب</th>
                  <th className="px-3 py-2 text-right font-bold">التصنيف</th>
                  <th className="px-3 py-2 text-right font-bold">الشدة</th>
                  <th className="px-3 py-2 text-right font-bold">Salla</th>
                  <th className="px-3 py-2 text-right font-bold">قيود</th>
                  <th className="px-3 py-2 text-right font-bold">السداد</th>
                  <th className="px-3 py-2 text-right font-bold">Δ قيود − سلة</th>
                  <th className="px-3 py-2 text-right font-bold">Δ سداد − قيود</th>
                  <th className="px-3 py-2 text-right font-bold"></th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row) => {
                  const expanded = expandedRowId === row.row_id;
                  return (
                    <RowAndDetails
                      key={row.row_id}
                      row={row}
                      expanded={expanded}
                      onToggle={() => setExpandedRowId(expanded ? null : row.row_id)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Filter empty state */}
      {report && report.rows.length > 0 && visibleRows.length === 0 && (
        <section className="bg-slate-50 border border-slate-200 rounded-xl p-4 text-center text-sm text-slate-600"
                 data-testid="filter-empty-state">
          لا توجد نتائج للفلاتر المختارة.{" "}
          <button
            onClick={() => {
              setBucketFilter("ALL");
              setSeverityFilter("ALL");
              setGapFilter("ALL");
              setOnlyNonZeroDiff(false);
              setOnlyRemainingBal(false);
            }}
            className="text-sky-700 hover:underline"
            data-testid="btn-clear-filters"
          >
            مسح الفلاتر
          </button>
        </section>
      )}

      {/* Empty state — no drifts at all */}
      {report && report.rows.length === 0 && !loading && (
        <section className="bg-emerald-50 border border-emerald-200 rounded-xl p-6 text-center"
                 data-testid="empty-state-no-drifts">
          <div className="text-emerald-800 font-bold mb-1 flex items-center justify-center gap-2">
            <Info className="w-4 h-4" />
            لا توجد فروق تقريب على الفواتير الأخيرة
          </div>
          <div className="text-xs text-emerald-700">
            تم فحص {report.scanned_count} فاتورة، كلها متطابقة بين سلة وقيود.
          </div>
        </section>
      )}
    </div>
  );
}

/* Extracted to keep the parent JSX readable. */
function RowAndDetails({ row, expanded, onToggle }) {
  return (
    <>
      <tr
        className="border-t border-slate-100"
        data-testid={`rounding-row-${row.order_id}`}
      >
        <td className="px-3 py-2">
          <CopyChip
            value={row.order_number || row.order_id}
            testid={`copy-order-${row.order_id}`}
          />
          {row.qoyod_invoice_id && (
            <div className="mt-1 flex flex-wrap gap-1">
              <CopyChip
                value={`Inv ${row.qoyod_invoice_id}`}
                testid={`copy-invoice-${row.qoyod_invoice_id}`}
              />
              {row.qoyod_invoice_payment_id && (
                <CopyChip
                  value={`Pay ${row.qoyod_invoice_payment_id}`}
                  testid={`copy-payment-${row.qoyod_invoice_payment_id}`}
                />
              )}
            </div>
          )}
        </td>
        <td className="px-3 py-2"><BucketPill bucket={row.bucket} /></td>
        <td className="px-3 py-2"><SeverityPill severity={row.severity} /></td>
        <td className="px-3 py-2"><Money value={row.salla_total} /></td>
        <td className="px-3 py-2"><Money value={row.qoyod_invoice_total} /></td>
        <td className="px-3 py-2"><Money value={row.payment_amount_sent} /></td>
        <td className="px-3 py-2"><Diff value={row.invoice_diff} /></td>
        <td className="px-3 py-2"><Diff value={row.payment_diff} /></td>
        <td className="px-3 py-2">
          <button
            onClick={onToggle}
            className="text-xs text-sky-700 hover:underline"
            data-testid={`rounding-row-toggle-${row.order_id}`}
          >
            {expanded ? "إخفاء" : "تفاصيل"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-slate-50">
          <td colSpan={9} className="px-3 py-3">
            <div className="text-[11px] text-slate-600 mb-2">
              <strong>السبب:</strong> {row.rationale}
            </div>
            <div className="text-[11px] text-slate-600 mb-2">
              <strong>تقدير ميزان للإجمالي قبل الإرسال:</strong>{" "}
              <Money value={row.mezan_computed_total} />
              {" · "}
              <strong>الضريبة المُستخدمة:</strong>{" "}
              {row.tax_percent !== null && row.tax_percent !== undefined
                ? `${Number(row.tax_percent).toFixed(2)}%`
                : "—"}
            </div>
            <InvoiceSummary row={row} />
            <LinesTable row={row} />
          </td>
        </tr>
      )}
    </>
  );
}
