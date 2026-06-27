/**
 * QYD-GO — Production Readiness Page.
 *
 * Read-only Checklist + quantitative Report + ACTIVATE button that
 * refuses to flip Production Mode while any check is failing.
 *
 *   GET  /api/integrations/qoyod/go-live/checklist
 *   GET  /api/integrations/qoyod/go-live/report
 *   POST /api/integrations/qoyod/go-live/activate
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { toast } from "sonner";

const API = process.env.REACT_APP_BACKEND_URL + "/api";

function StatCell({ label, value, tone = "slate", testid, sub }) {
  const color =
    tone === "emerald" ? "text-emerald-700 bg-emerald-50 border-emerald-200" :
    tone === "amber"   ? "text-amber-800 bg-amber-50 border-amber-200" :
    tone === "rose"    ? "text-rose-800 bg-rose-50 border-rose-200" :
    tone === "blue"    ? "text-blue-800 bg-blue-50 border-blue-200" :
                         "text-slate-800 bg-white border-slate-200";
  return (
    <div className={`flex flex-col gap-1 p-3 rounded-lg border ${color}`}
         data-testid={testid}>
      <span className="text-[11px] font-bold opacity-75">{label}</span>
      <span className="text-2xl font-extrabold tabular-nums">{value ?? "—"}</span>
      {sub && <span className="text-[10px] opacity-70">{sub}</span>}
    </div>
  );
}

function ChecklistRow({ item, onRequeue, requeueing }) {
  const ok = item.ok;
  const extra = item.extra || {};
  const isFailuresRow = item.key === "outstanding_failures";
  const autoRecoverable = Number(extra.auto_recoverable_count || 0);
  const blocking = Number(extra.blocking_count || 0);
  return (
    <li
      className={`flex items-start gap-3 p-3 rounded-lg border ${
        ok ? "bg-emerald-50/50 border-emerald-200"
           : "bg-rose-50/40 border-rose-200"
      }`}
      data-testid={`checklist-${item.key}`}
    >
      <span className={`flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-base font-extrabold
                       ${ok ? "bg-emerald-500 text-white"
                            : "bg-rose-500 text-white"}`}>
        {ok ? "✓" : "✗"}
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-extrabold text-slate-900">
          {item.label}
        </div>
        <div className={`text-xs ${ok ? "text-emerald-800" : "text-rose-800"}`}>
          {item.detail}
        </div>
        {isFailuresRow && (autoRecoverable > 0 || blocking > 0) && (
          <div className="mt-2 space-y-2"
               data-testid="outstanding-failures-detail">
            {autoRecoverable > 0 && (
              <div className="flex items-center justify-between gap-2 bg-blue-50 border border-blue-200 rounded p-2"
                   data-testid="auto-recoverable-banner">
                <span className="text-[11px] text-blue-900 font-bold">
                  🔄 {autoRecoverable} فاتورة قابلة للإصلاح تلقائياً (خطأ معروف تم إصلاحه)
                </span>
                <button
                  type="button"
                  onClick={onRequeue}
                  disabled={requeueing}
                  data-testid="btn-trigger-auto-requeue"
                  className="px-2.5 py-1 text-[11px] font-extrabold rounded
                             bg-blue-600 text-white hover:bg-blue-700
                             disabled:opacity-50 whitespace-nowrap">
                  {requeueing ? "جاري…" : "▶ إعادة المعالجة الآن"}
                </button>
              </div>
            )}
            {blocking > 0 && Array.isArray(extra.sample_blocking) &&
             extra.sample_blocking.length > 0 && (
              <details className="bg-rose-50 border border-rose-200 rounded p-2"
                       data-testid="blocking-failures-sample">
                <summary className="text-[11px] font-bold text-rose-900 cursor-pointer">
                  عرض أول {extra.sample_blocking.length} فاتورة عالقة (تحتاج مراجعة يدوية)
                </summary>
                <ul className="mt-2 space-y-1 text-[10px] font-mono" dir="ltr">
                  {extra.sample_blocking.map((s) => (
                    <li key={s.row_id} className="text-rose-800">
                      {s.last_failed_stage} · {s.error_code || "—"} · {s.trace_id?.slice(0, 8)}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

export default function QoyodGoLive() {
  const [loadingChecklist, setLoadingChecklist] = useState(true);
  const [loadingReport,    setLoadingReport]    = useState(true);
  const [checklist, setChecklist] = useState(null);
  const [report,    setReport]    = useState(null);
  const [activating, setActivating] = useState(false);
  const [requeueing, setRequeueing] = useState(false);
  const [diag, setDiag] = useState(null);
  const [diagLoading, setDiagLoading] = useState(false);
  const [identityConfirmed, setIdentityConfirmed] = useState(false);

  const runIdentityDiagnostics = async () => {
    setDiagLoading(true);
    try {
      const { data } = await axios.get(
        `${API}/integrations/qoyod/diagnostics/identity`);
      setDiag(data);
      setIdentityConfirmed(false); // require re-confirm on every refresh
    } catch (e) {
      toast.error("تعذّر تشغيل تشخيص الهوية");
    } finally {
      setDiagLoading(false);
    }
  };

  const loadAll = async () => {
    setLoadingChecklist(true);
    setLoadingReport(true);
    try {
      const [c, r] = await Promise.all([
        axios.get(`${API}/integrations/qoyod/go-live/checklist`),
        axios.get(`${API}/integrations/qoyod/go-live/report`),
      ]);
      setChecklist(c.data?.checklist || null);
      setReport(r.data?.report || null);
    } catch (e) {
      toast.error("تعذّر تحميل التحقّق من جاهزية الإنتاج");
    } finally {
      setLoadingChecklist(false);
      setLoadingReport(false);
    }
  };

  useEffect(() => { loadAll(); }, []);

  const onAutoRequeue = async () => {
    setRequeueing(true);
    try {
      const { data } = await axios.post(
        `${API}/integrations/qoyod/dead-letter/auto-requeue`, {});
      const n = data?.result?.requeued || 0;
      if (n > 0) {
        toast.success(`تم إعادة معالجة ${n} فاتورة. سيُكمل العامل الخلفي إرسالها خلال ثوانٍ.`);
      } else {
        toast.info("لا توجد فواتير قابلة للإصلاح حالياً.");
      }
      // Refresh after the worker has had a moment to drain.
      setTimeout(() => loadAll(), 2500);
    } catch (e) {
      toast.error("تعذّر تشغيل إعادة المعالجة التلقائية");
    } finally {
      setRequeueing(false);
    }
  };

  const onActivate = async () => {
    if (!checklist?.all_passed) {
      toast.warning("لا يمكن التفعيل — هناك بنود لم تجتز التحقق بعد.");
      return;
    }
    if (!identityConfirmed) {
      toast.warning(
        "يجب تشغيل تشخيص الهوية والتأكيد أن حساب قيود صحيح قبل التفعيل.");
      return;
    }
    if (!window.confirm(
      "هل تريد بالفعل تفعيل وضع الإنتاج؟\n" +
      "سيتم إيقاف Dry Run وتشغيل الإرسال الفعلي إلى قيود.\n" +
      "تأكّد من تنفيذ الـCheckList بالكامل قبل المتابعة.")) return;
    setActivating(true);
    try {
      const { data } = await axios.post(
        `${API}/integrations/qoyod/go-live/activate`);
      toast.success("تم تفعيل وضع الإنتاج بنجاح! 🎉");
      await loadAll();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      if (detail?.code === "activation_blocked") {
        toast.error(`التفعيل مرفوض — البنود غير المكتملة: ${detail.reasons.join(" · ")}`);
      } else {
        toast.error("تعذّر تفعيل وضع الإنتاج");
      }
    } finally {
      setActivating(false);
      await loadAll();
    }
  };

  const allPassed = !!checklist?.all_passed;
  const isLiveAlready = checklist?.context?.enabled_currently &&
                        !checklist?.context?.dry_run_mode_currently_on;

  return (
    <div className="max-w-7xl mx-auto p-4 md:p-6" dir="rtl"
         data-testid="qoyod-go-live-page">
      <header className="mb-5">
        <h1 className="text-2xl md:text-3xl font-extrabold text-slate-900">
          🚀 QYD-GO — جاهزية الإنتاج (Production Readiness)
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          صفحة مستقلة للتحقق من اكتمال إعدادات قيود قبل تفعيل وضع الإنتاج.
          لا يُسمح بإيقاف Dry Run إلا بعد اجتياز جميع البنود.
        </p>
      </header>

      {/* ── Status banner ────────────────────────────────────────── */}
      {!loadingChecklist && (
        <div className={`rounded-xl border-2 p-4 mb-5 ${
          isLiveAlready
            ? "bg-emerald-50 border-emerald-300"
            : allPassed
              ? "bg-blue-50 border-blue-300"
              : "bg-amber-50 border-amber-300"}`}
            data-testid="go-live-status-banner">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <h3 className="text-base font-extrabold text-slate-900">
                {isLiveAlready
                  ? "✅ وضع الإنتاج مُفعَّل حالياً"
                  : allPassed
                    ? "🎯 جميع البنود اجتازت التحقق — جاهز للتفعيل"
                    : `⚠️ ${checklist?.totals?.failed || 0} بند(ود) لم تجتز التحقق بعد`}
              </h3>
              <p className="text-xs text-slate-600 mt-0.5">
                Dry Run: <strong>{checklist?.context?.dry_run_mode_currently_on ? "مُفعَّل" : "مُعطَّل"}</strong>
                {" · "}
                التفعيل: <strong>{checklist?.context?.enabled_currently ? "مُفعَّل" : "مُعطَّل"}</strong>
                {checklist?.context?.would_fail_count > 0 && (
                  <span className="text-rose-700">
                    {" · "}قد يفشل {checklist.context.would_fail_count} طلب لو بدأنا الآن
                  </span>
                )}
              </p>
            </div>
            <button
              type="button"
              onClick={onActivate}
              disabled={!allPassed || !identityConfirmed || activating || isLiveAlready}
              data-testid="btn-activate-production"
              className={`px-5 py-2.5 rounded-lg font-extrabold text-sm transition
                          ${allPassed && identityConfirmed && !isLiveAlready
                            ? "bg-emerald-600 hover:bg-emerald-700 text-white"
                            : "bg-slate-200 text-slate-500 cursor-not-allowed"}`}>
              {activating ? "جاري التفعيل…" :
               isLiveAlready ? "مُفعَّل ✓" :
               !identityConfirmed ? "🔒 يلزم تأكيد الهوية" :
               "🚀 تفعيل وضع الإنتاج"}
            </button>
          </div>
        </div>
      )}

      {/* ── Identity Diagnostics ─────────────────────────────────── */}
      <section className="rounded-xl border-2 border-amber-300 bg-amber-50/50 p-4 md:p-5 mb-5"
               data-testid="qoyod-identity-diagnostics-card">
        <header className="mb-3 flex items-center justify-between gap-2 flex-wrap">
          <div>
            <h3 className="text-base font-extrabold text-amber-900">
              🔍 تشخيص هوية حساب قيود (إلزامي قبل التفعيل)
            </h3>
            <p className="text-xs text-amber-800 mt-1 leading-relaxed">
              تأكّد أن المفتاح المستخدم في ميزان مربوط بنفس حساب قيود الذي تراه في الواجهة.
              قارن العيّنات أدناه مع ما يظهر لديك في قيود — إذا لم تتطابق، أوقف التفعيل واستبدل المفتاح.
            </p>
          </div>
          <button
            type="button"
            onClick={runIdentityDiagnostics}
            disabled={diagLoading}
            data-testid="btn-run-identity-diag"
            className="px-3 py-2 text-xs font-extrabold rounded-lg bg-amber-700 text-white
                       hover:bg-amber-800 disabled:opacity-50 whitespace-nowrap">
            {diagLoading ? "جاري الاستعلام…" : "▶ تشغيل التشخيص الآن"}
          </button>
        </header>

        {!diag ? (
          <p className="text-sm text-amber-800 bg-amber-100 rounded p-3">
            لم يتم التشخيص بعد. اضغط الزر أعلاه لاستعلام Qoyod مباشرة وعرض عيّنة من المنتجات والعملاء.
          </p>
        ) : diag.summary === "no_api_key" ? (
          <div className="text-sm text-rose-800 bg-rose-50 border border-rose-200 rounded p-3"
               data-testid="diag-no-api-key">
            ⛔ {diag.next_step}
          </div>
        ) : (
          <div className="space-y-3">
            {/* Mezan-side */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-[12px]">
              <div className="bg-white border border-slate-200 rounded p-2"
                   data-testid="diag-mezan-base-url">
                <div className="text-[10px] font-bold text-slate-500">Base URL</div>
                <div className="font-mono break-all" dir="ltr">{diag.mezan?.base_url}</div>
              </div>
              <div className="bg-white border border-slate-200 rounded p-2"
                   data-testid="diag-mezan-fingerprint">
                <div className="text-[10px] font-bold text-slate-500">API Key Fingerprint</div>
                <div className="font-mono" dir="ltr">{diag.mezan?.api_key_fingerprint || "—"}</div>
              </div>
              <div className="bg-white border border-slate-200 rounded p-2"
                   data-testid="diag-queried-at">
                <div className="text-[10px] font-bold text-slate-500">آخر تشخيص</div>
                <div className="font-mono text-[11px]" dir="ltr">
                  {diag.mezan?.queried_at?.slice(0, 19)} UTC
                </div>
              </div>
            </div>

            {/* Tenant hints */}
            {diag.qoyod?.tenant_hints && Object.keys(diag.qoyod.tenant_hints).length > 0 && (
              <div className="bg-white border border-emerald-200 rounded p-2"
                   data-testid="diag-tenant-hints">
                <div className="text-[11px] font-extrabold text-emerald-800 mb-1">
                  معلومات الحساب من /branches:
                </div>
                <pre className="text-[11px] font-mono whitespace-pre-wrap" dir="ltr">
                  {JSON.stringify(diag.qoyod.tenant_hints, null, 2)}
                </pre>
              </div>
            )}

            {/* Products sample */}
            <div className="bg-white border border-slate-200 rounded p-2"
                 data-testid="diag-products">
              <div className="text-[11px] font-extrabold text-slate-700 mb-1 flex items-center justify-between">
                <span>أول 5 منتجات من قيود</span>
                <span className="font-mono text-slate-500" dir="ltr">
                  {diag.qoyod?.products?.endpoint}
                </span>
              </div>
              {diag.qoyod?.products?.ok ? (
                <>
                  <div className="text-[11px] text-slate-600 mb-1">
                    إجمالي حسب Qoyod: <strong>{diag.qoyod.products.meta?.total ?? "?"}</strong>
                  </div>
                  {diag.qoyod.products.sample?.length > 0 ? (
                    <table className="w-full text-[11px]" data-testid="diag-products-table">
                      <thead className="bg-slate-50">
                        <tr>
                          <th className="text-right p-1">ID</th>
                          <th className="text-right p-1">الاسم</th>
                          <th className="text-right p-1">SKU</th>
                          <th className="text-right p-1">النوع</th>
                          <th className="text-right p-1">الحالة</th>
                          <th className="text-right p-1">مؤرشف</th>
                        </tr>
                      </thead>
                      <tbody>
                        {diag.qoyod.products.sample.map((p, i) => (
                          <tr key={i} className={`border-t border-slate-100 ${
                                p.is_system ? "bg-slate-50" : ""}`}
                              data-testid={`diag-product-row-${i}`}>
                            <td className="p-1 font-mono" dir="ltr">{p.id}</td>
                            <td className="p-1">
                              {p.name ? (
                                <span>
                                  {p.name}
                                  {p.name_source && p.name_source !== "name" && (
                                    <span className="ms-1 text-[9px] text-slate-500"
                                          dir="ltr">({p.name_source})</span>
                                  )}
                                </span>
                              ) : (
                                <span className="text-slate-400 italic">— (لا يوجد اسم في أي حقل)</span>
                              )}
                              {p.is_system && (
                                <span
                                  data-testid={`diag-product-system-badge-${i}`}
                                  title="منتج نظامي/افتراضي ينشئه Qoyod أو موصِّل خارجي تلقائياً"
                                  className="ms-1 inline-block px-1 py-0.5 rounded
                                             bg-slate-200 text-slate-700 text-[9px] font-extrabold">
                                  نظامي
                                </span>
                              )}
                            </td>
                            <td className="p-1 font-mono" dir="ltr">{p.sku || "—"}</td>
                            <td className="p-1">{p.type || "—"}</td>
                            <td className="p-1">
                              {p.active === false ? "غير نشط"
                                : p.status || (p.active === true ? "نشط" : "—")}
                            </td>
                            <td className="p-1">
                              {p.archived === true || p.archived_at
                                ? <span className="text-rose-700 font-bold">نعم</span>
                                : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="text-[11px] text-slate-500 italic">لا توجد منتجات</div>
                  )}
                  {diag.qoyod.products.raw_rows?.length > 0 && (
                    <details open className="mt-2 text-[11px]"
                             data-testid="diag-products-raw">
                      <summary className="cursor-pointer font-bold text-slate-700">
                        🔎 عرض JSON الكامل لـ {diag.qoyod.products.raw_rows.length} منتجات من Qoyod
                      </summary>
                      <pre className="mt-1 p-2 bg-slate-900 text-slate-100 rounded font-mono whitespace-pre-wrap text-[10px] max-h-96 overflow-auto" dir="ltr">
                        {JSON.stringify(diag.qoyod.products.raw_rows, null, 2)}
                      </pre>
                    </details>
                  )}
                </>
              ) : (
                <div className="text-[11px] text-rose-700">
                  ✗ {diag.qoyod?.products?.error?.code} —
                  {" "}{diag.qoyod?.products?.error?.message}
                </div>
              )}
            </div>

            {/* Customers sample */}
            <div className="bg-white border border-slate-200 rounded p-2"
                 data-testid="diag-customers">
              <div className="text-[11px] font-extrabold text-slate-700 mb-1 flex items-center justify-between">
                <span>أول 5 عملاء من قيود</span>
                <span className="font-mono text-slate-500" dir="ltr">
                  {diag.qoyod?.customers?.endpoint}
                </span>
              </div>
              {diag.qoyod?.customers?.ok ? (
                <>
                  <div className="text-[11px] text-slate-600 mb-1">
                    إجمالي حسب Qoyod: <strong>{diag.qoyod.customers.meta?.total ?? "?"}</strong>
                  </div>
                  {diag.qoyod.customers.sample?.length > 0 ? (
                    <table className="w-full text-[11px]" data-testid="diag-customers-table">
                      <thead className="bg-slate-50">
                        <tr>
                          <th className="text-right p-1">ID</th>
                          <th className="text-right p-1">الاسم</th>
                          <th className="text-right p-1">الهاتف</th>
                          <th className="text-right p-1">النوع</th>
                          <th className="text-right p-1">مؤرشف</th>
                        </tr>
                      </thead>
                      <tbody>
                        {diag.qoyod.customers.sample.map((c, i) => (
                          <tr key={i} className="border-t border-slate-100">
                            <td className="p-1 font-mono" dir="ltr">{c.id}</td>
                            <td className="p-1">{c.name || "—"}</td>
                            <td className="p-1 font-mono" dir="ltr">{c.phone || "—"}</td>
                            <td className="p-1">{c.type || "—"}</td>
                            <td className="p-1">
                              {c.archived === true
                                ? <span className="text-rose-700 font-bold">نعم</span>
                                : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="text-[11px] text-slate-500 italic">لا توجد عملاء</div>
                  )}
                  {diag.qoyod.customers.raw_rows?.length > 0 && (
                    <details open className="mt-2 text-[11px]"
                             data-testid="diag-customers-raw">
                      <summary className="cursor-pointer font-bold text-slate-700">
                        🔎 عرض JSON الكامل لـ {diag.qoyod.customers.raw_rows.length} عملاء من Qoyod
                      </summary>
                      <pre className="mt-1 p-2 bg-slate-900 text-slate-100 rounded font-mono whitespace-pre-wrap text-[10px] max-h-96 overflow-auto" dir="ltr">
                        {JSON.stringify(diag.qoyod.customers.raw_rows, null, 2)}
                      </pre>
                    </details>
                  )}
                </>
              ) : (
                <div className="text-[11px] text-rose-700">
                  ✗ {diag.qoyod?.customers?.error?.code} —
                  {" "}{diag.qoyod?.customers?.error?.message}
                </div>
              )}
            </div>

            {/* Operator confirmation */}
            <label className="flex items-start gap-2 mt-3 p-2 bg-amber-100 rounded border border-amber-300 cursor-pointer">
              <input
                type="checkbox"
                checked={identityConfirmed}
                onChange={(e) => setIdentityConfirmed(e.target.checked)}
                data-testid="diag-confirm-identity"
                className="mt-1"
              />
              <span className="text-[12.5px] font-bold text-amber-900">
                ✅ أؤكد أن المنتجات والعملاء أعلاه تطابق ما أراه في واجهة قيود
                لحسابي. (إلزامي قبل تفعيل الإنتاج)
              </span>
            </label>
          </div>
        )}
      </section>

      {/* ── Quantitative report ──────────────────────────────────── */}
      {!loadingReport && report && (
        <section className="rounded-xl border border-slate-200 bg-white p-4 md:p-5 mb-5"
                 data-testid="go-live-report-card">
          <header className="mb-3">
            <h3 className="text-base font-extrabold text-slate-800">
              📊 تقرير ما قبل البدء — الأرقام التي تهمّك
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              صورة كاملة لما سيحدث لحظة تفعيل الإنتاج.
            </p>
          </header>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCell label="طلبات مؤهلة للإرسال"
                      value={report.eligible_orders_count}
                      tone={report.eligible_orders_count ? "blue" : "amber"}
                      testid="go-stat-eligible-orders" />
            <StatCell label="منتجات ستُنشأ في قيود"
                      value={report.products_needing_creation}
                      tone={report.products_needing_creation ? "amber" : "emerald"}
                      testid="go-stat-products-create" />
            <StatCell label="منتجات مربوطة محلياً"
                      value={report.products_already_in_qoyod}
                      sub={report.qoyod_products_total != null
                           ? `إجمالي قيود: ${report.qoyod_products_total}` : null}
                      tone="emerald"
                      testid="go-stat-products-mapped" />
            <StatCell label="عملاء سيُنشَؤون"
                      value={report.customers_needing_creation}
                      tone={report.customers_needing_creation ? "amber" : "emerald"}
                      testid="go-stat-customers-create" />
            <StatCell label="عملاء مربوطون محلياً"
                      value={report.customers_already_local}
                      sub={report.qoyod_contacts_total != null
                           ? `إجمالي قيود: ${report.qoyod_contacts_total}` : null}
                      tone="emerald"
                      testid="go-stat-customers-mapped" />
            <StatCell label="طرق دفع غير مربوطة"
                      value={report.unmapped_payment_methods_count}
                      sub={report.unmapped_payment_methods?.join(", ") || null}
                      tone={report.unmapped_payment_methods_count ? "rose" : "emerald"}
                      testid="go-stat-unmapped-payments" />
            <StatCell label="طلبات ستفشل لو بدأنا الآن"
                      value={report.would_fail_if_live_now}
                      tone={report.would_fail_if_live_now ? "rose" : "emerald"}
                      testid="go-stat-would-fail" />
            <StatCell label="Dry Run الحالي"
                      value={report.dry_run_mode_currently_on ? "مُفعَّل" : "مُعطَّل"}
                      tone={report.dry_run_mode_currently_on ? "amber" : "emerald"}
                      testid="go-stat-dry-run" />
          </div>
        </section>
      )}

      {/* ── Checklist ─────────────────────────────────────────────── */}
      <section className="rounded-xl border border-slate-200 bg-white p-4 md:p-5 mb-5"
               data-testid="go-live-checklist-card">
        <header className="mb-3 flex items-center justify-between gap-2 flex-wrap">
          <div>
            <h3 className="text-base font-extrabold text-slate-800">
              📋 قائمة التحقق (Checklist)
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              كل بند فيها يجب أن يكون ✓ قبل السماح بالتفعيل.
            </p>
          </div>
          {!loadingChecklist && checklist && (
            <span className={`text-sm font-extrabold px-3 py-1.5 rounded-full
                            ${allPassed ? "bg-emerald-100 text-emerald-800"
                                       : "bg-rose-100 text-rose-800"}`}>
              {checklist.totals.passed} / {checklist.totals.checks} اجتاز
            </span>
          )}
        </header>
        {loadingChecklist ? (
          <p className="text-sm text-slate-500">جاري التحقق…</p>
        ) : (
          <ul className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {(checklist?.items || []).map((it) => (
              <ChecklistRow key={it.key} item={it}
                            onRequeue={onAutoRequeue}
                            requeueing={requeueing} />
            ))}
          </ul>
        )}
      </section>

      {/* ── Quick links ──────────────────────────────────────────── */}
      <div className="text-xs text-slate-500 flex items-center gap-2 flex-wrap"
           data-testid="go-live-quick-links">
        <span>الصفحات ذات الصلة:</span>
        <Link to="/integrations/qoyod/settings"
              className="text-blue-600 hover:underline font-bold"
              data-testid="link-qoyod-settings">إعدادات قيود</Link>
        <span>·</span>
        <Link to="/integrations/qoyod/invoices"
              className="text-blue-600 hover:underline font-bold"
              data-testid="link-qoyod-invoices">فواتير قيود — مراقبة</Link>
        <span>·</span>
        <button type="button" onClick={loadAll}
                className="text-blue-600 hover:underline font-bold"
                data-testid="btn-refresh-checklist">↻ إعادة التحقق</button>
      </div>
    </div>
  );
}
