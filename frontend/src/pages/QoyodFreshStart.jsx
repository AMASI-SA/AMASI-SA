/**
 * Qoyod Fresh-Start Audit — STRICTLY READ-ONLY snapshot UI.
 *
 * Purpose
 * ───────
 * Before Mezan starts pushing data to Qoyod, the operator wants a
 * forensic count of what Qoyod ALREADY contains (legacy direct-Salla
 * integration data). This page never deletes, edits, or updates
 * anything — it only displays counts, histograms, and risk flags.
 *
 * Scope (locked):
 *   ✅ Invoices, Receipts, Products, Customers — counted.
 *   ❌ Chart of Accounts, Branches, Taxes, Settings — NEVER queried here.
 */
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";

const API = process.env.REACT_APP_BACKEND_URL + "/api";


function Card({ title, value, hint, tone = "default", testid }) {
  const t = {
    default: "border-slate-200 bg-white",
    danger:  "border-rose-300 bg-rose-50/50",
    success: "border-emerald-300 bg-emerald-50/50",
    warn:    "border-amber-300 bg-amber-50/50",
  }[tone] || "border-slate-200 bg-white";
  return (
    <div className={`rounded-xl border ${t} p-4`} data-testid={testid}>
      <div className="text-[11px] font-bold text-slate-500">{title}</div>
      <div className="text-2xl font-extrabold text-slate-900 mt-1 font-mono"
           data-testid={`${testid}-value`}>
        {value}
      </div>
      {hint && (
        <div className="text-[11px] text-slate-500 mt-1 leading-snug">{hint}</div>
      )}
    </div>
  );
}

function MonthHistogram({ data, testid }) {
  const entries = useMemo(() => {
    const arr = Object.entries(data || {});
    arr.sort(([a], [b]) => a.localeCompare(b));
    return arr;
  }, [data]);
  if (entries.length === 0) {
    return <div className="text-xs text-slate-400 italic">لا توجد بيانات شهرية</div>;
  }
  const max = Math.max(...entries.map(([, v]) => v));
  return (
    <div className="space-y-1" data-testid={testid}>
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-center gap-2 text-[11px]">
          <span className="w-16 text-slate-600 font-mono">{k}</span>
          <div className="flex-1 bg-slate-100 rounded h-4 relative overflow-hidden">
            <div
              className="absolute inset-y-0 right-0 bg-sky-500"
              style={{ width: `${(v / max) * 100}%` }} />
          </div>
          <span className="w-12 text-right font-mono font-bold text-slate-700">
            {v}
          </span>
        </div>
      ))}
    </div>
  );
}

function FlagPill({ flag }) {
  const tone =
    flag.severity === "warning" ? "bg-rose-100 text-rose-800 border-rose-300"
    : flag.severity === "info"  ? "bg-sky-100 text-sky-800 border-sky-300"
    : "bg-slate-100 text-slate-700 border-slate-300";
  return (
    <li className={`rounded-lg border px-3 py-2 ${tone}`}
        data-testid={`flag-${flag.code}`}>
      <div className="flex items-start gap-2 text-xs">
        <span className="font-extrabold">
          {flag.severity === "warning" ? "⚠️"
           : flag.severity === "info" ? "ℹ️" : "•"}
        </span>
        <div className="flex-1">
          <div className="font-bold">{flag.message}</div>
          <div className="text-[10px] mt-0.5 font-mono opacity-70">
            {flag.code} · count={flag.count}
          </div>
        </div>
      </div>
    </li>
  );
}

function SamplesTable({ rows, columns, testid }) {
  if (!rows || rows.length === 0) {
    return (
      <div className="text-[11px] text-slate-400 italic">
        لا توجد أمثلة لعرضها
      </div>
    );
  }
  return (
    <div className="overflow-x-auto rounded border border-slate-200">
      <table className="w-full text-[11px]" dir="rtl" data-testid={testid}>
        <thead className="bg-slate-50">
          <tr>
            {columns.map((c) => (
              <th key={c.key}
                  className="text-right px-2 py-1.5 font-bold text-slate-600">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c.key}
                    className="px-2 py-1 text-slate-700 font-mono break-all">
                  {String(r[c.key] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({ title, subtitle, children }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 md:p-5 mb-4">
      <h3 className="text-base font-extrabold text-slate-800">{title}</h3>
      {subtitle && (
        <p className="text-[12px] text-slate-500 mt-0.5 mb-3">{subtitle}</p>
      )}
      <div className="space-y-3 mt-3">{children}</div>
    </section>
  );
}


export default function QoyodFreshStart() {
  const [audit, setAudit] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(
        `${API}/integrations/qoyod/fresh-start/audit`);
      setAudit(data?.audit || null);
    } catch (e) {
      toast.error("تعذّر تحميل التقرير");
    } finally {
      setLoading(false);
    }
  };

  const runAudit = async () => {
    if (!window.confirm(
      "بدء Audit جديد من قيود — قراءة فقط، لا يوجد أي حذف أو تعديل.\n"
      + "قد يستغرق دقيقة أو أكثر حسب حجم البيانات. متابعة؟"
    )) return;
    setRunning(true);
    try {
      const { data } = await axios.post(
        `${API}/integrations/qoyod/fresh-start/audit/run`,
        {}, { timeout: 300000 });
      if (data?.ok) {
        toast.success(
          `اكتمل التقرير — ${data.summary?.totals?.invoices || 0} فاتورة، `
          + `${data.summary?.totals?.customers || 0} عميل`);
        await load();
      } else {
        toast.error(data?.error?.message || "فشل التقرير");
      }
    } catch (e) {
      toast.error(e.response?.data?.detail
                  || e.message || "فشل تشغيل التقرير");
    } finally {
      setRunning(false);
    }
  };

  useEffect(() => { load(); }, []);

  if (loading) {
    return <div className="p-8 text-center text-slate-500">جاري التحميل…</div>;
  }

  const s = audit?.summary || {};
  const totals = s.totals || {};
  const inv = s.invoices || {};
  const rec = s.receipts || {};
  const prods = s.products || {};
  const cust = s.customers || {};
  const flags = s.flags || [];

  return (
    <div dir="rtl" className="max-w-6xl mx-auto p-4 md:p-6"
         data-testid="qoyod-fresh-start-page">
      <header className="mb-4">
        <h1 className="text-2xl md:text-3xl font-extrabold text-slate-900">
          🔍 تقرير Fresh-Start — قراءة فقط
        </h1>
        <p className="text-sm text-slate-500 mt-1 leading-relaxed">
          فحص ما يحتويه حسابك في قيود قبل أن يصبح ميزان المصدر الوحيد.
          هذه الصفحة <strong>لا تعدّل ولا تحذف</strong> أي بيانات.
          النطاق: الفواتير، سندات القبض، المنتجات، العملاء فقط.
          إعداداتك المحاسبية (دليل الحسابات، الفروع، الضرائب) لا تُمَس.
        </p>
      </header>

      {/* Action bar */}
      <div className="rounded-xl border border-slate-200 bg-white p-4 mb-4
                      flex items-center justify-between gap-3"
           data-testid="audit-actionbar">
        <div>
          {audit ? (
            <div className="space-y-0.5">
              <div className="text-sm font-bold text-slate-700">
                آخر تشغيل:&nbsp;
                <span data-testid="audit-last-run-at" className="font-mono">
                  {audit.started_at
                    ? new Date(audit.started_at).toLocaleString("ar-SA")
                    : "—"}
                </span>
              </div>
              <div className="text-[11px] text-slate-500">
                الحالة: <span data-testid="audit-status"
                             className={audit.status === "completed"
                                          ? "text-emerald-700 font-bold"
                                          : "text-rose-700 font-bold"}>
                  {audit.status === "completed" ? "مكتمل ✓"
                   : audit.status === "failed"   ? "فشل ✗"
                   : audit.status}
                </span>
                {audit.error && (
                  <span className="text-rose-600 mr-2">
                    — {audit.error.message || audit.error.code}
                  </span>
                )}
              </div>
            </div>
          ) : (
            <div className="text-sm text-slate-600"
                 data-testid="audit-no-runs">
              لم يُشغّل أي تقرير بعد. اضغط الزر لبدء أول فحص.
            </div>
          )}
        </div>
        <button
          onClick={runAudit}
          disabled={running}
          data-testid="btn-run-audit"
          className="px-5 py-2.5 text-sm font-extrabold rounded-lg bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-50 disabled:cursor-wait">
          {running ? "جاري الفحص…" : (audit ? "🔄 تشغيل تقرير جديد" : "▶️ تشغيل أول تقرير")}
        </button>
      </div>

      {!audit && !running && (
        <Section title="عن هذا التقرير" subtitle="ما الذي سيُفحص؟">
          <ul className="text-sm text-slate-700 space-y-1.5 list-disc pr-5">
            <li>عدد الفواتير الإجمالي + توزيعها على الأشهر + كم منها بدون سند قبض.</li>
            <li>عدد المنتجات في قيود + كم منها له SKU.</li>
            <li>عدد العملاء + كم منهم له هاتف/إيميل + كم له فواتير.</li>
            <li>عدد سندات القبض + المرتبطة بفواتير + اليتيمة (orphan).</li>
            <li>إشارات مخاطر للمراجعة قبل أي حذف.</li>
          </ul>
          <div className="rounded-lg bg-emerald-50 border border-emerald-200 p-3 mt-3">
            <div className="text-sm font-bold text-emerald-900">
              🛡️ ضمانات الأمان
            </div>
            <ul className="text-[12px] text-emerald-800 mt-1.5 space-y-0.5 list-disc pr-5">
              <li>لا يوجد DELETE / PUT / PATCH في كل هذا التدفق.</li>
              <li>لا يُستعلم عن دليل الحسابات / الفروع / الضرائب.</li>
              <li>كل القراءات عبر GET فقط مع 100ms cushion بين الصفحات.</li>
            </ul>
          </div>
        </Section>
      )}

      {audit && audit.status === "completed" && (
        <>
          {/* Totals overview */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <Card title="الفواتير" value={totals.invoices ?? 0}
              hint={`بإجمالي ${(inv.total_amount || 0).toLocaleString("ar-SA")} ر.س`}
              testid="card-total-invoices" />
            <Card title="سندات القبض" value={totals.receipts ?? 0}
              hint={`بإجمالي ${(rec.total_amount || 0).toLocaleString("ar-SA")} ر.س`}
              testid="card-total-receipts" />
            <Card title="المنتجات" value={totals.products ?? 0}
              hint={`${prods.with_sku ?? 0} بـ SKU · ${prods.without_sku ?? 0} بدون`}
              testid="card-total-products" />
            <Card title="العملاء" value={totals.customers ?? 0}
              hint={`${cust.has_invoices ?? 0} له فواتير · ${cust.no_invoices ?? 0} بدون`}
              testid="card-total-customers" />
          </div>

          {/* Flags */}
          {flags.length > 0 && (
            <Section title="🚩 إشارات للمراجعة"
                     subtitle="ليست أخطاء — مجرد ملاحظات تحتاج فهماً قبل اتخاذ أي قرار حذف">
              <ul className="space-y-1.5" data-testid="audit-flags-list">
                {flags.map((f, i) => <FlagPill key={i} flag={f} />)}
              </ul>
            </Section>
          )}

          {/* Invoices */}
          <Section title="📄 الفواتير"
                   subtitle="تفصيل الفواتير الموجودة في قيود">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
              <Card title="بـ external_reference" value={inv.with_external_ref ?? 0}
                hint="غالباً من ربط سلة القديم" tone="default"
                testid="card-inv-with-ref" />
              <Card title="بدون external_reference" value={inv.without_external_ref ?? 0}
                hint="قد تكون يدوية" tone="warn"
                testid="card-inv-without-ref" />
              <Card title="مع سند قبض" value={inv.with_receipt ?? 0}
                tone="success" testid="card-inv-with-receipt" />
              <Card title="بدون سند قبض" value={inv.without_receipt ?? 0}
                tone="danger" testid="card-inv-without-receipt" />
            </div>

            <div className="grid md:grid-cols-2 gap-4">
              <div>
                <div className="text-xs font-bold text-slate-700 mb-2">
                  التوزيع الشهري
                </div>
                <MonthHistogram data={inv.by_month}
                                testid="hist-invoices-by-month" />
              </div>
              <div>
                <div className="text-xs font-bold text-slate-700 mb-2">
                  حسب الحالة (status)
                </div>
                <div className="space-y-1 text-[11px]"
                     data-testid="inv-by-status">
                  {Object.entries(inv.by_status || {}).map(([k, v]) => (
                    <div key={k} className="flex justify-between
                                              border-b border-slate-100 py-0.5">
                      <span className="font-mono text-slate-600">{k}</span>
                      <span className="font-bold">{v}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Samples */}
            {(inv.samples?.no_receipt?.length > 0) && (
              <div className="mt-3">
                <div className="text-xs font-bold text-slate-700 mb-1">
                  أمثلة: فواتير بدون سند قبض (أول 5)
                </div>
                <SamplesTable
                  rows={inv.samples.no_receipt}
                  testid="samples-inv-no-receipt"
                  columns={[
                    {key:"id",         label:"ID"},
                    {key:"issue_date", label:"التاريخ"},
                    {key:"total",      label:"المبلغ"},
                    {key:"status",     label:"الحالة"},
                  ]} />
              </div>
            )}
            {(inv.samples?.no_ref?.length > 0) && (
              <div className="mt-3">
                <div className="text-xs font-bold text-slate-700 mb-1">
                  أمثلة: فواتير بدون external_reference (أول 5)
                </div>
                <SamplesTable
                  rows={inv.samples.no_ref}
                  testid="samples-inv-no-ref"
                  columns={[
                    {key:"id",         label:"ID"},
                    {key:"number",     label:"رقم الفاتورة"},
                    {key:"issue_date", label:"التاريخ"},
                    {key:"total",      label:"المبلغ"},
                  ]} />
              </div>
            )}
          </Section>

          {/* Receipts */}
          <Section title="💰 سندات القبض">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
              <Card title="إجمالي السندات" value={rec.total ?? 0}
                testid="card-rec-total" />
              <Card title="فواتير مرتبطة" value={rec.invoice_ids ?? 0}
                testid="card-rec-invoices" />
              <Card title="سندات يتيمة (بدون فاتورة)"
                value={rec.orphan ?? 0}
                tone={rec.orphan > 0 ? "warn" : "default"}
                testid="card-rec-orphan" />
              <Card title="إجمالي المبلغ"
                value={(rec.total_amount || 0).toLocaleString("ar-SA")}
                hint="ر.س" testid="card-rec-amount" />
            </div>
            <div>
              <div className="text-xs font-bold text-slate-700 mb-2">
                التوزيع الشهري
              </div>
              <MonthHistogram data={rec.by_month}
                              testid="hist-receipts-by-month" />
            </div>
          </Section>

          {/* Products */}
          <Section title="📦 المنتجات">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
              <Card title="إجمالي المنتجات" value={prods.total ?? 0}
                testid="card-prod-total" />
              <Card title="بـ SKU" value={prods.with_sku ?? 0}
                tone="success" testid="card-prod-with-sku" />
              <Card title="بدون SKU" value={prods.without_sku ?? 0}
                tone={prods.without_sku > 0 ? "warn" : "default"}
                testid="card-prod-without-sku" />
            </div>
            <div>
              <div className="text-xs font-bold text-slate-700 mb-2">
                التوزيع الشهري للإنشاء
              </div>
              <MonthHistogram data={prods.by_month}
                              testid="hist-products-by-month" />
            </div>
            {(prods.samples?.without_sku?.length > 0) && (
              <div className="mt-3">
                <div className="text-xs font-bold text-slate-700 mb-1">
                  أمثلة: منتجات بدون SKU (أول 5)
                </div>
                <SamplesTable
                  rows={prods.samples.without_sku}
                  testid="samples-prods-no-sku"
                  columns={[
                    {key:"id",   label:"ID"},
                    {key:"name", label:"الاسم"},
                  ]} />
              </div>
            )}
          </Section>

          {/* Customers */}
          <Section title="👥 العملاء">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
              <Card title="إجمالي العملاء" value={cust.total ?? 0}
                testid="card-cust-total" />
              <Card title="له فواتير" value={cust.has_invoices ?? 0}
                tone="success" testid="card-cust-with-inv" />
              <Card title="بدون فواتير" value={cust.no_invoices ?? 0}
                tone={cust.no_invoices > 0 ? "warn" : "default"}
                hint="قد يكون مُدخل يدوياً" testid="card-cust-no-inv" />
              <Card title="ضيوف (Guest)" value={cust.guests ?? 0}
                testid="card-cust-guests" />
            </div>
            <div className="grid grid-cols-2 gap-3 mb-3">
              <Card title="بهاتف" value={cust.with_phone ?? 0}
                testid="card-cust-phone" />
              <Card title="بإيميل" value={cust.with_email ?? 0}
                testid="card-cust-email" />
            </div>
            <div>
              <div className="text-xs font-bold text-slate-700 mb-2">
                التوزيع الشهري
              </div>
              <MonthHistogram data={cust.by_month}
                              testid="hist-customers-by-month" />
            </div>
            {(cust.samples?.no_invoice?.length > 0) && (
              <div className="mt-3">
                <div className="text-xs font-bold text-slate-700 mb-1">
                  أمثلة: عملاء بدون فواتير (أول 5)
                </div>
                <SamplesTable
                  rows={cust.samples.no_invoice}
                  testid="samples-cust-no-inv"
                  columns={[
                    {key:"id",    label:"ID"},
                    {key:"name",  label:"الاسم"},
                    {key:"phone", label:"الهاتف"},
                    {key:"email", label:"الإيميل"},
                  ]} />
              </div>
            )}
            {(cust.samples?.guest?.length > 0) && (
              <div className="mt-3">
                <div className="text-xs font-bold text-slate-700 mb-1">
                  أمثلة: ضيوف (أول 5)
                </div>
                <SamplesTable
                  rows={cust.samples.guest}
                  testid="samples-cust-guest"
                  columns={[
                    {key:"id",   label:"ID"},
                    {key:"name", label:"الاسم"},
                  ]} />
              </div>
            )}
          </Section>

          {/* Footer — next step */}
          <div className="rounded-xl border-2 border-amber-300 bg-amber-50 p-4 mt-4"
               data-testid="audit-next-step">
            <h4 className="text-sm font-extrabold text-amber-900 mb-1">
              ⚠️ الخطوة التالية
            </h4>
            <p className="text-[12px] text-amber-800 leading-relaxed">
              راجع الأرقام أعلاه بعناية. لم يتم حذف أي شيء من قيود.
              إذا أردت المضي في خطة الحذف، أخبرني بالمعايير (تاريخ الفصل،
              ما يُحذف وما يُبقى) وسأبني مرحلة Plan (Preview قبل التنفيذ)
              مع تأكيد <code className="font-mono bg-white px-1.5 py-0.5 rounded
                                          border border-amber-300">DELETE-CONFIRM</code>.
            </p>
          </div>
        </>
      )}

      {audit && audit.status === "failed" && (
        <div className="rounded-xl border border-rose-300 bg-rose-50 p-4">
          <h4 className="text-sm font-extrabold text-rose-900">فشل التقرير</h4>
          <pre className="text-[11px] text-rose-800 mt-1 whitespace-pre-wrap font-mono"
               dir="ltr">
            {JSON.stringify(audit.error || {}, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
