/* Iter-290k · Phase-2 DRY-RUN UI — read-only simulation viewer.

Renders the output of `/admin/rounding-dry-run` so the operator can:
  • See per-eligible-row simulation results.
  • See exactly which قيود-payload line would be touched.
  • Inspect adjustment_net + new_discount before any production change.
  • Confirm `diff_after == 0` on the cases the algorithm claims to fix.

NO buttons mutate anything. The "Apply to قيود" CTA does NOT exist on
this page — that's intentional and per the user's explicit guardrail.
*/
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { RefreshCw, AlertTriangle, CheckCircle2, XCircle, Info } from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const OUTCOME_META = {
  adjustment_succeeded: {
    label: "✓ التصحيح نجح",
    description: "بعد تعديل خصم أكبر سطر، أصبح إجمالي المحاكاة = إجمالي سلة.",
    tone: "emerald",
  },
  no_adjustment_needed: {
    label: "✓ متطابق أصلاً",
    description: "محاكاة Decimal الجديدة تطابق سلة دون أي تعديل.",
    tone: "sky",
  },
  parity_gap_needs_qoyod_model: {
    label: "⚠ PARITY GAP — نموذج المحاكاة لا يطابق قيود",
    description: "محاكاتنا تطابق Salla لكن قيود الفعلي مختلف. لا يمكن اقتراح إصلاح حتى نجعل المحاكاة تعيد إنتاج قيود فعلياً.",
    tone: "amber",
  },
  skipped: {
    label: "⤴ تخطّى",
    description: "خارج نطاق Phase 2.",
    tone: "slate",
  },
};

const PARITY_META = {
  ALIGNED:                       { label: "✓ متطابق",                tone: "emerald" },
  MODEL_OK_NEEDS_ADJUSTMENT:     { label: "✓ نموذج صحيح — يحتاج تعديل", tone: "sky" },
  PARITY_GAP_LOCAL_MATCHES_SALLA: { label: "⚠ PARITY GAP",             tone: "amber" },
  PARITY_GAP_MODEL_OFF:          { label: "⚠ نموذج بعيد",              tone: "rose" },
  NO_QOYOD_ACTUAL:               { label: "— لا يوجد رد قيود",         tone: "slate" },
  UNKNOWN:                       { label: "—",                         tone: "slate" },
};

const SKIP_REASON_LABELS = {
  excluded_bucket:          "Bucket مستثنى",
  non_phase2_bucket:        "Bucket ليس ضمن Phase 2",
  non_minor_severity:       "شدة غير Minor",
  diff_out_of_phase2_set:   "الفرق خارج 0.01/0.02",
  no_payload_line_items:    "لا يوجد payload للسطور",
  no_invoice_diff:          "لا يمكن حساب الفرق",
};

const TONE_BG = {
  emerald: "bg-emerald-50 text-emerald-900 border-emerald-200",
  sky:     "bg-sky-50 text-sky-900 border-sky-200",
  slate:   "bg-slate-50 text-slate-700 border-slate-200",
  rose:    "bg-rose-50 text-rose-900 border-rose-200",
  amber:   "bg-amber-50 text-amber-900 border-amber-200",
};

function Pill({ tone, label, testid }) {
  return (
    <span
      data-testid={testid}
      className={`inline-block text-[11px] font-bold px-2 py-0.5 rounded-full border ${TONE_BG[tone] || TONE_BG.slate}`}>
      {label}
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

function Diff({ value, dp = 4 }) {
  if (value === null || value === undefined) {
    return <span className="text-slate-300 font-mono text-xs">—</span>;
  }
  const abs = Math.abs(value);
  const tone = abs <= 0.005 ? "text-emerald-700"
              : abs <= 0.05 ? "text-amber-700" : "text-rose-700";
  const sign = value > 0 ? "+" : value < 0 ? "" : "";
  return (
    <span className={`font-mono text-xs font-bold ${tone}`}>
      {sign}{Number(value).toFixed(dp)}
    </span>
  );
}

function OutcomePill({ outcome }) {
  let meta;
  if (outcome === "adjustment_succeeded") meta = OUTCOME_META.adjustment_succeeded;
  else if (outcome === "no_adjustment_needed") meta = OUTCOME_META.no_adjustment_needed;
  else if (outcome === "parity_gap_needs_qoyod_model") meta = OUTCOME_META.parity_gap_needs_qoyod_model;
  else if (outcome === "skipped") meta = OUTCOME_META.skipped;
  else if (outcome && outcome.startsWith("adjustment_failed")) {
    meta = { label: `✗ فشل: ${outcome.split(":")[1] || ""}`, tone: "rose" };
  } else {
    meta = { label: outcome || "—", tone: "slate" };
  }
  return <Pill tone={meta.tone} label={meta.label}
               testid={`outcome-${outcome}`} />;
}

function ParityPill({ parity }) {
  const meta = PARITY_META[parity] || PARITY_META.UNKNOWN;
  return <Pill tone={meta.tone} label={meta.label}
               testid={`parity-${parity}`} />;
}

function ExpandedDetails({ row }) {
  const adj = row.adjustment;
  const qr  = row.qoyod_response || {};
  const isParityGap = row.outcome === "parity_gap_needs_qoyod_model";
  return (
    <div className="space-y-3">
      {/* Iter-290k.1 — Parity callout. The big red sign that
          "we don't yet have a model that reproduces قيود's actual
          number — so we MUST NOT propose any production fix here". */}
      {isParityGap && (
        <div className="bg-amber-50 border border-amber-300 rounded p-3"
             data-testid={`parity-gap-callout-${row.order_id}`}>
          <div className="text-[12px] font-bold text-amber-900 mb-1">
            ⚠ PARITY GAP — نموذج المحاكاة لا يعيد إنتاج قيود الفعلي
          </div>
          <div className="text-[11px] text-amber-800 leading-relaxed">
            محاكاة Decimal+ROUND_HALF_UP من نفس الـ payload تنتج
            <span className="font-mono mx-1">{Number(row.simulated_qoyod_invoice_total).toFixed(2)}</span>
            (مطابق Salla)، لكن قيود فعلياً أعاد إنتاج
            <span className="font-mono mx-1">{Number(row.qoyod_actual_total).toFixed(2)}</span>
            (فرق {Number(row.simulated_minus_qoyod_actual ?? 0).toFixed(4)} عن المحاكاة).
            <strong className="block mt-1">
              لن يُقترح أي adjustment حتى نُحدّد منطق قيود الداخلي بدقة.
            </strong>
          </div>
        </div>
      )}

      {/* Triple-comparison summary card — Salla vs Local-sim vs Qoyod-actual */}
      <div className="bg-white border border-slate-200 rounded p-3"
           data-testid={`triple-comparison-${row.order_id}`}>
        <div className="text-[11px] font-bold text-slate-700 mb-2">
          المقارنة الثلاثية:
        </div>
        <table className="w-full text-[11px] font-mono">
          <tbody>
            <tr>
              <td className="py-0.5 text-slate-600 w-1/3">Salla total</td>
              <td className="py-0.5"><Money value={row.salla_total} /></td>
              <td className="py-0.5 text-slate-400 text-[10px]">المرجع المتوقع</td>
            </tr>
            <tr>
              <td className="py-0.5 text-slate-600">Local simulated total</td>
              <td className="py-0.5"><Money value={row.simulated_qoyod_invoice_total} /></td>
              <td className="py-0.5 text-slate-400 text-[10px]">
                Decimal+ROUND_HALF_UP من stored payload
                {row.local_sim_matches_salla
                  ? <span className="ms-2 text-emerald-700">≈ Salla</span>
                  : <span className="ms-2 text-rose-700">≠ Salla</span>}
              </td>
            </tr>
            <tr>
              <td className="py-0.5 text-slate-600">Qoyod actual total</td>
              <td className="py-0.5"><Money value={row.qoyod_actual_total} /></td>
              <td className="py-0.5 text-slate-400 text-[10px]">
                من response body
                {row.local_sim_matches_qoyod_actual
                  ? <span className="ms-2 text-emerald-700">≈ Local-sim</span>
                  : row.qoyod_actual_total !== null
                    ? <span className="ms-2 text-rose-700">≠ Local-sim</span>
                    : null}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* قيود response details — invoice id/status/balance/payment */}
      {qr && (qr.invoice_id || qr.invoice_total !== null) && (
        <div className="bg-white border border-slate-200 rounded p-3"
             data-testid={`qoyod-response-summary-${row.order_id}`}>
          <div className="text-[11px] font-bold text-slate-700 mb-2">
            تفاصيل فاتورة قيود (Read-Only):
          </div>
          <table className="w-full text-[11px] font-mono">
            <tbody>
              <tr>
                <td className="py-0.5 text-slate-600 w-1/3">invoice_id</td>
                <td className="py-0.5">{qr.invoice_id || "—"}</td>
              </tr>
              <tr>
                <td className="py-0.5 text-slate-600">invoice_total</td>
                <td className="py-0.5"><Money value={qr.invoice_total} /></td>
              </tr>
              <tr>
                <td className="py-0.5 text-slate-600">invoice_balance</td>
                <td className="py-0.5"><Money value={qr.invoice_balance} /></td>
              </tr>
              <tr>
                <td className="py-0.5 text-slate-600">invoice_status</td>
                <td className="py-0.5">{qr.invoice_status || "—"}</td>
              </tr>
              <tr>
                <td className="py-0.5 text-slate-600">payment_amount</td>
                <td className="py-0.5"><Money value={qr.payment_amount} /></td>
              </tr>
              <tr>
                <td className="py-0.5 text-slate-600">payment_id</td>
                <td className="py-0.5">{qr.payment_id || "—"}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {/* Adjustment proposal — but only when the parity gate passed.
          For PARITY_GAP rows adj is null and this block is skipped. */}
      {adj && (
        <div className="bg-white border border-slate-200 rounded p-3"
             data-testid={`adjustment-${row.order_id}`}>
          <div className="text-[11px] font-bold text-slate-700 mb-2">
            اقتراح التصحيح (Dry-Run — لا يُرسَل لقيود):
          </div>
          {adj.no_adjustment_needed ? (
            <div className="text-[11px] text-sky-700">
              <Info className="inline-block w-3.5 h-3.5 mr-1" />
              المحاكاة تُطابق سلة أصلاً — لا حاجة لأي تعديل.
            </div>
          ) : adj.reason ? (
            <div className="text-[11px] text-rose-700">
              <XCircle className="inline-block w-3.5 h-3.5 mr-1" />
              تعذّر التصحيح — السبب: <span className="font-mono">{adj.reason}</span>
              {adj.chosen_line_description && (
                <span className="ml-2">
                  (السطر المُختبر: {adj.chosen_line_description})
                </span>
              )}
            </div>
          ) : (
            <table className="w-full text-[11px] font-mono">
              <tbody>
                <tr>
                  <td className="py-0.5 text-slate-600">السطر المُختار</td>
                  <td className="py-0.5">
                    #{adj.chosen_idx} · {adj.chosen_line_description || "—"}
                  </td>
                </tr>
                <tr>
                  <td className="py-0.5 text-slate-600">الخصم الحالي</td>
                  <td className="py-0.5"><Money value={adj.current_discount} dp={4} /></td>
                </tr>
                <tr>
                  <td className="py-0.5 text-slate-600">قيمة التعديل (net)</td>
                  <td className="py-0.5"><Diff value={adj.adjustment_net} /></td>
                </tr>
                <tr>
                  <td className="py-0.5 text-slate-600">الخصم بعد التعديل</td>
                  <td className="py-0.5"><Money value={adj.new_discount} dp={4} /></td>
                </tr>
                <tr className="border-t border-slate-100">
                  <td className="py-0.5 text-slate-600">simulated_before</td>
                  <td className="py-0.5"><Money value={adj.simulated_before} /></td>
                </tr>
                <tr>
                  <td className="py-0.5 text-slate-600">diff_before</td>
                  <td className="py-0.5"><Diff value={adj.diff_before} /></td>
                </tr>
                <tr>
                  <td className="py-0.5 text-slate-600">simulated_after</td>
                  <td className="py-0.5"><Money value={adj.simulated_after} /></td>
                </tr>
                <tr>
                  <td className="py-0.5 text-slate-600">diff_after</td>
                  <td className="py-0.5"><Diff value={adj.diff_after} /></td>
                </tr>
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Payload columns + per-line parity gap */}
      <div className="bg-white border border-slate-200 rounded p-2 overflow-x-auto">
        <div className="text-[11px] font-bold text-slate-700 mb-1">
          مقارنة السطور (Payload → Local Sim → Qoyod Response):
        </div>
        <table className="w-full text-[10px] font-mono whitespace-nowrap">
          <thead className="text-slate-500 border-b border-slate-200">
            <tr>
              <th className="text-right py-1 px-1">#</th>
              <th className="text-right py-1 px-1">الوصف</th>
              <th className="text-right py-1 px-1">payload_unit_price</th>
              <th className="text-right py-1 px-1">payload_qty</th>
              <th className="text-right py-1 px-1">payload_discount</th>
              <th className="text-right py-1 px-1">payload_tax%</th>
              <th className="text-right py-1 px-1 bg-sky-50">sim_net</th>
              <th className="text-right py-1 px-1 bg-sky-50">sim_gross</th>
              <th className="text-right py-1 px-1 bg-amber-50">qoyod_net</th>
              <th className="text-right py-1 px-1 bg-amber-50">qoyod_tax</th>
              <th className="text-right py-1 px-1 bg-amber-50">qoyod_total</th>
              <th className="text-right py-1 px-1 bg-rose-50">line_gap</th>
            </tr>
          </thead>
          <tbody>
            {(row.payload_columns || []).map((pc, i) => {
              const sim = (row.simulated_lines || [])[i] || {};
              const qoyodLine = (row.qoyod_response_lines || [])[i] || {};
              return (
                <tr key={i}
                    data-testid={`payload-line-${row.order_id}-${i}`}
                    className="border-t border-slate-100">
                  <td className="py-0.5 px-1">{i}</td>
                  <td className="py-0.5 px-1">{pc.description || "—"}</td>
                  <td className="py-0.5 px-1"><Money value={pc.qoyod_payload_unit_price} dp={4} /></td>
                  <td className="py-0.5 px-1"><Money value={pc.qoyod_payload_quantity} dp={0} /></td>
                  <td className="py-0.5 px-1"><Money value={pc.qoyod_payload_discount} dp={4} /></td>
                  <td className="py-0.5 px-1">{Number(pc.qoyod_payload_tax_percent || 0).toFixed(2)}%</td>
                  <td className="py-0.5 px-1 bg-sky-50/50"><Money value={sim.line_net} /></td>
                  <td className="py-0.5 px-1 bg-sky-50/50"><Money value={sim.line_gross} /></td>
                  <td className="py-0.5 px-1 bg-amber-50/50"><Money value={qoyodLine.qoyod_response_line_net} /></td>
                  <td className="py-0.5 px-1 bg-amber-50/50"><Money value={qoyodLine.qoyod_response_tax} /></td>
                  <td className="py-0.5 px-1 bg-amber-50/50"><Money value={qoyodLine.qoyod_response_line_total} /></td>
                  <td className="py-0.5 px-1 bg-rose-50/50">
                    <Diff value={qoyodLine.local_vs_qoyod_line_gap} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {row.skip_reason && (
        <div className="text-[11px] text-slate-600 bg-slate-50 border border-slate-200 rounded p-2"
             data-testid={`skip-reason-${row.order_id}`}>
          <strong>سبب التخطّي:</strong>{" "}
          <span className="font-mono">{row.skip_reason}</span>
        </div>
      )}
    </div>
  );
}

export default function QoyodRoundingDryRun() {
  const [loading, setLoading] = useState(false);
  const [report,  setReport]  = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [outcomeFilter, setOutcomeFilter] = useState("ELIGIBLE");

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(
        `${API}/integrations/qoyod/admin/rounding-dry-run?limit=200`
      );
      setReport(data);
    } catch (_) {
      alert("تعذّر تحميل المحاكاة. تأكد من تسجيل الدخول.");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const visible = useMemo(() => {
    if (!report?.results) return [];
    return report.results.filter((r) => {
      if (outcomeFilter === "ALL") return true;
      if (outcomeFilter === "ELIGIBLE") return r.eligible;
      if (outcomeFilter === "PARITY_GAP")
        return r.outcome === "parity_gap_needs_qoyod_model";
      if (outcomeFilter === "SKIPPED")  return !r.eligible;
      if (outcomeFilter === "SUCCEEDED")
        return r.outcome === "adjustment_succeeded"
            || r.outcome === "no_adjustment_needed";
      if (outcomeFilter === "FAILED")
        return r.outcome && r.outcome.startsWith("adjustment_failed");
      return true;
    });
  }, [report, outcomeFilter]);

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-5" dir="rtl">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-extrabold text-slate-800">
            🧪 محاكاة Phase 2 (Dry-Run)
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Iter-290k — قراءة فقط. يُحاكي الإصلاح المقترح للفروقات الصغيرة
            (0.01 / 0.02) باستخدام Decimal + ROUND_HALF_UP. <strong>لا يُرسَل
            شيء إلى قيود، ولا يُحفَظ شيء في قاعدة البيانات.</strong>
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          data-testid="btn-refresh-dry-run"
          className="flex items-center gap-2 px-4 py-2 text-sm font-bold rounded-md bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          {loading ? "جاري المحاكاة..." : "إعادة المحاكاة"}
        </button>
      </header>

      {/* Phase-2 scope reminder */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 text-[11px] text-amber-900 flex items-start gap-2"
           data-testid="phase2-scope-banner">
        <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
        <div>
          <strong>نطاق Phase 2:</strong> فروقات 0.01 و 0.02 فقط · ضمن
          buckets <span className="font-mono">QOYOD_SERVER_SIDE_ROUNDING /
          MULTI_LINE_CUMULATIVE / SHIPPING / INVOICE_TOTAL</span>.
          مستثنى: <span className="font-mono">DISCOUNT_ALLOCATION</span> (يحتاج
          RCA مستقل) و <span className="font-mono">MATERIAL_MISMATCH</span> (ليست
          تقريباً) والبيانات الناقصة والطلبات قبل إصلاح فرق الضريبة.
        </div>
      </div>

      {/* Summary */}
      {report && (
        <section className="grid md:grid-cols-6 gap-3"
                 data-testid="dry-run-summary">
          {[
            ["scanned_count",   "مفحوصة",            "slate"],
            ["eligible_count",  "مؤهلة",              "amber"],
            ["succeeded_count", "نجحت بعد تعديل",      "emerald"],
            ["no_adjustment_needed_count", "متطابقة",  "sky"],
            ["parity_gap_count", "PARITY GAP",         "amber"],
            ["failed_count",    "فشلت",                "rose"],
          ].map(([key, label, tone]) => (
            <div key={key}
                 className={`rounded-xl p-3 border ${TONE_BG[tone]}`}
                 data-testid={`stat-${key}`}>
              <div className="text-[10px] font-bold opacity-70">{label}</div>
              <div className="text-2xl font-extrabold font-mono">
                {report[key] ?? 0}
              </div>
            </div>
          ))}
        </section>
      )}

      {/* Iter-290k.1 — Parity histogram across ALL scanned rows.
          If most rows are PARITY_GAP_*, our Decimal simulator is
          NOT a faithful model of قيود's server-side math yet. */}
      {report && Object.keys(report.parity_histogram || {}).length > 0 && (
        <section className="bg-white border border-slate-200 rounded-xl p-3"
                 data-testid="parity-histogram">
          <div className="text-[11px] font-bold text-slate-600 mb-2">
            توزّع الـ Parity (Local-sim vs Qoyod-actual):
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(report.parity_histogram).map(([key, count]) => (
              <div key={key} className="flex items-center gap-1"
                   data-testid={`parity-count-${key}`}>
                <ParityPill parity={key} />
                <span className="text-[10px] font-mono text-slate-600">× {count}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Skip histogram */}
      {report && Object.keys(report.skip_histogram || {}).length > 0 && (
        <section className="bg-white border border-slate-200 rounded-xl p-3"
                 data-testid="skip-histogram">
          <div className="text-[11px] font-bold text-slate-600 mb-2">
            أسباب التخطّي:
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(report.skip_histogram).map(([reason, count]) => (
              <div key={reason} className="flex items-center gap-1"
                   data-testid={`skip-${reason}`}>
                <span className="text-[10px] bg-slate-100 border border-slate-200 rounded px-1.5 py-0.5">
                  {SKIP_REASON_LABELS[reason] || reason}
                </span>
                <span className="text-[10px] font-mono text-slate-600">× {count}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Filters */}
      {report && (
        <section className="bg-white border border-slate-200 rounded-xl p-3 flex flex-wrap items-center gap-3">
          <div className="text-[11px] font-bold text-slate-600">تصفية:</div>
          <select
            value={outcomeFilter}
            onChange={(e) => setOutcomeFilter(e.target.value)}
            data-testid="filter-outcome"
            className="text-xs border border-slate-300 rounded px-2 py-1 bg-white"
          >
            <option value="ELIGIBLE">المؤهلة فقط</option>
            <option value="PARITY_GAP">PARITY GAP فقط</option>
            <option value="SUCCEEDED">نجحت / متطابقة</option>
            <option value="FAILED">فشلت</option>
            <option value="SKIPPED">المتخطّاة</option>
            <option value="ALL">الكل</option>
          </select>
          <div className="ms-auto text-[11px] text-slate-500">
            <strong>{visible.length}</strong> من <strong>{report.results?.length || 0}</strong>
          </div>
        </section>
      )}

      {/* Results table */}
      {report && visible.length > 0 && (
        <section className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-slate-50 text-slate-700">
                <tr>
                  <th className="px-3 py-2 text-right font-bold">رقم الطلب</th>
                  <th className="px-3 py-2 text-right font-bold">Bucket</th>
                  <th className="px-3 py-2 text-right font-bold">Parity</th>
                  <th className="px-3 py-2 text-right font-bold">Salla</th>
                  <th className="px-3 py-2 text-right font-bold">Local sim</th>
                  <th className="px-3 py-2 text-right font-bold">Qoyod actual</th>
                  <th className="px-3 py-2 text-right font-bold">sim − qoyod</th>
                  <th className="px-3 py-2 text-right font-bold">النتيجة</th>
                  <th className="w-12"></th>
                </tr>
              </thead>
              <tbody>
                {visible.map((row) => {
                  const expanded = expandedId === row.row_id;
                  return (
                    <RowAndDetails
                      key={row.row_id || row.order_id}
                      row={row}
                      expanded={expanded}
                      onToggle={() => setExpandedId(
                        expanded ? null : row.row_id || row.order_id)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {report && visible.length === 0 && !loading && (
        <section className="bg-slate-50 border border-slate-200 rounded-xl p-4 text-center text-sm text-slate-600"
                 data-testid="dry-run-empty">
          لا توجد نتائج بهذا الفلتر.
        </section>
      )}
    </div>
  );
}

function RowAndDetails({ row, expanded, onToggle }) {
  return (
    <>
      <tr className="border-t border-slate-100"
          data-testid={`dry-run-row-${row.order_id}`}>
        <td className="px-3 py-2 font-mono text-[11px]">
          {row.order_number || row.order_id}
        </td>
        <td className="px-3 py-2 font-mono text-[10px]">
          {row.bucket}
        </td>
        <td className="px-3 py-2"><ParityPill parity={row.parity} /></td>
        <td className="px-3 py-2"><Money value={row.salla_total} /></td>
        <td className="px-3 py-2"><Money value={row.simulated_qoyod_invoice_total} /></td>
        <td className="px-3 py-2"><Money value={row.qoyod_actual_total} /></td>
        <td className="px-3 py-2"><Diff value={row.simulated_minus_qoyod_actual} /></td>
        <td className="px-3 py-2"><OutcomePill outcome={row.outcome} /></td>
        <td className="px-3 py-2">
          <button
            onClick={onToggle}
            className="text-xs text-sky-700 hover:underline"
            data-testid={`dry-run-toggle-${row.order_id}`}
          >
            {expanded ? "إخفاء" : "تفاصيل"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-slate-50">
          <td colSpan={9} className="px-3 py-3">
            <ExpandedDetails row={row} />
          </td>
        </tr>
      )}
    </>
  );
}
