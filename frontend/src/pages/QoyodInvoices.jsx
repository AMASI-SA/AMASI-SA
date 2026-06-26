/**
 * Qoyod Invoices — Monitoring Page (Pre-Day 3 Placeholders).
 *
 * Renders the scaffolding that the Day 4-5 work will fill with real
 * behaviour:
 *   • Compliance Alert card (Dashboard Alert lives ONLY here, per
 *     user spec — does NOT pollute the global Dashboard).
 *   • Invoices Data Grid (currently empty; will populate once the
 *     Day 3 webhook starts writing to `qoyod_invoices`).
 *   • Timeline drawer (shows `stage_history[]` for the selected row).
 *   • Manual Actions panel — 6 buttons, all DISABLED with a tooltip
 *     pointing at "Day 4-5 — Background Worker".
 *
 * What lives where:
 *   GET /api/integrations/qoyod/invoices            → table feed
 *   GET /api/integrations/qoyod/invoices/{order_id} → drawer + timeline
 *   GET /api/integrations/qoyod/compliance/summary  → alert counts
 *   GET /api/integrations/qoyod/compliance/orphan-orders → orphans table
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";

const API = process.env.REACT_APP_BACKEND_URL + "/api";

// ─── Vocabulary translations ─────────────────────────────────────────
const STATUS_AR = {
  pending: "بانتظار المعالجة",
  sent: "أُرسلت ✓",
  invoice_sent_receipt_failed: "فاتورة ناجحة / سند فاشل",
  failed: "فشل",
  retrying: "إعادة المحاولة",
  skipped: "تم التخطّي",
};

const STATUS_COLOR = {
  pending: "bg-slate-100 text-slate-700 border-slate-200",
  sent: "bg-emerald-100 text-emerald-800 border-emerald-200",
  invoice_sent_receipt_failed: "bg-amber-100 text-amber-800 border-amber-200",
  failed: "bg-rose-100 text-rose-800 border-rose-200",
  retrying: "bg-blue-100 text-blue-800 border-blue-200",
  skipped: "bg-zinc-100 text-zinc-700 border-zinc-200",
};

const ELIGIBILITY_AR = {
  not_eligible: "غير مؤهل",
  eligible_pending: "مؤهّل لم يُرسل",
  sent_to_qoyod: "أُرسل إلى قيود",
  failed_before_qoyod: "فشل قبل قيود",
  invoice_sent_receipt_failed: "فاتورة بدون سند",
};

const ELIGIBILITY_COLOR = {
  not_eligible: "bg-slate-50 text-slate-600 border-slate-200",
  eligible_pending: "bg-amber-50 text-amber-700 border-amber-200",
  sent_to_qoyod: "bg-emerald-50 text-emerald-700 border-emerald-200",
  failed_before_qoyod: "bg-rose-50 text-rose-700 border-rose-200",
  invoice_sent_receipt_failed: "bg-orange-50 text-orange-700 border-orange-200",
};

const REASON_AR = {
  order_status_not_completed: "حالة الطلب ليست «تم التنفيذ»",
  order_completed_ready_to_send: "جاهز للإرسال",
  missing_customer_data: "بيانات العميل غير مكتملة",
  missing_product_mapping: "لا يوجد ربط للمنتج",
  payment_method_mapping_missing: "طريقة الدفع غير مرتبطة",
  qoyod_api_error: "رفض من واجهة قيود",
  already_sent: "أُرسلت سابقاً",
};

const STAGE_AR = {
  NEW: "جديد", RECEIVED: "تم الاستقبال", VALIDATED: "تم التحقق",
  NORMALIZED: "تم التطبيع", RULES_APPLIED: "بعد قواعد العمل",
  CUSTOMER_RESOLVED: "تم تجهيز العميل", PRODUCT_RESOLVED: "تم تجهيز المنتجات",
  INVOICE_CREATED: "أُنشئت الفاتورة", RECEIPT_CREATED: "أُنشئ السند",
  COMPLETED: "اكتمل ✓",
  SKIPPED: "تم التخطّي", RETRYING: "إعادة المحاولة",
  FAILED_VALIDATION: "فشل التحقق", FAILED_CUSTOMER: "فشل عند العميل",
  FAILED_PRODUCT: "فشل عند المنتج", FAILED_INVOICE: "فشل الفاتورة",
  FAILED_RECEIPT: "فشل السند", DEAD_LETTER: "متروك (Dead Letter)",
};

// Manual Action placeholders — disabled until Day 4-5
const MANUAL_ACTIONS = [
  { key: "retry",             label: "إعادة المحاولة",       icon: "↻" },
  { key: "recreate_customer", label: "إعادة إنشاء العميل",   icon: "👤" },
  { key: "recreate_products", label: "إعادة إنشاء المنتجات",  icon: "📦" },
  { key: "recreate_invoice",  label: "إعادة إنشاء الفاتورة",  icon: "🧾" },
  { key: "recreate_receipt",  label: "إعادة إنشاء السند",     icon: "💵" },
  { key: "sync_this_order",   label: "مزامنة هذا الطلب",      icon: "⟳" },
];

// ─── Small UI primitives ─────────────────────────────────────────────
function Card({ title, subtitle, tone = "default", children, testid }) {
  const toneCls =
    tone === "alert"   ? "border-amber-300 bg-amber-50/40" :
    tone === "danger"  ? "border-rose-300 bg-rose-50/40" :
    tone === "success" ? "border-emerald-300 bg-emerald-50/30" :
                         "border-slate-200 bg-white";
  return (
    <section className={`rounded-xl border ${toneCls} p-4 md:p-5 mb-4`} data-testid={testid}>
      {(title || subtitle) && (
        <header className="mb-3">
          {title && <h3 className="text-base font-extrabold text-slate-800">{title}</h3>}
          {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
        </header>
      )}
      {children}
    </section>
  );
}

function Pill({ tone = "slate", children, testid }) {
  const toneCls = tone.startsWith("bg-") ? tone :
                  `bg-${tone}-50 text-${tone}-700 border-${tone}-200`;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-semibold ${toneCls}`}
          data-testid={testid}>
      {children}
    </span>
  );
}

function StatCell({ label, value, tone = "slate", testid }) {
  const colorCls =
    tone === "emerald" ? "text-emerald-700" :
    tone === "amber"   ? "text-amber-700"   :
    tone === "rose"    ? "text-rose-700"    :
    tone === "orange"  ? "text-orange-700"  :
                         "text-slate-800";
  return (
    <div className="flex flex-col gap-1 p-3 rounded-lg border border-slate-200 bg-white" data-testid={testid}>
      <span className="text-[11px] text-slate-500 font-bold">{label}</span>
      <span className={`text-2xl font-extrabold tabular-nums ${colorCls}`}>{value ?? "—"}</span>
    </div>
  );
}

function formatDate(d) {
  if (!d) return "—";
  try {
    return new Date(d).toLocaleString("ar-SA", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return String(d); }
}

// ─── Reconciliation Card (Pre-Day 3 Refinement) ──────────────────────
// Three-number diff card per user spec: eligible orders, sent invoices,
// and the gap between them. When there IS a gap a CTA reveals the list.
function ReconciliationCard({ recon, loading, onDrillDown }) {
  if (loading) {
    return <Card title="مطابقة قيود"><span className="text-sm text-slate-500">جاري التحميل…</span></Card>;
  }
  if (!recon) return null;
  const eligible = recon.eligible_orders_count || 0;
  const invoiced = recon.qoyod_invoices_count  || 0;
  const diff     = recon.difference            || 0;
  const tone = diff === 0 ? "success" : "alert";
  const diffTone = diff === 0 ? "emerald" : (diff > 10 ? "rose" : "amber");
  return (
    <Card
      title="مطابقة قيود — Reconciliation"
      subtitle="هذه البطاقة هي المرجع الرئيسي للتأكد من وصول جميع الطلبات المؤهلة إلى قيود."
      tone={tone}
      testid="qoyod-reconciliation-card"
    >
      <div className="grid grid-cols-3 gap-3">
        <StatCell label="عدد الطلبات المؤهلة للإرسال"
                  value={eligible}
                  tone="slate"
                  testid="recon-eligible-count" />
        <StatCell label="عدد الفواتير الموجودة في قيود"
                  value={invoiced}
                  tone="emerald"
                  testid="recon-invoiced-count" />
        <StatCell label="الفرق"
                  value={diff}
                  tone={diffTone}
                  testid="recon-difference" />
      </div>
      {diff > 0 && (
        <div className="mt-4 flex items-center justify-between gap-3 flex-wrap bg-amber-100/60 rounded-lg px-3 py-2.5 border border-amber-200">
          <p className="text-xs text-amber-900 font-bold">
            ⚠️ يوجد {diff} طلب{diff === 1 ? "" : "اً"} مؤهل{diff > 10 ? "ة" : ""} لم تصل إلى قيود بعد.
          </p>
          <button
            type="button"
            onClick={onDrillDown}
            className="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-xs font-extrabold transition"
            data-testid="recon-drill-down"
          >
            عرض الطلبات غير المرسلة ↓
          </button>
        </div>
      )}
      {diff === 0 && eligible > 0 && (
        <p className="mt-3 text-xs text-emerald-800 bg-emerald-100/60 rounded-lg px-3 py-2">
          ✓ كل الطلبات المؤهلة وصلت إلى قيود — لا توجد فجوة.
        </p>
      )}
    </Card>
  );
}


// ─── Day 4 Report Card (Eligibility & Resolution outcomes) ───────────
function Day4ReportCard({ report, loading, onProcessNormalized, onProcessCustomerResolved, running }) {
  if (loading) {
    return <Card title="تقرير Day 4 — أهلية وحل العميل"><span className="text-sm text-slate-500">جاري التحميل…</span></Card>;
  }
  if (!report) return null;
  const t = report.totals || {};
  const sr = report.skipped_reasons || {};
  const dl = report.dead_letter_by_stage || {};
  return (
    <Card
      title="تقرير Day 4 — مراجعة قبل بدء الإرسال الفعلي"
      subtitle="إحصاء لمخرجات قواعد العمل وحل العميل عبر جميع الطلبات المُستقبَلة."
      testid="qoyod-day4-report-card"
    >
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <StatCell label="استُلمت (NORMALIZED)" value={t.normalized} testid="d4-stat-normalized" />
        <StatCell label="جاهز للعميل" value={t.customer_resolved} tone="amber" testid="d4-stat-resolved" />
        <StatCell label="مكتملة" value={t.completed} tone="emerald" testid="d4-stat-completed" />
        <StatCell label="تخطّى" value={t.skipped} testid="d4-stat-skipped" />
        <StatCell label="Dead Letter" value={t.dead_letter} tone="rose" testid="d4-stat-deadletter" />
        <StatCell label="فشل جزئي" value={t.partial_failure} tone="orange" testid="d4-stat-partial" />
      </div>
      {Object.keys(sr).length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-bold text-slate-700 mb-2">أسباب التخطّي:</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(sr).map(([reason, n]) => (
              <Pill key={reason} tone="slate">{(REASON_AR && REASON_AR[reason]) || reason} · {n}</Pill>
            ))}
          </div>
        </div>
      )}
      {Object.keys(dl).length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-bold text-slate-700 mb-2">Dead Letter — حسب آخر مرحلة فشل:</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(dl).map(([stage, n]) => (
              <Pill key={stage} tone="rose">{(STAGE_AR && STAGE_AR[stage]) || stage} · {n}</Pill>
            ))}
          </div>
        </div>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3">
        <button
          type="button"
          onClick={onProcessNormalized}
          disabled={running}
          className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-xs font-extrabold transition disabled:opacity-50"
          data-testid="run-process-normalized">
          ⚙️ تشغيل: NORMALIZED → CUSTOMER_RESOLVED
        </button>
        <button
          type="button"
          onClick={onProcessCustomerResolved}
          disabled={running}
          className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-extrabold transition disabled:opacity-50"
          data-testid="run-process-customer-resolved">
          🚀 تشغيل: CUSTOMER_RESOLVED → INVOICE → COMPLETED
        </button>
        <span className="text-[11px] text-slate-500">
          (يحترم Dry Run Mode + Pre-flight + Payload Snapshot)
        </span>
      </div>
    </Card>
  );
}



function ComplianceAlertCard({ summary, loading }) {
  if (loading) {
    return <Card title="مراقبة الامتثال (Compliance Watch)"><span className="text-sm text-slate-500">جاري التحميل…</span></Card>;
  }
  if (!summary) return null;
  const pending = summary.eligible_pending || 0;
  const failedB = summary.failed_before_qoyod || 0;
  const recFail = summary.invoice_sent_receipt_failed || 0;
  const tone = (pending + failedB + recFail) > 0 ? "alert" : "success";
  return (
    <Card
      title="مراقبة الامتثال — هل وصلت كل الطلبات إلى قيود؟"
      subtitle="تظهر هنا الطلبات بحالة «تم التنفيذ» التي لم تُرسَل إلى قيود بعد، أو فشلت في إحدى المراحل. تُعرض داخل صفحة قيود فقط."
      tone={tone}
      testid="qoyod-compliance-alert"
    >
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatCell label="إجمالي الطلبات المكتملة"
                  value={summary.completed_orders_total}
                  testid="compliance-stat-total" />
        <StatCell label="أُرسلت إلى قيود"
                  value={summary.sent_to_qoyod}
                  tone="emerald"
                  testid="compliance-stat-sent" />
        <StatCell label="مؤهّلة ولم تُرسل"
                  value={pending}
                  tone="amber"
                  testid="compliance-stat-pending" />
        <StatCell label="فشلت قبل قيود"
                  value={failedB}
                  tone="rose"
                  testid="compliance-stat-failed" />
        <StatCell label="فاتورة بدون سند"
                  value={recFail}
                  tone="orange"
                  testid="compliance-stat-receipt-failed" />
      </div>
      {summary.oldest_pending_at && pending > 0 && (
        <p className="text-xs text-amber-800 mt-3 bg-amber-100/60 rounded-lg px-3 py-2">
          ⏳ أقدم طلب مكتمل غير مُرسَل منذ <strong>{formatDate(summary.oldest_pending_at)}</strong>.
        </p>
      )}
    </Card>
  );
}

// ─── Orphan Orders Table (Eligible but not sent) ─────────────────────
function OrphanOrdersSection({ orphans, loading, onOpen }) {
  if (loading) {
    return <Card title="طلبات بانتظار الإرسال إلى قيود"><span className="text-sm text-slate-500">جاري التحميل…</span></Card>;
  }
  if (!orphans || orphans.length === 0) {
    return (
      <Card title="طلبات بانتظار الإرسال إلى قيود" tone="success" testid="qoyod-orphans-empty">
        <p className="text-sm text-emerald-800">✓ لا توجد طلبات «تم التنفيذ» معلّقة. كل الطلبات في طريقها أو وصلت قيود.</p>
      </Card>
    );
  }
  return (
    <Card title={`طلبات بانتظار الإرسال إلى قيود (${orphans.length})`}
          subtitle="هذه الطلبات في حالة «تم التنفيذ» لكنها لم تكتمل في قيود بعد. قد تكون لم تبدأ، أو فشلت في مرحلة معيّنة."
          testid="qoyod-orphans-table">
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="min-w-full text-xs">
          <thead className="bg-slate-50 text-slate-700">
            <tr>
              <th className="px-3 py-2 text-right font-bold">رقم الطلب</th>
              <th className="px-3 py-2 text-right font-bold">حالة قيود</th>
              <th className="px-3 py-2 text-right font-bold">السبب</th>
              <th className="px-3 py-2 text-right font-bold">العميل</th>
              <th className="px-3 py-2 text-right font-bold">الإجمالي</th>
              <th className="px-3 py-2 text-right font-bold">التاريخ</th>
              <th className="px-3 py-2 text-right font-bold">إجراء</th>
            </tr>
          </thead>
          <tbody>
            {orphans.map((o, i) => (
              <tr key={o.salla_order_id || i} className="border-t border-slate-100">
                <td className="px-3 py-2 font-mono text-slate-700">{o.salla_order_id}</td>
                <td className="px-3 py-2">
                  <Pill tone={ELIGIBILITY_COLOR[o.eligibility_status]}>
                    {ELIGIBILITY_AR[o.eligibility_status] || o.eligibility_status}
                  </Pill>
                </td>
                <td className="px-3 py-2 text-slate-600">{REASON_AR[o.eligibility_reason] || o.eligibility_reason}</td>
                <td className="px-3 py-2 text-slate-700">{o.customer_name || o.customer_phone || "—"}</td>
                <td className="px-3 py-2 tabular-nums">{o.total_amount?.toFixed?.(2) ?? "—"}</td>
                <td className="px-3 py-2 text-slate-500">{formatDate(o.order_date)}</td>
                <td className="px-3 py-2">
                  <button
                    type="button"
                    className="text-blue-600 hover:underline text-xs font-bold"
                    onClick={() => onOpen?.(o)}
                    data-testid={`open-orphan-${o.salla_order_id}`}
                  >
                    عرض التفاصيل
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ─── Invoices Data Grid ──────────────────────────────────────────────
function InvoicesTable({ invoices, loading, onOpen }) {
  if (loading) {
    return <Card title="سجل الفواتير في قيود"><span className="text-sm text-slate-500">جاري التحميل…</span></Card>;
  }
  if (!invoices || invoices.length === 0) {
    return (
      <Card title="سجل الفواتير في قيود" testid="qoyod-invoices-empty">
        <p className="text-sm text-slate-500">
          لا توجد فواتير في السجل بعد. ستظهر الفواتير هنا تلقائياً بعد تفعيل
          المرحلة الثالثة (استلام Webhook من Make.com).
        </p>
      </Card>
    );
  }
  return (
    <Card title={`سجل الفواتير في قيود (${invoices.length})`}
          subtitle="الفواتير التي اجتازت أو فشلت في خط أنابيب الإرسال."
          testid="qoyod-invoices-table">
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="min-w-full text-xs">
          <thead className="bg-slate-50 text-slate-700">
            <tr>
              <th className="px-3 py-2 text-right font-bold">طلب سلة</th>
              <th className="px-3 py-2 text-right font-bold">الحالة</th>
              <th className="px-3 py-2 text-right font-bold">المرحلة</th>
              <th className="px-3 py-2 text-right font-bold">قيود — فاتورة</th>
              <th className="px-3 py-2 text-right font-bold">قيود — سند</th>
              <th className="px-3 py-2 text-right font-bold">المحاولات</th>
              <th className="px-3 py-2 text-right font-bold">آخر تحديث</th>
              <th className="px-3 py-2 text-right font-bold"></th>
            </tr>
          </thead>
          <tbody>
            {invoices.map((row, i) => (
              <tr key={row.salla_order_id || i} className="border-t border-slate-100">
                <td className="px-3 py-2 font-mono text-slate-700">{row.salla_order_id}</td>
                <td className="px-3 py-2">
                  <Pill tone={STATUS_COLOR[row.status] || "slate"}>
                    {STATUS_AR[row.status] || row.status}
                  </Pill>
                </td>
                <td className="px-3 py-2 text-slate-700 text-[11px]">
                  {STAGE_AR[row.pipeline_stage] || row.pipeline_stage || "—"}
                </td>
                <td className="px-3 py-2 font-mono text-slate-600">{row.qoyod_invoice_number || "—"}</td>
                <td className="px-3 py-2 font-mono text-slate-600">{row.qoyod_receipt_id || "—"}</td>
                <td className="px-3 py-2 tabular-nums">{row.attempts ?? 0}</td>
                <td className="px-3 py-2 text-slate-500">{formatDate(row.updated_at)}</td>
                <td className="px-3 py-2">
                  <button
                    type="button"
                    className="text-blue-600 hover:underline text-xs font-bold"
                    onClick={() => onOpen?.(row)}
                    data-testid={`open-invoice-${row.salla_order_id}`}
                  >
                    عرض
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ─── Timeline Drawer ─────────────────────────────────────────────────
function TimelineDrawer({ open, item, onClose }) {
  if (!open || !item) return null;
  const history = item.stage_history || item.inbox?.stage_history || [];
  return (
    <div className="fixed inset-0 z-50 flex" onClick={onClose} data-testid="qoyod-timeline-drawer">
      <div className="flex-1 bg-black/40" />
      <div className="w-full max-w-2xl bg-white shadow-2xl overflow-y-auto"
           onClick={(e) => e.stopPropagation()}>
        <header className="sticky top-0 bg-slate-900 text-white p-4 flex items-center justify-between">
          <div>
            <h3 className="text-base font-extrabold">
              تفاصيل الطلب {item.salla_order_id || item.invoice?.salla_order_id}
            </h3>
            <p className="text-xs text-slate-300 mt-0.5">
              المسار الكامل عبر خط الأنابيب (Pipeline Timeline)
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-white hover:text-amber-300 text-2xl leading-none"
            data-testid="close-timeline-drawer"
          >×</button>
        </header>

        <div className="p-5 space-y-4">
          {/* Audit Trail summary — Pre-Day 3 spec */}
          {(item.invoice || item.inbox) && (
            <section className="rounded-xl border border-slate-200 p-4 bg-slate-50" data-testid="audit-trail-summary">
              <h4 className="text-sm font-extrabold text-slate-700 mb-3">
                🧭 سجل التتبع (Audit Trail)
              </h4>
              <dl className="grid grid-cols-2 md:grid-cols-3 gap-3 text-[11px]">
                <div>
                  <dt className="text-slate-500 font-bold">Trace ID</dt>
                  <dd className="font-mono text-slate-700 truncate" title={item.invoice?.trace_id || item.inbox?.trace_id}>
                    {item.invoice?.trace_id || item.inbox?.trace_id || "—"}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500 font-bold">المرحلة الحالية</dt>
                  <dd><Pill tone="slate">{STAGE_AR[item.invoice?.pipeline_stage || item.inbox?.pipeline_stage] || "—"}</Pill></dd>
                </div>
                <div>
                  <dt className="text-slate-500 font-bold">آخر مرحلة نجحت</dt>
                  <dd><Pill tone="emerald">{STAGE_AR[item.invoice?.last_success_stage || item.inbox?.last_success_stage] || "—"}</Pill></dd>
                </div>
                <div>
                  <dt className="text-slate-500 font-bold">آخر مرحلة فشلت</dt>
                  <dd>
                    {(item.invoice?.last_failed_stage || item.inbox?.last_failed_stage)
                      ? <Pill tone="rose">{STAGE_AR[item.invoice?.last_failed_stage || item.inbox?.last_failed_stage]}</Pill>
                      : <span className="text-slate-400">—</span>}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500 font-bold">وقت البداية</dt>
                  <dd className="text-slate-700">{formatDate(item.invoice?.pipeline_started_at || item.inbox?.pipeline_started_at)}</dd>
                </div>
                <div>
                  <dt className="text-slate-500 font-bold">وقت النهاية</dt>
                  <dd className="text-slate-700">{formatDate(item.invoice?.pipeline_finished_at || item.inbox?.pipeline_finished_at)}</dd>
                </div>
                <div>
                  <dt className="text-slate-500 font-bold">مدة التنفيذ</dt>
                  <dd className="text-slate-700">
                    {(() => {
                      const ms = item.invoice?.pipeline_duration_ms ?? item.inbox?.pipeline_duration_ms;
                      if (ms == null) return "—";
                      if (ms < 1000) return `${ms} ms`;
                      if (ms < 60000) return `${(ms/1000).toFixed(2)} ث`;
                      return `${(ms/60000).toFixed(2)} د`;
                    })()}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500 font-bold">عدد المحاولات</dt>
                  <dd className="text-slate-700 tabular-nums">{item.invoice?.attempts ?? item.inbox?.attempts ?? 0}</dd>
                </div>
                <div>
                  <dt className="text-slate-500 font-bold">نتيجة الخط</dt>
                  <dd>
                    {(item.invoice?.pipeline_outcome || item.inbox?.pipeline_outcome)
                      ? <Pill tone="slate">{STAGE_AR[item.invoice?.pipeline_outcome || item.inbox?.pipeline_outcome]}</Pill>
                      : <span className="text-slate-400">—</span>}
                  </dd>
                </div>
              </dl>
            </section>
          )}

          {/* Manual Actions placeholders */}
          <section className="rounded-xl border border-dashed border-slate-300 p-4 bg-slate-50/60">
            <h4 className="text-sm font-extrabold text-slate-700 mb-2">
              ✨ إجراءات يدوية (متاحة بعد إنجاز المرحلة 4-5)
            </h4>
            <p className="text-xs text-slate-500 mb-3">
              هذه الأزرار معطّلة حالياً. سيتم تفعيلها بعد بناء العامل (Worker)
              في المرحلة الرابعة والخامسة.
            </p>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              {MANUAL_ACTIONS.map((a) => (
                <button
                  key={a.key}
                  type="button"
                  disabled
                  title="قريباً — المرحلة 4-5"
                  className="px-3 py-2 rounded-lg border border-slate-300 bg-white text-xs font-bold text-slate-400 cursor-not-allowed flex items-center justify-center gap-1.5"
                  data-testid={`manual-action-${a.key}`}
                >
                  <span aria-hidden>{a.icon}</span>
                  <span>{a.label}</span>
                </button>
              ))}
            </div>
          </section>

          {/* Timeline */}
          <section className="rounded-xl border border-slate-200 p-4 bg-white">
            <h4 className="text-sm font-extrabold text-slate-700 mb-3">
              📜 خط الزمن (Timeline)
            </h4>
            {history.length === 0 ? (
              <p className="text-xs text-slate-500">
                لا يوجد سجل تحوّلات بعد. سيظهر هنا كل انتقال للحالة بعد بدء المعالجة.
              </p>
            ) : (
              <ol className="relative border-r-2 border-slate-200 pr-4 space-y-3">
                {history.slice().reverse().map((h, i) => (
                  <li key={i} className="relative" data-testid={`timeline-entry-${i}`}>
                    <span className="absolute -right-[7px] top-1.5 w-3 h-3 rounded-full bg-blue-500 border-2 border-white" />
                    <div className="text-xs">
                      <div className="flex items-center gap-2 flex-wrap">
                        {h.from_stage && (
                          <Pill tone="slate">{STAGE_AR[h.from_stage] || h.from_stage}</Pill>
                        )}
                        <span className="text-slate-400">←</span>
                        <Pill tone={h.to_stage?.startsWith("FAILED") ? "rose" :
                                    h.to_stage === "COMPLETED" ? "emerald" :
                                    h.to_stage === "RETRYING" ? "blue" : "slate"}>
                          {STAGE_AR[h.to_stage] || h.to_stage}
                        </Pill>
                        <span className="text-slate-500 text-[11px]">
                          {formatDate(h.at)} · بواسطة {h.actor || "system"}
                        </span>
                      </div>
                      {h.note && (
                        <p className="text-slate-600 mt-1 text-[11px]">📝 {h.note}</p>
                      )}
                      {h.error && (
                        <pre className="text-rose-700 mt-1 text-[11px] bg-rose-50/60 rounded p-2 whitespace-pre-wrap break-all">
                          {JSON.stringify(h.error, null, 2)}
                        </pre>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>

          {/* Raw record */}
          {item.invoice && (
            <details className="rounded-xl border border-slate-200 p-4 bg-slate-50">
              <summary className="text-xs font-bold text-slate-600 cursor-pointer">
                البيانات الخام (JSON)
              </summary>
              <pre className="text-[11px] mt-2 whitespace-pre-wrap break-all text-slate-700">
                {JSON.stringify(item, null, 2)}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Main Page ───────────────────────────────────────────────────────
export default function QoyodInvoices() {
  const [loadingSummary, setLoadingSummary] = useState(true);
  const [loadingRecon,   setLoadingRecon]   = useState(true);
  const [loadingOrphans, setLoadingOrphans] = useState(true);
  const [loadingInvoices, setLoadingInvoices] = useState(true);
  const [loadingReport,  setLoadingReport]  = useState(true);
  const [running,        setRunning]        = useState(false);
  const [summary, setSummary] = useState(null);
  const [recon,   setRecon]   = useState(null);
  const [report,  setReport]  = useState(null);
  const [orphans, setOrphans] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [selected, setSelected] = useState(null);

  const loadAll = async () => {
    try {
      const [s, r, rep, o, inv] = await Promise.all([
        axios.get(`${API}/integrations/qoyod/compliance/summary`),
        axios.get(`${API}/integrations/qoyod/compliance/reconciliation`),
        axios.get(`${API}/integrations/qoyod/reports/day4`),
        axios.get(`${API}/integrations/qoyod/compliance/orphan-orders?limit=200`),
        axios.get(`${API}/integrations/qoyod/invoices?limit=100`),
      ]);
      setSummary(s.data?.summary || null);
      setRecon(r.data?.reconciliation || null);
      setReport(rep.data?.report || null);
      setOrphans(o.data?.items || []);
      setInvoices(inv.data?.items || []);
    } catch (e) {
      toast.error("تعذّر تحميل بيانات قيود");
    } finally {
      setLoadingSummary(false);
      setLoadingRecon(false);
      setLoadingReport(false);
      setLoadingOrphans(false);
      setLoadingInvoices(false);
    }
  };

  const runPipeline = async (endpoint, label) => {
    setRunning(true);
    try {
      const { data } = await axios.post(
        `${API}/integrations/qoyod/pipeline/${endpoint}?limit=25`);
      const c = data?.counts || {};
      toast.success(`${label}: ${data?.processed || 0} طلب · ${JSON.stringify(c)}`);
      await loadAll();
    } catch (e) {
      toast.error(`تعذّر تشغيل ${label}`);
    } finally {
      setRunning(false);
    }
  };

  useEffect(() => { loadAll(); }, []); // eslint-disable-line

  const openInvoice = async (row) => {
    try {
      const { data } = await axios.get(
        `${API}/integrations/qoyod/invoices/${encodeURIComponent(row.salla_order_id)}`
      );
      setSelected({
        salla_order_id: row.salla_order_id,
        invoice: data.invoice,
        inbox:   data.inbox,
        stage_history: data.invoice?.stage_history || [],
      });
    } catch (e) {
      // No detail row yet (orphan path) — open a minimal placeholder.
      setSelected({
        salla_order_id: row.salla_order_id,
        invoice: null,
        inbox: null,
        stage_history: [],
      });
    }
  };

  return (
    <div className="max-w-7xl mx-auto p-4 md:p-6" dir="rtl" data-testid="qoyod-invoices-page">
      <header className="mb-5">
        <h1 className="text-2xl md:text-3xl font-extrabold text-slate-900">
          فواتير قيود — مراقبة وإدارة
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          صفحة المراقبة للمرحلة الثالثة. الإجراءات اليدوية placeholders فقط
          إلى أن نُنجز المرحلة 4-5 (العامل الخلفي).
        </p>
      </header>

      <ReconciliationCard
        recon={recon}
        loading={loadingRecon}
        onDrillDown={() => {
          const el = document.querySelector('[data-testid="qoyod-orphans-table"], [data-testid="qoyod-orphans-empty"]');
          if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        }}
      />
      <Day4ReportCard
        report={report}
        loading={loadingReport}
        running={running}
        onProcessNormalized={() => runPipeline("process-normalized", "Day 4")}
        onProcessCustomerResolved={() => runPipeline("process-customer-resolved", "Day 5")}
      />
      <ComplianceAlertCard summary={summary} loading={loadingSummary} />
      <OrphanOrdersSection orphans={orphans} loading={loadingOrphans} onOpen={openInvoice} />
      <InvoicesTable invoices={invoices} loading={loadingInvoices} onOpen={openInvoice} />

      <TimelineDrawer
        open={!!selected}
        item={selected}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}
