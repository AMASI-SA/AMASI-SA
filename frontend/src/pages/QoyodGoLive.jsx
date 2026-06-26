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

function ChecklistRow({ item }) {
  const ok = item.ok;
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

  useEffect(() => { loadAll(); }, []); // eslint-disable-line

  const onActivate = async () => {
    if (!checklist?.all_passed) {
      toast.warning("لا يمكن التفعيل — هناك بنود لم تجتز التحقق بعد.");
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
              disabled={!allPassed || activating || isLiveAlready}
              data-testid="btn-activate-production"
              className={`px-5 py-2.5 rounded-lg font-extrabold text-sm transition
                          ${allPassed && !isLiveAlready
                            ? "bg-emerald-600 hover:bg-emerald-700 text-white"
                            : "bg-slate-200 text-slate-500 cursor-not-allowed"}`}>
              {activating ? "جاري التفعيل…" :
               isLiveAlready ? "مُفعَّل ✓" :
               "🚀 تفعيل وضع الإنتاج"}
            </button>
          </div>
        </div>
      )}

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
              <ChecklistRow key={it.key} item={it} />
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
