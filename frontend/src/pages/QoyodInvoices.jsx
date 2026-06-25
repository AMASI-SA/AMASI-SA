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

// ─── Compliance Alert Card (Dashboard Alert — Qoyod page only) ───────
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
  const [loadingOrphans, setLoadingOrphans] = useState(true);
  const [loadingInvoices, setLoadingInvoices] = useState(true);
  const [summary, setSummary] = useState(null);
  const [orphans, setOrphans] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [selected, setSelected] = useState(null);

  const loadAll = async () => {
    try {
      const [s, o, inv] = await Promise.all([
        axios.get(`${API}/integrations/qoyod/compliance/summary`),
        axios.get(`${API}/integrations/qoyod/compliance/orphan-orders?limit=200`),
        axios.get(`${API}/integrations/qoyod/invoices?limit=100`),
      ]);
      setSummary(s.data?.summary || null);
      setOrphans(o.data?.items || []);
      setInvoices(inv.data?.items || []);
    } catch (e) {
      toast.error("تعذّر تحميل بيانات قيود");
    } finally {
      setLoadingSummary(false);
      setLoadingOrphans(false);
      setLoadingInvoices(false);
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
